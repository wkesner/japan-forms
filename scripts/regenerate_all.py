#!/usr/bin/env python3
"""
Regenerate all walkthroughs with standardized naming: ward_formtype_v1.pdf

Form type detection based on filename patterns:
- tenshutsu/tensyutsu → moveout
- tennyuu → movein
- ido/idou/juminido → registration
- yuusou/yuso → mail (postal submission)
- kinyurei/sample/kakikata → example
- eigo/english → english
- mynumber → mynumber
"""

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
INPUT_DIR = BASE_DIR / "input" / "tokyo"
OUTPUT_DIR = BASE_DIR / "output" / "walkthroughs" / "tokyo"


def detect_form_type(filename: str) -> str:
    """Detect form type from filename patterns."""
    name = filename.lower()

    # Check for specific types first
    if "english" in name or "eigo" in name:
        return "english"
    if "kinyurei" in name or "sample" in name or "kakikata" in name:
        return "example"
    if "mynumber" in name or "mynatensyutsu" in name:
        return "mynumber"
    if "tennyuu" in name:
        return "movein"
    if "tenshutsu" in name or "tensyutsu" in name or "tenshutu" in name:
        return "moveout"
    if "yuusou" in name or "yuso" in name:
        return "mail"
    if "ido" in name or "juminido" in name:
        return "registration"
    if "tetuduki" in name:
        return "guide"
    if "hp" in name:
        return "homepage"

    # Default
    return "form"


def get_version(ward_dir: Path, form_type: str, existing: dict) -> int:
    """Get next version number for this ward/formtype combo."""
    key = f"{ward_dir.name}_{form_type}"
    existing[key] = existing.get(key, 0) + 1
    return existing[key]


def main():
    # Track version numbers per ward/formtype
    versions = {}

    # Collect all PDFs
    pdfs = sorted(INPUT_DIR.rglob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs to process\n")

    # Skip setagaya for now (OCR issues)
    skip_wards = {"setagaya"}

    for pdf_path in pdfs:
        ward = pdf_path.parent.name

        if ward in skip_wards:
            print(f"SKIP: {ward}/{pdf_path.name} (OCR issues)")
            continue

        form_type = detect_form_type(pdf_path.stem)
        version = get_version(pdf_path.parent, form_type, versions)

        # New output name
        new_name = f"{ward}_{form_type}_v{version}_walkthrough.pdf"

        print(f"Processing: {ward}/{pdf_path.name}")
        print(f"  → {new_name}")

        # Ensure output directory exists
        ward_output = OUTPUT_DIR / ward
        ward_output.mkdir(parents=True, exist_ok=True)

        # Run pipeline
        result = subprocess.run(
            [sys.executable, "scripts/pipeline.py", str(pdf_path)],
            cwd=BASE_DIR,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            print(f"  ERROR: {result.stderr[:200]}")
            continue

        # Find the generated walkthrough and rename it
        old_walkthrough = ward_output / f"{pdf_path.stem}_walkthrough.pdf"
        new_walkthrough = ward_output / new_name

        if old_walkthrough.exists():
            old_walkthrough.rename(new_walkthrough)
            print(f"  OK: {new_name}")
        else:
            print(f"  WARN: Expected output not found: {old_walkthrough.name}")

        print()

    print("\nDone!")


if __name__ == "__main__":
    main()
