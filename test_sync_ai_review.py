#!/usr/bin/env python3
"""
Test AI Review processing synchronously using test database.
This bypasses Redis/RQ and directly tests the PDF extraction and Ollama calls.
"""

import os
import sys

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_reviewboard.db")
PROD_DB_PATH = os.path.join(os.path.dirname(__file__), "reviewboard.db")

# Force testing mode
os.environ["TESTING"] = "1"

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app, db
from models import Ticket
from services.ai_reviewer import chunk_pdf_by_sections, process_pdf_sections

def main():
    # Create app with TESTING mode
    app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{TEST_DB_PATH}"
    })
    
    with app.app_context():
        # Find our test ticket
        test_ticket = Ticket.query.filter(
            Ticket.title.like("[TEST]%")
        ).order_by(Ticket.id.desc()).first()
        
        if not test_ticket:
            print("❌ No test ticket found. Run test_ai_review.py first.")
            return 1
        
        print(f"✅ Found test ticket: #{test_ticket.id} - {test_ticket.title}")
        
        # Get PDF path
        pdf_path = os.path.join(app.config["UPLOAD_FOLDER"], test_ticket.pdf_filename)
        print(f"📄 PDF: {pdf_path}")
        
        if not os.path.exists(pdf_path):
            print(f"❌ PDF not found!")
            return 1
        
        print(f"✅ PDF exists ({os.path.getsize(pdf_path):,} bytes)")
        
        # Test PDF chunking
        print("\n📑 Testing PDF chunking...")
        sections = chunk_pdf_by_sections(pdf_path)
        
        if not sections:
            print("❌ No sections extracted from PDF!")
            return 1
        
        print(f"✅ Extracted {len(sections)} sections from PDF:")
        for s in sections[:5]:  # Show first 5
            print(f"   - Section {s.index}: '{s.title}' (pages {s.page_start}-{s.page_end})")
        if len(sections) > 5:
            print(f"   ... and {len(sections) - 5} more sections")
        
        # Test Ollama with just the first 2 sections (to save time)
        print(f"\n🤖 Testing Ollama with first 2 sections...")
        test_sections = sections[:2]
        
        results = process_pdf_sections(
            test_sections,
            job_id="sync-test",
            ticket_id=test_ticket.id,
            db_session=db.session
        )
        
        print(f"\n📊 Results:")
        success_count = sum(1 for r in results if r.success)
        print(f"   Success: {success_count}/{len(results)}")
        
        for result in results:
            section = test_sections[result.section_index]
            if result.success:
                print(f"\n   ✅ Section {result.section_index} ({section.title}):")
                # Show first 200 chars of review
                review_preview = result.review[:200] + "..." if len(result.review) > 200 else result.review
                print(f"      {review_preview}")
            else:
                print(f"\n   ❌ Section {result.section_index} ({section.title}): {result.error}")
        
        if success_count == len(results):
            print("\n" + "="*60)
            print("✅ FULL END-TO-END TEST PASSED!")
            print("="*60)
            return 0
        else:
            print("\n" + "="*60)
            print("⚠️  PARTIAL TEST - Some sections failed")
            print("="*60)
            return 1

if __name__ == "__main__":
    sys.exit(main())
