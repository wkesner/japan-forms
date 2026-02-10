#!/usr/bin/env python3
"""
Scan walkthrough PDFs for untranslated text and auto-translate using Claude API.
Caches translations in the dictionary.json for future pipeline runs.

Usage:
    python find_gaps.py                          # Scan all walkthroughs, report gaps
    python find_gaps.py --translate              # Scan + translate gaps via Claude API
    python find_gaps.py --translate --limit 50   # Translate up to 50 gaps
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ── Paths ──
BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output" / "walkthroughs"
DICTIONARY_PATH = BASE_DIR / "data" / "fields" / "dictionary.json"


def load_dictionary():
    """Load the field dictionary."""
    with open(DICTIONARY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_dictionary(data):
    """Save the field dictionary."""
    with open(DICTIONARY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_text_from_pdf(pdf_path):
    """Extract all text from a PDF file."""
    try:
        import pdfplumber
    except ImportError:
        print("ERROR: pdfplumber not installed. Run: pip install pdfplumber")
        sys.exit(1)

    text_content = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_content.append(text)
    except Exception as e:
        print(f"  WARN: Could not read {pdf_path}: {e}")

    return "\n".join(text_content)


def find_untranslated_gaps(text):
    """
    Find Japanese text that appears in brackets without translation.

    Pattern: `[Japanese text]` where Japanese text contains kanji/hiragana/katakana
    These are fields that were extracted but not translated.
    """
    # Pattern matches [Japanese text] where Japanese includes kanji, hiragana, or katakana
    # but excludes things like [1] or [blank field]
    pattern = r'\[([^\[\]]+)\]'
    matches = re.findall(pattern, text)

    gaps = []
    for match in matches:
        # Check if it contains Japanese characters (kanji, hiragana, katakana)
        has_japanese = any(
            '\u4e00' <= c <= '\u9fff' or  # CJK Unified Ideographs (kanji)
            '\u3040' <= c <= '\u309f' or  # Hiragana
            '\u30a0' <= c <= '\u30ff'     # Katakana
            for c in match
        )
        # Skip if it's just a description in English
        if has_japanese and not match.startswith('blank'):
            # Clean up the text
            cleaned = match.strip()
            if cleaned and len(cleaned) > 0:
                gaps.append(cleaned)

    return gaps


def is_already_in_dictionary(text, dictionary):
    """Check if text already has a dictionary entry."""
    fields = dictionary.get("fields", {})

    for field_id, field in fields.items():
        # Check exact kanji match
        if text == field.get("kanji", ""):
            return True
        # Check aliases
        if text in field.get("aliases", []):
            return True
        # Check options
        for opt_key in field.get("options", {}).keys():
            if text == opt_key:
                return True

    return False


def translate_with_claude(text, existing_dictionary):
    """Translate Japanese text using Claude API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set")
        return None

    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not installed. Run: pip install anthropic")
        return None

    # Build context from existing dictionary entries
    sample_entries = []
    fields = existing_dictionary.get("fields", {})
    for fid, field in list(fields.items())[:5]:
        sample_entries.append(f'  "{field.get("kanji", "")}": "{field.get("english", "")}"')
    context = "\n".join(sample_entries)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": f"""Translate this Japanese government form text to English.
Context: This appears on municipal forms like residence registration (住民異動届).

Similar translations in our dictionary:
{context}

Text to translate: {text}

Reply in EXACTLY this JSON format (nothing else):
{{
  "english": "<translation>",
  "reading": "<hiragana reading or empty string>",
  "tip": "<brief tip for foreign residents filling this field, or empty string if not applicable>",
  "category": "<one of: personal, address, household, document, administrative, date, other>"
}}

Important:
- If it's staff-use-only text (like 受付, 処理, 入力, 照合), still translate but set tip to "Staff use only - do not fill in"
- If it's a checkbox or abbreviation, expand the full meaning
- Keep translations concise (1-5 words preferred)"""
                }
            ],
        )

        raw = message.content[0].text.strip()
        # Parse JSON response
        # Handle potential markdown code blocks
        if raw.startswith("```"):
            raw = re.sub(r'^```json?\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)

        result = json.loads(raw)
        return result

    except json.JSONDecodeError as e:
        print(f"  WARN: Could not parse LLM response for '{text}': {e}")
        print(f"  Raw response: {raw[:200]}")
        return None
    except Exception as e:
        print(f"  WARN: LLM translation failed for '{text}': {e}")
        return None


def generate_field_id(text):
    """Generate a unique field ID from Japanese text."""
    # Use romaji-ish conversion for common characters, fallback to hash
    import hashlib

    # Simple mapping for common administrative terms
    mappings = {
        "受付": "uketsuke",
        "処理": "shori",
        "入力": "nyuryoku",
        "照合": "shougou",
        "確認": "kakunin",
        "国保": "kokuho",
        "年金": "nenkin",
        "介護": "kaigo",
        "後期": "kouki",
        "学務": "gakumu",
        "障福": "shofuku",
        "旧氏": "kyuushi",
        "通カ": "tsuuka",
        "在カ": "zaika",
        "住コ": "juuko",
    }

    for jp, romaji in mappings.items():
        if jp in text:
            suffix = hashlib.md5(text.encode()).hexdigest()[:4]
            return f"{romaji}_{suffix}"

    # Default: use hash
    return f"auto_{hashlib.md5(text.encode()).hexdigest()[:8]}"


def add_to_dictionary(dictionary, text, translation):
    """Add a new translation to the dictionary."""
    fields = dictionary.get("fields", {})

    field_id = generate_field_id(text)
    # Ensure unique ID
    base_id = field_id
    counter = 1
    while field_id in fields:
        field_id = f"{base_id}_{counter}"
        counter += 1

    fields[field_id] = {
        "kanji": text,
        "reading": translation.get("reading", ""),
        "english": translation.get("english", text),
        "category": translation.get("category", "administrative"),
        "tip_en": translation.get("tip", ""),
        "appears_on": ["residence_registration"],
        "confidence": "draft",
        "notes": "Auto-translated by find_gaps.py"
    }

    dictionary["fields"] = fields
    return field_id


def scan_all_walkthroughs():
    """Scan all walkthrough PDFs and collect untranslated gaps."""
    all_gaps = {}  # text -> list of files it appears in

    pdf_files = list(OUTPUT_DIR.rglob("*_walkthrough.pdf"))
    print(f"Scanning {len(pdf_files)} walkthrough PDFs...")

    for pdf_path in pdf_files:
        rel_path = pdf_path.relative_to(OUTPUT_DIR)
        text = extract_text_from_pdf(pdf_path)
        gaps = find_untranslated_gaps(text)

        for gap in gaps:
            if gap not in all_gaps:
                all_gaps[gap] = []
            all_gaps[gap].append(str(rel_path))

    return all_gaps


def main():
    parser = argparse.ArgumentParser(description="Find and translate gaps in walkthrough PDFs")
    parser.add_argument("--translate", action="store_true", help="Translate gaps using Claude API")
    parser.add_argument("--limit", type=int, default=100, help="Max gaps to translate (default: 100)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be translated without making changes")
    args = parser.parse_args()

    # Load dictionary
    dictionary = load_dictionary()

    # Scan for gaps
    all_gaps = scan_all_walkthroughs()

    # Filter out gaps already in dictionary
    new_gaps = {}
    for text, files in all_gaps.items():
        if not is_already_in_dictionary(text, dictionary):
            new_gaps[text] = files

    print(f"\nFound {len(all_gaps)} total bracketed Japanese items")
    print(f"Of those, {len(new_gaps)} are not in the dictionary\n")

    # Sort by frequency (most common first)
    sorted_gaps = sorted(new_gaps.items(), key=lambda x: len(x[1]), reverse=True)

    # Show top gaps
    print("Top untranslated gaps by frequency:")
    print("-" * 60)
    for i, (text, files) in enumerate(sorted_gaps[:30]):
        print(f"  {i+1:3}. [{text}] — appears in {len(files)} file(s)")

    if len(sorted_gaps) > 30:
        print(f"  ... and {len(sorted_gaps) - 30} more")

    # Translate if requested
    if args.translate and not args.dry_run:
        print(f"\n{'='*60}")
        print(f"Translating up to {args.limit} gaps using Claude API...")
        print(f"{'='*60}\n")

        translated = 0
        for text, files in sorted_gaps[:args.limit]:
            print(f"Translating: {text}")
            result = translate_with_claude(text, dictionary)

            if result:
                field_id = add_to_dictionary(dictionary, text, result)
                print(f"  -> {result['english']} (id: {field_id})")
                translated += 1
            else:
                print(f"  -> FAILED")

            # Small delay to avoid rate limiting
            import time
            time.sleep(0.5)

        # Save updated dictionary
        if translated > 0:
            save_dictionary(dictionary)
            print(f"\n{'='*60}")
            print(f"Added {translated} new translations to dictionary.json")
            print(f"Run the pipeline again to regenerate walkthroughs with new translations")
            print(f"{'='*60}")

    elif args.dry_run:
        print(f"\n[DRY RUN] Would translate {min(len(sorted_gaps), args.limit)} gaps")

    return 0


if __name__ == "__main__":
    sys.exit(main())
