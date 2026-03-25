"""
AI Review Service - PDF extraction, chunking, and Ollama API integration.

This service handles:
1. PDF text extraction using PyMuPDF
2. Section-aware chunking (preserves heading + content)
3. Calling Ollama API for review
4. Error handling and logging
"""

import hashlib
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import bleach
import fitz  # PyMuPDF
import requests

logger = logging.getLogger(__name__)

# Ollama configuration - MUST use /v1/chat/completions (OpenAI-compatible API)
# The /api/chat endpoint returns streaming NDJSON which is incompatible with this code
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://10.51.5.169:11434/v1/chat/completions")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))  # seconds (5 minutes)
OLLAMA_MAX_RETRIES = int(os.getenv("OLLAMA_MAX_RETRIES", "3"))
OLLAMA_MAX_CONCURRENT = int(os.getenv("OLLAMA_MAX_CONCURRENT", "10"))  # Max concurrent requests

# Sections to skip - these are trivial or cause false positives
SKIP_SECTIONS = [
    'references',
    'acknowledgments',
    'acknowledgements',
    'bibliography',
    'appendix',
    'supplementary materials',
    'supplementary information',
]

# Review prompt template
REVIEW_PROMPT_TEMPLATE = """You are a peer reviewer examining a scientific paper.
Review ONLY this section and provide constructive feedback.

Focus on:
- Clarity and readability of scientific writing
- Factual accuracy and logical consistency
- Methodology soundness (if applicable)
- Potential improvements

IMPORTANT - STRICT RULES:
- Respond with DIRECT feedback ONLY, no preamble or explanation
- Be concise: 2-3 sentences maximum
- If no issues found, respond with exactly: "No issues found"
- Do NOT include any thinking, reasoning, or analysis process in your response
- Do NOT write things like "Let me analyze" or "Looking at this section" 
- Do NOT claim to have internet access
- Your response must be ONLY the feedback itself

SECTION: {section_title}
---
{sanitized_content}
---

Your feedback:"""


@dataclass
class PDFSection:
    """Represents a section of a PDF document."""
    index: int
    title: str
    content: str
    content_hash: str
    page_start: int
    page_end: int


@dataclass
class SectionReview:
    """Result of reviewing a section."""
    section_index: int
    review: str
    success: bool
    error: Optional[str] = None


def extract_text_from_pdf(pdf_path: str) -> list[fitz.Page]:
    """
    Extract all pages from a PDF as text.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        List of PyMuPDF Page objects with text content
    """
    doc = fitz.open(pdf_path)
    pages = []
    
    for page_num in range(len(doc)):
        pages.append(doc[page_num])
    
    doc.close()
    return pages


def chunk_pdf_by_sections(pdf_path: str, max_chunk_tokens: int = 2000) -> list[PDFSection]:
    """
    Extract text from PDF and chunk into sections based on headings.
    
    Uses section-aware chunking: combines each heading with its associated
    paragraphs to preserve document structure.
    
    Args:
        pdf_path: Path to the PDF file
        max_chunk_tokens: Maximum tokens per chunk (approximate)
        
    Returns:
        List of PDFSection objects. Empty list if PDF cannot be read.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logger.error(f"[PDF ERROR] Could not open PDF file: {pdf_path} - {e}")
        return []
    
    sections = []
    
    # Patterns for detecting headings (various formats)
    heading_patterns = [
        r'^(Abstract|Introduction|Background|Related Work|Methodology|Methods?|Experiment|Results?|Discussion|Limitations?|Conclusion|Future Work|References?|Acknowledgments?|Appendix)\s*$',
        r'^(#+\s+.+)$',  # Markdown-style headers
        r'^(\d+\.\s+[A-Z][^\n]+)$',  # Numbered sections like "1. Introduction"
        r'^(\d+\.\d+\s+[^\n]+)$',  # Numbered subsections like "1.1 Background"
    ]
    
    current_title = "Introduction"
    current_content = []
    current_page_start = 1
    section_index = 0
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        
        if not text.strip():
            continue
            
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            is_heading = False
            matched_title = None
            
            # Check if line is a heading
            for pattern in heading_patterns:
                if re.match(pattern, line, re.IGNORECASE | re.MULTILINE):
                    is_heading = True
                    matched_title = line.strip('#').strip()
                    break
            
            # If we hit a new section and have content
            if is_heading and (current_content or section_index == 0):
                # Save previous section
                content_text = '\n'.join(current_content)
                if content_text.strip():
                    content_hash = hashlib.sha256(content_text.encode()).hexdigest()[:16]
                    sections.append(PDFSection(
                        index=section_index,
                        title=current_title,
                        content=content_text,
                        content_hash=content_hash,
                        page_start=current_page_start,
                        page_end=page_num + 1
                    ))
                    section_index += 1
                
                # Start new section
                current_title = matched_title or line
                current_content = []
                current_page_start = page_num + 1
            else:
                current_content.append(line)
    
    # Don't forget the last section
    content_text = '\n'.join(current_content)
    if content_text.strip():
        content_hash = hashlib.sha256(content_text.encode()).hexdigest()[:16]
        sections.append(PDFSection(
            index=section_index,
            title=current_title,
            content=content_text,
            content_hash=content_hash,
            page_start=current_page_start,
            page_end=len(doc)
        ))
    
    doc.close()
    
    # If we only got one section (no headings detected), split by pages
    if len(sections) == 1 and len(doc) > 1:
        sections = _chunk_by_pages(pdf_path, max_chunk_tokens)
    
    # Log if no meaningful content was extracted
    if not sections:
        logger.warning(f"[PDF WARNING] No sections extracted from PDF: {pdf_path} (file may be scanned or contain no extractable text)")
    
    logger.info(f"[PDF INFO] Chunked PDF into {len(sections)} sections: {pdf_path}")
    return sections


def _chunk_by_pages(pdf_path: str, max_chunk_tokens: int = 2000) -> list[PDFSection]:
    """
    Fallback: chunk PDF by pages when no headings are detected.
    Groups multiple pages into chunks based on token limit.
    
    Returns empty list if PDF cannot be read.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logger.error(f"[PDF ERROR] Could not open PDF for page chunking: {pdf_path} - {e}")
        return []
    
    sections = []
    
    current_content = []
    current_pages = []
    current_title = "Page 1"
    section_index = 0
    
    tokens_per_page = 400  # Rough estimate
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()
        
        if not text:
            continue
            
        current_content.append(f"[Page {page_num + 1}]\n{text}")
        current_pages.append(page_num + 1)
        
        # Check if we've hit the token limit
        if len(current_content) * tokens_per_page >= max_chunk_tokens:
            content_text = '\n'.join(current_content)
            content_hash = hashlib.sha256(content_text.encode()).hexdigest()[:16]
            
            sections.append(PDFSection(
                index=section_index,
                title=current_title,
                content=content_text,
                content_hash=content_hash,
                page_start=min(current_pages),
                page_end=max(current_pages)
            ))
            
            section_index += 1
            current_content = []
            current_pages = []
            current_title = f"Pages {page_num + 1}"
    
    # Last section
    if current_content:
        content_text = '\n'.join(current_content)
        content_hash = hashlib.sha256(content_text.encode()).hexdigest()[:16]
        sections.append(PDFSection(
            index=section_index,
            title=current_title,
            content=content_text,
            content_hash=content_hash,
            page_start=min(current_pages) if current_pages else 1,
            page_end=max(current_pages) if current_pages else len(doc)
        ))
    
    doc.close()
    
    if not sections:
        logger.warning(f"[PDF WARNING] No content extracted during page chunking: {pdf_path}")
    
    logger.info(f"[PDF INFO] Chunked PDF by pages into {len(sections)} sections: {pdf_path}")
    return sections


def sanitize_llm_response(text: str) -> str:
    """
    Sanitize LLM response to prevent XSS and other injection attacks.
    
    Uses bleach to whitelist only safe HTML tags and attributes.
    """
    if not text:
        return ""
    
    # Allow minimal formatting but strip dangerous tags
    allowed_tags = ['p', 'br', 'strong', 'em', 'u', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li']
    allowed_attributes = {}
    
    # Clean the text
    cleaned = bleach.clean(
        text,
        tags=allowed_tags,
        attributes=allowed_attributes,
        strip=True
    )
    
    # Also remove any leftover HTML entities that could be exploited
    cleaned = cleaned.replace('&lt;script', '&lt;script')
    cleaned = cleaned.replace('&gt;', '>')
    
    return cleaned.strip()


def sanitize_pdf_text(text: str) -> str:
    """
    Sanitize PDF text before sending to LLM.
    
    Removes artifacts from LaTeX/PDF conversion:
    - Hyphenation at end of lines (LaTeX wraps lines with -)
    - Multiple whitespace
    - Fixes common PDF extraction issues
    
    The LLM can understand text even with minor artifacts, but hyphens
    at end of lines (LaTeX wrapping) can cause false positives.
    """
    if not text:
        return ""
    
    # Step 1: Replace hyphens followed by newlines (and optional whitespace) with space
    # This handles "word-\n" and "word- \n" and "word-\n  " patterns
    text = re.sub(r'-\s*\n\s*', ' ', text)
    
    # Step 2: Handle hyphen at very end of text followed by newline
    text = re.sub(r'-\n', '', text)
    
    # Step 3: Remove multiple newlines (preserve paragraph breaks)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Step 4: Normalize whitespace but preserve paragraph structure
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if line:
            cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)
    
    # Step 5: Final cleanup of any remaining hyphens at end of lines
    text = re.sub(r'-\s*$', '', text, flags=re.MULTILINE)
    
    return text.strip()


def call_ollama(section: PDFSection, endpoint: str = None, model: str = None,
                timeout: int = None, max_retries: int = None) -> SectionReview:
    """
    Call Ollama API to review a single PDF section.
    
    Args:
        section: The PDFSection to review
        endpoint: Ollama API endpoint
        model: Model name to use
        timeout: Request timeout in seconds
        max_retries: Number of retry attempts
        
    Returns:
        SectionReview with the result
    """
    endpoint = endpoint or OLLAMA_ENDPOINT
    model = model or OLLAMA_MODEL
    timeout = timeout or OLLAMA_TIMEOUT
    
    # Debug: log the actual timeout being used
    logger.info(f"[DEBUG TIMEOUT] OLLAMA_ENDPOINT={OLLAMA_ENDPOINT}, OLLAMA_TIMEOUT={OLLAMA_TIMEOUT}, "
                f"using timeout={timeout}s, endpoint={endpoint}, model={model}")
    max_retries = max_retries or OLLAMA_MAX_RETRIES
    
    # Sanitize content: remove LaTeX hyphens and PDF artifacts
    sanitized_content = sanitize_pdf_text(section.content[:4000])
    
    prompt = REVIEW_PROMPT_TEMPLATE.format(
        section_title=section.title,
        sanitized_content=sanitized_content
    )
    
    # Log the request details
    logger.info(f"[OLLAMA REQUEST] Section {section.index}: '{section.title}' "
                f"(hash={section.content_hash}, pages={section.page_start}-{section.page_end}, "
                f"endpoint={endpoint}, model={model})")
    
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
        "temperature": 0.3,
        "stream": False  # Must be explicitly False to get single JSON response
    }
    
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            
            logger.debug(f"[OLLAMA ATTEMPT {attempt+1}] Posting to {endpoint}")
            
            response = requests.post(
                endpoint,
                json=payload,
                timeout=(30, timeout),  # 30s connect, variable read timeout
                headers={"Content-Type": "application/json"}
            )
            
            elapsed = time.time() - start_time
            
            # Log response status always
            logger.info(f"[OLLAMA RESPONSE] Section {section.index}: "
                       f"status={response.status_code}, elapsed={elapsed:.1f}s, "
                       f"content_length={len(response.content)} bytes")
            
            if response.status_code == 200:
                result = response.json()
                logger.debug(f"[OLLAMA JSON] Section {section.index}: {result}")
                
                # Handle both OpenAI-compatible and Ollama native response formats
                #
                # OpenAI-compatible format:
                #   {"choices": [{"message": {"content": "..."}}]}
                #
                # Ollama native format (/api/chat):
                #   {"message": {"role": "assistant", "content": "..."}}
                #
                
                review_text = ""
                
                # Try OpenAI-compatible format first
                choices = result.get("choices", [])
                if choices and isinstance(choices, list):
                    message = choices[0].get("message", {})
                    review_text = message.get("content", "").strip()
                    # Also check for Ollama extended thinking in reasoning field
                    if not review_text:
                        reasoning = message.get("reasoning", {})
                        if isinstance(reasoning, dict):
                            reasoning = reasoning.get("summary", [""])[0] if reasoning.get("summary") else ""
                        review_text = str(reasoning).strip()
                
                # Fallback to Ollama native format
                if not review_text:
                    ollama_message = result.get("message", {})
                    review_text = ollama_message.get("content", "").strip()
                    # Ollama extended thinking might be in 'thinking' field
                    if not review_text:
                        thinking = ollama_message.get("thinking")
                        if thinking:
                            review_text = str(thinking).strip()
                
                if review_text:
                    # Sanitize before returning
                    sanitized = sanitize_llm_response(review_text)
                    logger.info(f"[OLLAMA SUCCESS] Section {section.index} reviewed in {elapsed:.1f}s")
                    return SectionReview(
                        section_index=section.index,
                        review=sanitized,
                        success=True
                    )
                else:
                    logger.warning(f"[OLLAMA EMPTY] Section {section.index} returned empty response. "
                                  f"Full response: {result}")
                    # Don't retry empty responses - fail silently
                    return SectionReview(
                        section_index=section.index,
                        review="",
                        success=False,
                        error="Empty response from Ollama"
                    )
            elif response.status_code in (502, 503, 504):
                # Gateway errors - retry
                logger.warning(f"[OLLAMA GATEWAY ERROR] Section {section.index}: "
                             f"HTTP {response.status_code}: {response.text[:500]}")
            elif response.status_code == 400:
                # Bad request - don't retry, fail silently
                logger.error(f"[OLLAMA BAD REQUEST] Section {section.index}: "
                           f"HTTP 400: {response.text[:500]}")
                break
            elif response.status_code == 404:
                logger.error(f"[OLLAMA NOT FOUND] Section {section.index}: "
                           f"Endpoint not found: {endpoint}")
                break
            elif response.status_code == 500:
                # Server error - retry
                logger.warning(f"[OLLAMA SERVER ERROR] Section {section.index}: "
                             f"HTTP 500: {response.text[:500]}")
            else:
                logger.warning(f"[OLLAMA HTTP ERROR] Section {section.index}: "
                             f"HTTP {response.status_code}: {response.text[:200]}")
                
        except requests.exceptions.Timeout:
            logger.warning(f"[OLLAMA TIMEOUT] Section {section.index} attempt {attempt + 1}/{max_retries} "
                          f"after {timeout}s (endpoint={endpoint}, model={model})")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[OLLAMA CONNECTION ERROR] Section {section.index}: "
                        f"Ollama server unreachable: {e} (endpoint={endpoint}, model={model})")
            # Don't retry on connection error - Ollama is down, fail silently
            break
        except requests.exceptions.HTTPError as e:
            logger.error(f"[OLLAMA HTTP ERROR] Section {section.index}: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"[OLLAMA JSON ERROR] Section {section.index}: "
                        f"Invalid JSON response: {e}")
        except Exception as e:
            logger.error(f"[OLLAMA ERROR] Section {section.index} attempt {attempt + 1}: {e}")
        
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt
            logger.info(f"[OLLAMA RETRY] Section {section.index}: waiting {wait_time}s before retry")
            time.sleep(wait_time)  # Exponential backoff
    
    # All retries failed - fail silently with logging
    error_msg = f"Ollama request failed after {max_retries} attempts"
    logger.error(f"[OLLAMA FAILED] Section {section.index}: {error_msg}")
    return SectionReview(
        section_index=section.index,
        review="",
        success=False,
        error=error_msg
    )


def filter_trivial_sections(sections: list[PDFSection]) -> list[PDFSection]:
    """
    Filter out trivial sections that LLMs commonly give false positives on
    or that don't contain meaningful content to review.
    """
    filtered = []
    for section in sections:
        title_lower = section.title.lower().strip()
        
        # Skip known trivial sections
        if title_lower in SKIP_SECTIONS:
            logger.info(f"[SECTION FILTER] Skipping trivial section: '{section.title}'")
            continue
        
        # Skip sections with very little content (likely just a heading)
        if len(section.content.strip()) < 100:
            logger.info(f"[SECTION FILTER] Skipping short section: '{section.title}' ({len(section.content)} chars)")
            continue
        
        # Skip "References" appearing anywhere in title
        if 'references' in title_lower:
            logger.info(f"[SECTION FILTER] Skipping section with 'references': '{section.title}'")
            continue
        
        filtered.append(section)
    
    if len(filtered) < len(sections):
        logger.info(f"[SECTION FILTER] Filtered {len(sections)} sections down to {len(filtered)} meaningful ones")
    
    return filtered


def process_pdf_sections(sections: list[PDFSection], job_id: str = None, 
                          ticket_id: int = None, db_session = None) -> list[SectionReview]:
    """
    Process multiple PDF sections concurrently using Ollama.
    
    Limits concurrent requests to OLLAMA_MAX_CONCURRENT.
    
    Handles edge cases:
    - Empty sections list
    - Ollama connection failures
    - Ticket deleted mid-review
    - Trivial sections (References, etc.)
    
    Args:
        sections: List of PDFSection objects to review
        job_id: Optional job ID for logging
        ticket_id: Optional ticket ID to check if ticket still exists
        db_session: Optional database session to check ticket existence
        
    Returns:
        List of SectionReview results (in order)
    """
    if not sections:
        logger.warning(f"[AI REVIEW] No sections to process for job {job_id}")
        return []
    
    # Filter out trivial sections (References, Acknowledgments, etc.)
    sections = filter_trivial_sections(sections)
    
    if not sections:
        logger.warning(f"[AI REVIEW] All sections filtered as trivial for job {job_id}")
        return []
    
    logger.info(f"[AI REVIEW] Starting processing of {len(sections)} sections for job {job_id}")
    
    results = []
    
    # Check if ticket was deleted before processing
    if ticket_id and db_session:
        from models import Ticket
        ticket_exists = db_session.get(Ticket, ticket_id)
        if not ticket_exists:
            logger.warning(f"[AI REVIEW] Ticket {ticket_id} was deleted, skipping review for job {job_id}")
            # Return empty results - job will be marked failed in worker
            return []
    
    # Use ThreadPoolExecutor to limit concurrency
    with ThreadPoolExecutor(max_workers=OLLAMA_MAX_CONCURRENT) as executor:
        # Submit all tasks
        future_to_section = {
            executor.submit(call_ollama, section): section
            for section in sections
        }
        
        # Collect results in order
        section_results = {}
        for future in future_to_section:
            section = future_to_section[future]
            try:
                result = future.result()
                section_results[section.index] = result
            except Exception as e:
                logger.error(f"[PROCESSING ERROR] Section {section.index}: {e}")
                section_results[section.index] = SectionReview(
                    section_index=section.index,
                    review="",
                    success=False,
                    error=str(e)
                )
        
        # Return in order
        results = [section_results.get(i, SectionReview(i, "", False, "Missing")) 
                   for i in range(len(sections))]
    
    # Log summary
    success_count = sum(1 for r in results if r.success)
    fail_count = len(results) - success_count
    logger.info(f"[AI REVIEW] Completed processing for job {job_id}: "
                f"{success_count} succeeded, {fail_count} failed")
    
    return results
