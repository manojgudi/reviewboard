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

# Review prompt template - MUST give specific, line-referenced feedback
REVIEW_PROMPT_TEMPLATE = """You are a CRITICAL peer reviewer. Your feedback MUST be SPECIFIC and ACTIONABLE.

ABSOLUTE RULES:
- You MUST quote specific sentences/paragraphs from the text (use "..." around quotes)
- You MUST cite line numbers or page references when possible
- NEVER give generic feedback like "inconsistent formatting" or "missing citations" without specifying WHERE
- NEVER say things like "clarify the setup" without explaining WHAT is unclear and WHY
- If you find an issue, explain exactly what the problem is with a concrete example from the text
- If you can't find a specific issue, say "No specific issues found - section appears sound"
- Output ONLY the feedback, nothing else

FORMAT: After any thinking, you MUST put your final feedback AFTER the word "FEEDBACK:" on its own line. The content field (after FEEDBACK:) is what gets shown to users. Example:
FEEDBACK: Line 3: "the model was trained" is vague - what training data? hyperparameters? size?

WRONG (generic):
"The section has inconsistent formatting and unclear methodology"
"Figure references need improvement"

RIGHT (specific):
"Line 3: 'the model was trained' is vague - what training data? hyperparameters? size?"
"Paragraph 2: The acronym 'NLP' is used without first defining it"

SECTION: {section_title}
---
{sanitized_content}
---

FEEDBACK:"""


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
    
    STRICT HEADING DETECTION - Only recognizes main section headings:
    - Roman numerals at top level: "I. Introduction", "II. Methods"
    - Arabic numerals at top level: "1. Introduction", "2. Methods"
    - Known section titles: "Abstract", "Introduction", "Methods", etc.
    - Markdown headers: "# Heading"
    
    Does NOT treat subsections (1.1, 1.2.3) as separate review sections.
    Does NOT treat figure/table captions (TABLE 1, Figure 2) as sections.
    Filters out References, Acknowledgments, Appendix.
    
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
    
    # ============================================================
    # STRICT HEADING DETECTION - Only top-level sections
    # ============================================================
    
    # Roman numeral pattern: I, II, III, IV, V... XI, XII, etc.
    # Uses non-capturing groups internally so outer groups work correctly
    # Must have period after numeral AND be followed by space + title
    ROMAN_NUMERAL = r'M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\.'
    
    # Known main section titles (must be standalone, not as part of sentences)
    MAIN_SECTION_TERMS = frozenset([
        'abstract', 'introduction', 'background', 'related work', 'related studies',
        'methodology', 'methods', 'method', 'experimental setup', 'experiments',
        'experiment', 'results', 'discussion', 'findings',
        'conclusion', 'conclusions', 'future work', 'limitations', 'threats to validity',
        'case study', 'motivation', 'problem statement', 'contributions',
        'results and discussion',  # Common combined section
    ])
    
    # Terms that indicate non-content sections (skip these entirely)
    SKIP_TERMS = frozenset([
        'references', 'acknowledgments', 'acknowledgements', 'bibliography',
        'appendix', 'appendices', 'supplementary materials', 'supplementary information'
    ])
    
    def is_strict_heading(line: str) -> tuple[bool, str, bool]:
        """
        Check if a line is a STRICT main section heading.
        
        Returns:
            (is_heading, normalized_title, should_skip)
            - is_heading: True if this is a main section
            - normalized_title: The title to use
            - should_skip: True if this is References/Appendix (skip entirely)
        """
        line = line.strip()
        
        # Skip empty or very short/long lines
        if len(line) < 3 or len(line) > 80:
            return False, "", False
        
        # Pattern 1: Markdown headers (# Heading)
        if line.startswith('#'):
            title = line.lstrip('#').strip()
            title_lower = title.lower()
            if title_lower in SKIP_TERMS:
                return True, title, True
            return True, title, False
        
        # Pattern 2: Roman numeral headings: "I. Introduction", "XI. Conclusion"
        # MUST have period AND space after numeral, followed by text
        # Filters out subsection markers like "C. Some subsection"
        roman_match = re.match(rf'^({ROMAN_NUMERAL})\s+(.+)$', line, re.IGNORECASE)
        if roman_match:
            numeral_str = roman_match.group(1)[:-1].upper()  # Remove trailing period
            title = roman_match.group(2).strip()
            title_lower = title.lower()
            
            # Single-letter Roman numerals that ARE valid main sections: I, V, X
            # Single-letter Roman numerals that are likely subsection markers: C, D, L, M
            main_section_singles = {'I', 'V', 'X'}
            if len(numeral_str) == 1 and numeral_str not in main_section_singles:
                # Likely a subsection marker like "C. Cooperative Awareness"
                return False, "", False
            
            if title_lower in SKIP_TERMS:
                return True, line, True
            # Skip if title starts with a sentence word (likely a section reference, not a heading)
            # Common patterns: "VII. However", "Section IV. The", "Table III. Shows", etc.
            sentence_starters = {'however', 'moreover', 'furthermore', 'additionally', 'also', 
                                'therefore', 'thus', 'hence', 'section', 'table', 'figure', 'fig',
                                'chapter', 'appendix', 'references', 'the', 'a', 'an'}
            # Strip punctuation from first word for matching
            first_word_raw = title_lower.split()[0] if title_lower.split() else ''
            first_word = re.sub(r'[^\w]', '', first_word_raw)  # Remove punctuation
            if first_word in sentence_starters:
                return False, "", False
            # Accept if it's a known section term OR title is reasonably short (up to 10 words)
            if title_lower in MAIN_SECTION_TERMS or len(title.split()) <= 10:
                return True, title, False
        
        # Pattern 3: Arabic numeral headings: "1. Introduction", "2. Methods"
        # Only TOP-LEVEL numbers (single digit or multi-digit, but no decimals)
        # Must be: number + period + space + title
        # Filters out: reference numbers like "60000. Using...", "RFC 1234", etc.
        arabic_match = re.match(r'^(\d+)\.\s+(.+)$', line)
        if arabic_match:
            num = int(arabic_match.group(1))
            title = arabic_match.group(2).strip()
            title_lower = title.lower()
            
            # Skip large numbers (likely bibliography references)
            if num > 100:
                return False, "", False
            
            if title_lower in SKIP_TERMS:
                return True, line, True
            # Skip sentence references like "1. However", "2. The"
            first_word_raw = title_lower.split()[0] if title_lower.split() else ''
            first_word = re.sub(r'[^\w]', '', first_word_raw)  # Remove punctuation
            if first_word in {'however', 'moreover', 'furthermore', 'additionally', 'also', 
                             'therefore', 'thus', 'hence', 'section', 'table', 'figure', 'fig',
                             'chapter', 'the', 'a', 'an'}:
                return False, "", False
            # Accept if it's a known section term OR title is reasonably short (up to 10 words)
            if title_lower in MAIN_SECTION_TERMS or len(title.split()) <= 10:
                return True, title, False
        
        # Pattern 4: Known section terms at start of line (no number prefix)
        # Must appear at start of line and be followed by space/punctuation
        # Handles "Abstract—The", "Abstract:", or just "Abstract"
        # Does NOT match: "methods over CORECONF" (full sentence starting with 'methods')
        line_lower = line.lower()
        for term in MAIN_SECTION_TERMS:
            if line_lower.startswith(term):
                rest = line_lower[len(term):]
                # If the line is just the term, accept it
                if not rest or rest.isspace():
                    return True, term.capitalize(), False
                # If followed by space+punctuation+text (like "Abstract—The"), accept it
                # but NOT if followed by a lowercase word (which would be a sentence)
                if rest and rest[0] in ' —:-–—' and len(rest) > 1:
                    # Check the ORIGINAL case (not lowercased) of what follows
                    original_rest = line[len(term):]
                    following = original_rest[1:].lstrip() if len(original_rest) > 1 else ''
                    # Accept if following text starts with uppercase (like "The" in "Abstract—The")
                    # or if it starts with punctuation
                    if following and (following[0].isupper() or following[0] in '—:-'):
                        return True, term.capitalize(), False
        if line_lower in SKIP_TERMS:
            return True, line, True
        
        return False, "", False
    
    current_title = "Introduction"
    current_content = []
    current_page_start = 1
    section_index = 0
    in_skip_section = False  # Track if we're in References/Appendix
    
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
                
            is_heading, matched_title, should_skip = is_strict_heading(line)
            
            # If we hit a skip section (References, Appendix), stop adding content
            if should_skip:
                # First save the previous section if it has content
                if current_content:
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
                
                # Now mark that we're in skip section mode
                in_skip_section = True
                current_content = []
                continue
            
            # If we hit a new section heading
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
                in_skip_section = False
            elif not in_skip_section:
                # Only add content if we're not in a skip section
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
    
    # Log detected sections for debugging
    section_titles = [s.title for s in sections]
    logger.info(f"[PDF INFO] Chunked PDF into {len(sections)} sections: {pdf_path}")
    logger.debug(f"[PDF DEBUG] Detected sections: {section_titles}")
    
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


def extract_feedback_from_reasoning(reasoning: str) -> str:
    """
    Extract just the feedback sentence from a reasoning/thinking string.
    
    The model often puts its thinking process in 'reasoning' followed by
    the actual feedback sentence. This function tries to extract just
    the feedback part.
    
    Looks for patterns like:
    - "So we can say: "feedback""
    - "Let's craft: "feedback""
    - Final sentence that looks like feedback
    """
    if not reasoning:
        return ""
    
    # Try to find feedback after common intro phrases
    intro_patterns = [
        r"FEEDBACK[:\s]+([^\n]+)",   # Explicit FEEDBACK: marker (highest priority)
        r"So we can say[:\s]+([^\n]+)",
        r"Let's craft[:\s]+([^\n]+)",
        r"So[:\s]+([^\n]+)",
        r"Thus[:\s]+([^\n]+)",
        r"Therefore[:\s]+([^\n]+)",
        r"Probably[:\s]+([^\n]+)",
        r"We can say[:\s]+([^\n]+)",
        r"So feedback[:\s]+([^\n]+)",  # Handle "So feedback: ..." style
    ]
    
    for pattern in intro_patterns:
        import re
        match = re.search(pattern, reasoning, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            # Make sure it's a reasonable length (not too long)
            if len(candidate) < 200:
                return candidate
    
    # If no intro pattern found, take the last sentence if it's short enough
    sentences = reasoning.split('.')
    if sentences:
        last = sentences[-1].strip()
        if len(last) < 150 and len(last) > 10:
            return last
        # If the last sentence is too long (thinking is cut off mid-sentence),
        # try to find the last sentence that's reasonably short and has feedback keywords
        feedback_keywords = ['issue', 'typo', 'error', 'missing', 'suggest', 'should', 'consider',
                           'unclear', 'vague', 'inconsistent', 'problem', 'recommend', 'concern',
                           'minor', 'format', 'incomplete', 'incorrect', 'fix', 'clarify']
        for sent in reversed(sentences[:-1]):
            sent = sent.strip()
            if 20 < len(sent) < 150:
                # Check if it looks like feedback (contains feedback keywords or specific terms)
                lower = sent.lower()
                if any(kw in lower for kw in feedback_keywords):
                    return sent

    # Otherwise, return the whole reasoning (better than nothing)
    # Increase limit to 1500 to avoid cutting mid-sentence in truncated responses
    return reasoning[:1500]


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
    # Allow more content for better context (increased from 4000)
    sanitized_content = sanitize_pdf_text(section.content[:8000])
    
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
        "max_tokens": 1024,  # Increased to allow both thinking + feedback content
        "temperature": 0.3,
        "stream": False,  # Must be explicitly False to get single JSON response
        "think": False,  # Disable extended thinking to get direct response
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
                    # The reasoning field may contain the actual feedback
                    if not review_text:
                        reasoning = message.get("reasoning")
                        if reasoning:
                            # Reasoning might be a string or dict (with summary array)
                            if isinstance(reasoning, str):
                                review_text = extract_feedback_from_reasoning(reasoning)
                            elif isinstance(reasoning, dict):
                                # Try summary array first, then the whole dict
                                summary = reasoning.get("summary")
                                if isinstance(summary, list) and summary:
                                    review_text = extract_feedback_from_reasoning(str(summary[0]))
                                elif isinstance(summary, str):
                                    review_text = extract_feedback_from_reasoning(summary)
                                else:
                                    review_text = extract_feedback_from_reasoning(str(reasoning))
                
                # Fallback to Ollama native format
                if not review_text:
                    ollama_message = result.get("message", {})
                    review_text = ollama_message.get("content", "").strip()
                    # Ollama extended thinking might be in 'thinking' field
                    if not review_text:
                        thinking = ollama_message.get("thinking")
                        if thinking:
                            review_text = extract_feedback_from_reasoning(str(thinking))
                
                # If still no content, check if reasoning is at top level
                if not review_text:
                    top_reasoning = result.get("reasoning")
                    if top_reasoning:
                        if isinstance(top_reasoning, str):
                            review_text = extract_feedback_from_reasoning(top_reasoning)
                        elif isinstance(top_reasoning, dict):
                            summary = top_reasoning.get("summary")
                            if isinstance(summary, list) and summary:
                                review_text = extract_feedback_from_reasoning(str(summary[0]))
                            elif isinstance(summary, str):
                                review_text = extract_feedback_from_reasoning(summary)
                
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
    Filter out ONLY truly trivial sections that can't be meaningfully reviewed.
    
    Philosophy: Be permissive. We'd rather review a boring section than miss
    a meaningful one. The LLM can handle content that doesn't need review.
    
    Only filters:
    - Empty or near-empty sections
    - Sections that are just "References" with no content
    - Pure bibliography/appendix content
    """
    # Sections that should NEVER be reviewed (pure metadata)
    NEVER_REVIEW = frozenset([
        'references', 'bibliography', 'acknowledgments', 'acknowledgements',
        'appendix', 'appendices', 'supplementary materials', 'supplementary information'
    ])
    
    # Minimum content threshold (very permissive)
    MIN_CONTENT_CHARS = 50
    
    filtered = []
    for section in sections:
        title_lower = section.title.lower().strip()
        content = section.content.strip()
        
        # Skip if title is in the never-review list AND content is minimal
        if title_lower in NEVER_REVIEW and len(content) < MIN_CONTENT_CHARS:
            logger.info(f"[SECTION FILTER] Skipping trivial section: '{section.title}' ({len(content)} chars)")
            continue
        
        # Skip completely empty sections
        if len(content) < 10:
            logger.info(f"[SECTION FILTER] Skipping empty section: '{section.title}' ({len(content)} chars)")
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
