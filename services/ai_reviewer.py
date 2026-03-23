"""
AI Review Service - PDF extraction, chunking, and Ollama API integration.

This service handles:
1. PDF text extraction using PyMuPDF
2. Section-aware chunking (preserves heading + content)
3. Calling Ollama API for review
4. Error handling and logging
"""

import hashlib
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

# Ollama configuration - can be overridden by admin settings
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:32b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))  # seconds
OLLAMA_MAX_RETRIES = int(os.getenv("OLLAMA_MAX_RETRIES", "3"))
OLLAMA_MAX_CONCURRENT = int(os.getenv("OLLAMA_MAX_CONCURRENT", "10"))  # Max concurrent requests

# Review prompt template
REVIEW_PROMPT_TEMPLATE = """You are a peer reviewer examining a section of an academic paper.
Review ONLY this section and provide constructive feedback.

Focus on:
- Clarity and readability
- Logical flow and structure
- Potential improvements
- Any issues or concerns

If the section seems incomplete or out of context, note that politely.

SECTION: {section_title}
---
{content}
---

Your review (be concise, 2-4 sentences):"""


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
        List of PDFSection objects
    """
    doc = fitz.open(pdf_path)
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
    
    logger.info(f"Chunked PDF into {len(sections)} sections")
    return sections


def _chunk_by_pages(pdf_path: str, max_chunk_tokens: int = 2000) -> list[PDFSection]:
    """
    Fallback: chunk PDF by pages when no headings are detected.
    Groups multiple pages into chunks based on token limit.
    """
    doc = fitz.open(pdf_path)
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
    logger.info(f"Chunked PDF by pages into {len(sections)} sections")
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
    max_retries = max_retries or OLLAMA_MAX_RETRIES
    
    prompt = REVIEW_PROMPT_TEMPLATE.format(
        section_title=section.title,
        content=section.content[:4000]  # Truncate to prevent token overflow
    )
    
    # Log the request (without full content for privacy)
    truncated_content = section.content[:200] + "..." if len(section.content) > 200 else section.content
    logger.info(f"[OLLAMA REQUEST] Section {section.index}: '{section.title}' "
                f"(hash={section.content_hash}, pages={section.page_start}-{section.page_end}, "
                f"content_len={len(section.content)})")
    
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,  # Lower temp for more consistent output
            "num_predict": 500  # Limit response length
        }
    }
    
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            
            response = requests.post(
                endpoint,
                json=payload,
                timeout=timeout,
                headers={"Content-Type": "application/json"}
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                result = response.json()
                review_text = result.get("response", "").strip()
                
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
                    logger.warning(f"[OLLAMA EMPTY] Section {section.index} returned empty response")
            else:
                logger.warning(f"[OLLAMA ERROR] Section {section.index} HTTP {response.status_code}: "
                             f"{response.text[:200]}")
                
        except requests.exceptions.Timeout:
            logger.warning(f"[OLLAMA TIMEOUT] Section {section.index} attempt {attempt + 1}/{max_retries} "
                          f"after {timeout}s (endpoint={endpoint}, model={model})")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[OLLAMA CONNECTION ERROR] Section {section.index}: {e} "
                        f"(endpoint={endpoint}, model={model})")
            # Don't retry on connection error - Ollama is probably down
            break
        except Exception as e:
            logger.error(f"[OLLAMA ERROR] Section {section.index} attempt {attempt + 1}: {e}")
        
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # Exponential backoff
    
    # All retries failed
    return SectionReview(
        section_index=section.index,
        review="",
        success=False,
        error=f"Ollama request failed after {max_retries} attempts"
    )


def process_pdf_sections(sections: list[PDFSection], job_id: str = None) -> list[SectionReview]:
    """
    Process multiple PDF sections concurrently using Ollama.
    
    Limits concurrent requests to OLLAMA_MAX_CONCURRENT.
    
    Args:
        sections: List of PDFSection objects to review
        job_id: Optional job ID for logging
        
    Returns:
        List of SectionReview results (in order)
    """
    results = []
    
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
    
    return results
