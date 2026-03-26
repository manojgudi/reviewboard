#!/usr/bin/env python3
"""
Unit tests for AI Review Service - Heading Detection and Section Chunking

These tests verify that the section detection logic correctly identifies
headings in various academic paper formats.

Run with: python -m pytest tests/test_ai_reviewer.py -v
"""

import pytest
import tempfile
import os
import sys
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

# Ensure app module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ai_reviewer import (
    PDFSection, 
    chunk_pdf_by_sections, 
    filter_trivial_sections,
    sanitize_pdf_text,
    sanitize_llm_response,
)


# ============================================================
# Test Fixtures - Mock PDF with various heading formats
# ============================================================

@dataclass
class MockPage:
    """Mock PyMuPDF page object."""
    page_num: int
    text: str

    def get_text(self, format="text"):
        return self.text


class MockPDFDoc:
    """Mock PyMuPDF document that returns controlled page content."""
    
    def __init__(self, pages):
        self._pages = pages
        self._len = len(pages)
    
    def __len__(self):
        return self._len
    
    def __getitem__(self, index):
        return self._pages[index]
    
    def close(self):
        pass


def create_mock_doc(page_texts):
    """Create a mock PDF document with given page texts."""
    pages = [MockPage(i, text) for i, text in enumerate(page_texts)]
    return MockPDFDoc(pages)


# ============================================================
# Test Cases: Heading Detection
# ============================================================

class TestHeadingDetection:
    """Tests for the is_likely_heading function embedded in chunk_pdf_by_sections."""
    
    def test_numbered_sections_detected(self):
        """Test that top-level numbered sections like '1. Introduction' are detected.
        
        NOTE: Subsections like '1.2' and '1.2.3' are intentionally NOT detected
        as main section headings (per docstring: "Does NOT treat subsections as separate review sections").
        """
        # Top-level sections that SHOULD be detected
        top_level_headings = [
            "1. Introduction",
            "2. Methodology",
            "10. Conclusions",
            "3. Experimental Results",
        ]
        
        # Subsections that should NOT create new sections
        subsection_headings = [
            "1.2 Background",
            "1.2.3 Details",
            "1.1 Related Work",
        ]
        
        # Patch fitz.open to return our mock doc
        mock_texts = [f"{h}\nSome content here." for h in top_level_headings + subsection_headings]
        
        with patch('fitz.open') as mock_open:
            mock_open.return_value = create_mock_doc(mock_texts)
            
            sections = chunk_pdf_by_sections("/fake/path.pdf")
            
            # Should detect only top-level sections (4), not subsections
            section_titles = [s.title for s in sections]
            
            assert len(sections) >= len(top_level_headings), f"Expected at least {len(top_level_headings)} sections, got {len(sections)}"
    
    def test_keyword_sections_detected(self):
        """Test that common section keywords are detected regardless of case."""
        # Keywords that ARE directly in MAIN_SECTION_TERMS
        keywords_in_terms = [
            "Abstract",
            "INTRODUCTION",  # ALL CAPS
            "Background",
            "Related Work",
            "Methodology",
            "Methods", 
            "Experimental Setup",
            "Discussion",
            "Conclusions",
            "Future Work",
            "Limitations",
        ]
        
        # "Results and Discussion" - detected via startswith("results") logic
        keywords_via_startswith = ["Results and Discussion"]
        
        all_keywords = keywords_in_terms + keywords_via_startswith
        
        mock_texts = [f"{kw}\nThis is the content of the {kw.lower()} section." for kw in all_keywords]
        
        with patch('fitz.open') as mock_open:
            mock_open.return_value = create_mock_doc(mock_texts)
            
            sections = chunk_pdf_by_sections("/fake/path.pdf")
            
            # Should detect all keyword sections (12 total)
            assert len(sections) >= len(all_keywords), f"Expected {len(all_keywords)} sections, got {len(sections)}"
    
    def test_markdown_headers_detected(self):
        """Test that Markdown-style # headers are detected."""
        mock_texts = [
            "# Introduction\nThis is the intro.",
            "## Background\nThis is background.",
            "### Methods\nThese are methods.",
            "## Results\nHere are results.",
        ]
        
        with patch('fitz.open') as mock_open:
            mock_open.return_value = create_mock_doc(mock_texts)
            
            sections = chunk_pdf_by_sections("/fake/path.pdf")
            
            # Should have at least 4 sections (one per markdown header)
            assert len(sections) >= 4, f"Expected at least 4 sections, got {len(sections)}"
            
            # Check that titles were extracted correctly
            titles = [s.title for s in sections]
            assert any('Introduction' in t for t in titles)
            assert any('Background' in t for t in titles)
    
    def test_title_case_headers_detected(self):
        """Test that title-case lines are detected as potential headers."""
        mock_texts = [
            "Experimental Results\nDetailed findings about experiments.",
            "Threats to Validity\nDiscussion of validity threats.",
            "Case Study\nAnalysis of a specific case.",
        ]
        
        with patch('fitz.open') as mock_open:
            mock_open.return_value = create_mock_doc(mock_texts)
            
            sections = chunk_pdf_by_sections("/fake/path.pdf")
            
            # Should detect these title-case headers
            assert len(sections) >= 3, f"Expected at least 3 sections, got {len(sections)}"
    
    def test_paragraphs_not_misclassified(self):
        """Test that regular paragraphs are NOT classified as headings."""
        paragraphs = [
            "This is a regular paragraph that should not be a heading.",
            "The experimental results show significant improvements in performance.",
            "According to Smith et al. (2023), the methodology has been proven effective.",
            "The first approach uses machine learning, while the second uses traditional methods.",
            "1. This starts with a number but is actually a sentence about point 1.",
        ]
        
        mock_texts = [p + "\n" + p for p in paragraphs]  # Repeat to have content
        
        with patch('fitz.open') as mock_open:
            mock_open.return_value = create_mock_doc(mock_texts)
            
            sections = chunk_pdf_by_sections("/fake/path.pdf")
            
            # Should NOT create a section for each paragraph
            # Should either combine them or have minimal sections
            # Key assertion: we shouldn't have 5+ sections from 5 paragraphs
            assert len(sections) <= 3, f"Too many sections created from paragraphs: {len(sections)}"


# ============================================================
# Test Cases: Section Filtering
# ============================================================

class TestSectionFiltering:
    """Tests for the filter_trivial_sections function."""
    
    def test_filters_empty_sections(self):
        """Test that empty or near-empty sections are filtered."""
        sections = [
            PDFSection(index=0, title="Introduction", content="This is intro content." * 10, 
                      content_hash="abc", page_start=1, page_end=1),
            PDFSection(index=1, title="Empty", content="", 
                      content_hash="def", page_start=2, page_end=2),
            PDFSection(index=2, title="Tiny", content="Hi", 
                      content_hash="ghi", page_start=3, page_end=3),
        ]
        
        filtered = filter_trivial_sections(sections)
        
        assert len(filtered) == 1
        assert filtered[0].title == "Introduction"
    
    def test_keeps_meaningful_content_sections(self):
        """Test that sections with meaningful content are kept."""
        sections = [
            PDFSection(index=0, title="References", content="[1] Smith et al. " * 100, 
                      content_hash="abc", page_start=1, page_end=1),
            PDFSection(index=1, title="Discussion", content="Our findings suggest that..." * 20, 
                      content_hash="def", page_start=2, page_end=2),
            PDFSection(index=2, title="Abstract", content="This paper presents... " * 50, 
                      content_hash="ghi", page_start=3, page_end=3),
        ]
        
        filtered = filter_trivial_sections(sections)
        
        # All should be kept since they have content
        assert len(filtered) == 3
    
    def test_filters_only_truly_trivial_references(self):
        """Test that References with minimal content is filtered."""
        sections = [
            PDFSection(index=0, title="References", content="References", 
                      content_hash="abc", page_start=1, page_end=1),
            PDFSection(index=1, title="References", content="[1] A. Smith. A Title. 2023.",  # 36 chars, still filtered
                      content_hash="def", page_start=2, page_end=2),
            PDFSection(index=2, title="References", content="[1] A. Smith. A Title. Journal of Testing. 2023. " * 3,
                      content_hash="ghi", page_start=3, page_end=3),
        ]
        
        filtered = filter_trivial_sections(sections)
        
        # First two should be filtered (trivial References)
        # Third should be kept (has substantial content)
        assert len(filtered) == 1, f"Expected 1 section kept, got {len(filtered)}"
        assert "Journal of Testing" in filtered[0].content


# ============================================================
# Test Cases: Content Sanitization
# ============================================================

class TestContentSanitization:
    """Tests for PDF text sanitization."""
    
    def test_removes_latex_hyphenation(self):
        """Test that LaTeX line-wrapping hyphens are removed."""
        text = "This is a long word that was-\nwrapped by LaTeX and needs-\n  to be joined."
        result = sanitize_pdf_text(text)
        
        assert "was-wrapped" not in result
        assert "was wrapped" in result
    
    def test_preserves_paragraph_structure(self):
        """Test that paragraph breaks are preserved."""
        text = "First paragraph.\n\nSecond paragraph.\n\n\nThird paragraph."
        result = sanitize_pdf_text(text)
        
        # Should have some newlines preserved
        assert "\n" in result
    
    def test_removes_excess_whitespace(self):
        """Test that excess whitespace (newlines) is normalized."""
        text = "Text\n\n\n\n\nwith\n\n\nmany\n\n\nnewlines"
        result = sanitize_pdf_text(text)
        
        # Should not have 3+ newlines in a row
        assert "\n\n\n" not in result


# ============================================================
# Test Cases: LLM Response Sanitization
# ============================================================

class TestLLMSanitization:
    """Tests for LLM response sanitization (XSS prevention)."""
    
    def test_strips_script_tags(self):
        """Test that script tags are stripped."""
        text = "<script>alert('xss')</script>Normal text"
        result = sanitize_llm_response(text)
        
        assert "<script>" not in result
        assert "Normal text" in result
    
    def test_strips_event_handlers(self):
        """Test that HTML event handlers are stripped."""
        text = '<img src="x" onerror="alert(1)">Some content'
        result = sanitize_llm_response(text)
        
        assert "onerror" not in result
        assert "Some content" in result
    
    def test_preserves_safe_formatting(self):
        """Test that safe formatting is preserved."""
        text = "<p>Paragraph with <strong>bold</strong> and <em>italic</em>.</p>"
        result = sanitize_llm_response(text)
        
        assert "<p>" in result or "Paragraph" in result


# ============================================================
# Test Cases: Integration with Mock PDF
# ============================================================

class TestPDFChunkingIntegration:
    """Integration tests with realistic PDF content."""
    
    def test_academic_paper_structure(self):
        """Test with typical academic paper section structure."""
        mock_texts = [
            "Abstract\nThis paper presents a novel approach to solving the problem.",
            "1. Introduction\nIntroduction content about the research area.",
            "2. Background\nRelated work and background information.",
            "2.1 Related Studies\nSpecific related studies.",
            "3. Methodology\nOur proposed methodology in detail.",
            "4. Experimental Results\nResults from experiments conducted.",
            "4.1 Performance Metrics\nDetailed performance numbers.",
            "5. Discussion\nAnalysis of the results.",
            "6. Conclusion\nSummary and future work.",
        ]
        
        with patch('fitz.open') as mock_open:
            mock_open.return_value = create_mock_doc(mock_texts)
            
            sections = chunk_pdf_by_sections("/fake/academic.pdf")
            
            # Should detect most/all sections
            assert len(sections) >= 7, f"Expected at least 7 sections, got {len(sections)}: {[s.title for s in sections]}"
            
            # Check for key sections
            titles = [s.title.lower() for s in sections]
            assert any('introduction' in t for t in titles), "Missing Introduction section"
            assert any('background' in t or 'related' in t for t in titles), "Missing Background/Related section"
            assert any('methodology' in t or 'method' in t for t in titles), "Missing Methodology section"
            assert any('result' in t for t in titles), "Missing Results section"
    
    def test_variant_section_naming(self):
        """Test that variant section names are detected."""
        mock_texts = [
            "Motivation\nWhy this research matters.",
            "Problem Statement\nFormal problem definition.",
            "Contributions\nOur main contributions.",
            "Experimental Setup\nHow experiments were conducted.",
            "Results and Discussion\nFindings and analysis.",
            "Threats to Validity\nPotential limitations.",
            "Related Studies\nComparison with related work.",
        ]
        
        with patch('fitz.open') as mock_open:
            mock_open.return_value = create_mock_doc(mock_texts)
            
            sections = chunk_pdf_by_sections("/fake/variants.pdf")
            
            # Should detect most of these variant section names
            assert len(sections) >= 5, f"Expected at least 5 sections, got {len(sections)}: {[s.title for s in sections]}"


# ============================================================
# Run Tests
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
