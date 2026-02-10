#!/usr/bin/env python3
"""
Quality check script for walkthrough PDFs.

Layer 3 of the translation pipeline:
1. pipeline.py - generates walkthroughs from source PDFs
2. find_gaps.py - identifies and translates missing dictionary entries
3. quality_check.py - validates formatting and understandability (this script)

Checks:
1. Translation quality - flags overly literal, truncated, or garbled translations
2. Formatting issues - checks for missing sections, broken layouts
3. Readability - identifies unclear explanations or unhelpful tips
4. Consistency - ensures similar fields have consistent translations

Usage:
    python quality_check.py                    # Check all walkthroughs
    python quality_check.py --fix              # Auto-fix simple issues and report others
    python quality_check.py --pdf path.pdf     # Check specific PDF
"""

import argparse
import json
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

    pages_text = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                pages_text.append({"page": i + 1, "text": text})
    except Exception as e:
        print(f"  WARN: Could not read {pdf_path}: {e}")

    return pages_text


class QualityIssue:
    """Represents a quality issue found in a walkthrough."""

    SEVERITY_ERROR = "ERROR"
    SEVERITY_WARNING = "WARNING"
    SEVERITY_INFO = "INFO"

    def __init__(self, severity, category, message, context="", suggestion="", auto_fixable=False):
        self.severity = severity
        self.category = category
        self.message = message
        self.context = context
        self.suggestion = suggestion
        self.auto_fixable = auto_fixable

    def __str__(self):
        s = f"[{self.severity}] {self.category}: {self.message}"
        if self.context:
            s += f"\n    Context: {self.context[:80]}..."
        if self.suggestion:
            s += f"\n    Suggestion: {self.suggestion}"
        return s


def check_translation_quality(text, dictionary):
    """Check for translation quality issues."""
    issues = []
    fields = dictionary.get("fields", {})

    # Check for common translation problems
    patterns = {
        # Overly literal translations that don't make sense
        r'\b(polite verb ending|verb ending)\b': (
            QualityIssue.SEVERITY_WARNING,
            "Literal Translation",
            "Translation includes grammar description instead of actual translation",
            "Translate the full phrase, not just the grammar pattern"
        ),
        # Truncated translations
        r'^\([\w\s]+$': (
            QualityIssue.SEVERITY_WARNING,
            "Truncated Translation",
            "Translation appears to be cut off (unbalanced parentheses)",
            "Complete the translation"
        ),
        # Sample placeholder text left in
        r'●●|○○|\[.*?\]': (
            QualityIssue.SEVERITY_INFO,
            "Placeholder Text",
            "Contains placeholder markers (may be intentional for form examples)",
            "Verify this is intentional"
        ),
        # Mixed language issues (Chinese characters mixed with Japanese translation)
        r'[\u4e00-\u9fff]{5,}[a-zA-Z]{5,}[\u4e00-\u9fff]{5,}': (
            QualityIssue.SEVERITY_WARNING,
            "Mixed Language",
            "Text appears to have interleaved Japanese/Chinese and English characters",
            "Review translation for corruption"
        ),
    }

    for pattern, (severity, category, message, suggestion) in patterns.items():
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            for match in matches[:3]:  # Limit to first 3 matches
                issues.append(QualityIssue(
                    severity=severity,
                    category=category,
                    message=message,
                    context=match if isinstance(match, str) else str(match),
                    suggestion=suggestion
                ))

    return issues


def check_formatting(pages_text):
    """Check for formatting issues in the walkthrough."""
    issues = []

    if not pages_text:
        issues.append(QualityIssue(
            severity=QualityIssue.SEVERITY_ERROR,
            category="Empty PDF",
            message="PDF has no extractable text",
            suggestion="Regenerate the walkthrough"
        ))
        return issues

    # Check first page has expected header elements
    first_page = pages_text[0]["text"] if pages_text else ""

    expected_elements = [
        ("japan-forms", "Missing 'japan-forms' branding"),
        ("Bilingual Guide", "Missing 'Bilingual Guide' indicator"),
        ("Page", "Missing page numbers"),
    ]

    for element, message in expected_elements:
        if element.lower() not in first_page.lower():
            issues.append(QualityIssue(
                severity=QualityIssue.SEVERITY_WARNING,
                category="Missing Header",
                message=message,
                suggestion="Check PDF generation settings"
            ))

    # Check for common sections
    full_text = "\n".join(p["text"] for p in pages_text)

    expected_sections = [
        "WHAT TO BRING",
        "COMMON MISTAKES",
        "AFTER YOU SUBMIT",
        "COUNTER PHRASES",
    ]

    missing_sections = [s for s in expected_sections if s not in full_text]
    if missing_sections:
        issues.append(QualityIssue(
            severity=QualityIssue.SEVERITY_INFO,
            category="Missing Sections",
            message=f"Missing expected sections: {', '.join(missing_sections)}",
            suggestion="Some forms may not need all standard sections"
        ))

    return issues


def check_readability(pages_text, dictionary):
    """Check for readability issues."""
    issues = []
    fields = dictionary.get("fields", {})
    full_text = "\n".join(p["text"] for p in pages_text)

    # Check for unhelpful tips
    unhelpful_patterns = [
        (r'Staff use only', QualityIssue.SEVERITY_INFO, "Staff-only fields should be clearly marked 'DO NOT FILL IN'"),
        (r'\[Japanese text\]', QualityIssue.SEVERITY_ERROR, "Untranslated Japanese text found"),
    ]

    for pattern, severity, suggestion in unhelpful_patterns:
        if re.search(pattern, full_text, re.IGNORECASE):
            issues.append(QualityIssue(
                severity=severity,
                category="Readability",
                message=f"Found pattern: {pattern}",
                suggestion=suggestion
            ))

    # Check for very long lines that might indicate formatting issues
    for page in pages_text:
        lines = page["text"].split("\n")
        for line in lines:
            if len(line) > 200:
                issues.append(QualityIssue(
                    severity=QualityIssue.SEVERITY_WARNING,
                    category="Line Length",
                    message=f"Very long line on page {page['page']} ({len(line)} chars)",
                    context=line[:100],
                    suggestion="May indicate text extraction issues"
                ))
                break  # Only report once per page

    return issues


def check_dictionary_consistency(dictionary):
    """Check dictionary for consistency issues."""
    issues = []
    fields = dictionary.get("fields", {})

    # Group similar translations
    english_to_kanji = {}
    for field_id, field in fields.items():
        en = field.get("english", "").lower().strip()
        kanji = field.get("kanji", "")
        if en:
            if en not in english_to_kanji:
                english_to_kanji[en] = []
            english_to_kanji[en].append((field_id, kanji))

    # Find duplicate English translations for different Japanese
    for en, entries in english_to_kanji.items():
        if len(entries) > 3:
            kanji_list = [e[1] for e in entries[:5]]
            issues.append(QualityIssue(
                severity=QualityIssue.SEVERITY_INFO,
                category="Duplicate Translation",
                message=f"'{en}' used for {len(entries)} different Japanese terms",
                context=f"Examples: {', '.join(kanji_list)}",
                suggestion="Consider making translations more specific"
            ))

    # Check for missing required fields
    for field_id, field in fields.items():
        if not field.get("english"):
            issues.append(QualityIssue(
                severity=QualityIssue.SEVERITY_ERROR,
                category="Missing Translation",
                message=f"Field '{field_id}' has no English translation",
                context=field.get("kanji", ""),
                suggestion="Add English translation"
            ))
        if not field.get("kanji") and "auto_" not in field_id:
            issues.append(QualityIssue(
                severity=QualityIssue.SEVERITY_WARNING,
                category="Missing Kanji",
                message=f"Field '{field_id}' has no kanji",
                suggestion="Add Japanese text"
            ))

    # Check for auto-generated entries that could be improved
    auto_count = sum(1 for fid in fields if fid.startswith("auto_"))
    if auto_count > 100:
        issues.append(QualityIssue(
            severity=QualityIssue.SEVERITY_INFO,
            category="Auto-Generated Entries",
            message=f"Dictionary has {auto_count} auto-generated entries",
            suggestion="Review and consolidate auto-generated translations"
        ))

    return issues


def run_checks(pdf_path, dictionary):
    """Run all quality checks on a single PDF."""
    all_issues = []

    pages_text = extract_text_from_pdf(pdf_path)
    full_text = "\n".join(p["text"] for p in pages_text)

    # Run checks
    all_issues.extend(check_translation_quality(full_text, dictionary))
    all_issues.extend(check_formatting(pages_text))
    all_issues.extend(check_readability(pages_text, dictionary))

    return all_issues


def main():
    parser = argparse.ArgumentParser(description="Quality check walkthrough PDFs")
    parser.add_argument("--pdf", type=str, help="Check specific PDF file")
    parser.add_argument("--fix", action="store_true", help="Auto-fix simple issues")
    parser.add_argument("--dictionary-only", action="store_true", help="Only check dictionary consistency")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all issues including INFO")
    args = parser.parse_args()

    # Load dictionary
    dictionary = load_dictionary()

    all_issues = []
    files_checked = 0

    # Check dictionary consistency
    dict_issues = check_dictionary_consistency(dictionary)
    all_issues.extend(dict_issues)

    if not args.dictionary_only:
        if args.pdf:
            # Check specific PDF
            pdf_path = Path(args.pdf)
            if pdf_path.exists():
                issues = run_checks(pdf_path, dictionary)
                all_issues.extend(issues)
                files_checked = 1
            else:
                print(f"ERROR: File not found: {args.pdf}")
                return 1
        else:
            # Check all walkthroughs
            pdf_files = list(OUTPUT_DIR.rglob("*_walkthrough.pdf"))
            print(f"Checking {len(pdf_files)} walkthrough PDFs...")

            for pdf_path in pdf_files:
                issues = run_checks(pdf_path, dictionary)
                for issue in issues:
                    issue.file = str(pdf_path.relative_to(OUTPUT_DIR))
                all_issues.extend(issues)
                files_checked += 1

    # Filter issues by severity
    if not args.verbose:
        all_issues = [i for i in all_issues if i.severity != QualityIssue.SEVERITY_INFO]

    # Group and display issues
    errors = [i for i in all_issues if i.severity == QualityIssue.SEVERITY_ERROR]
    warnings = [i for i in all_issues if i.severity == QualityIssue.SEVERITY_WARNING]
    infos = [i for i in all_issues if i.severity == QualityIssue.SEVERITY_INFO]

    print(f"\n{'='*60}")
    print(f"Quality Check Summary")
    print(f"{'='*60}")
    print(f"Files checked: {files_checked}")
    print(f"Dictionary entries: {len(dictionary.get('fields', {}))}")
    print(f"\nIssues found:")
    print(f"  Errors:   {len(errors)}")
    print(f"  Warnings: {len(warnings)}")
    print(f"  Info:     {len(infos)}")

    if errors:
        print(f"\n{'='*60}")
        print("ERRORS (must fix)")
        print(f"{'='*60}")
        for issue in errors[:20]:
            print(f"\n{issue}")
            if hasattr(issue, 'file'):
                print(f"    File: {issue.file}")

    if warnings and (args.verbose or len(errors) == 0):
        print(f"\n{'='*60}")
        print("WARNINGS (should review)")
        print(f"{'='*60}")
        for issue in warnings[:20]:
            print(f"\n{issue}")

    if args.verbose and infos:
        print(f"\n{'='*60}")
        print("INFO (for reference)")
        print(f"{'='*60}")
        for issue in infos[:20]:
            print(f"\n{issue}")

    print(f"\n{'='*60}")

    # Return error code if errors found
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
