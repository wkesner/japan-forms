#!/usr/bin/env python3
"""
japan-forms generator: Reads structured JSON data and produces
Markdown guides and PDF form translation sheets.

Usage:
    python generate.py                                    # Generate all
    python generate.py --municipality tokyo/minato-ku     # One municipality
    python generate.py --form residence_registration      # One form type
    python generate.py --format markdown                  # Only markdown
    python generate.py --format pdf                       # Only PDF
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import date

# ── Paths ──
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
FIELDS_PATH = DATA_DIR / "fields" / "dictionary.json"
FORMS_DIR = DATA_DIR / "forms"
MUNICIPALITIES_DIR = DATA_DIR / "municipalities"
OUTPUT_DIR = BASE_DIR / "output"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_field_dictionary():
    data = load_json(FIELDS_PATH)
    return data["fields"]


def load_form_template(form_id):
    path = FORMS_DIR / f"{form_id}.json"
    if not path.exists():
        print(f"  ⚠ Form template not found: {path}")
        return None
    return load_json(path)


def load_municipality(municipality_id):
    """Load municipality data. municipality_id like 'tokyo/minato-ku'"""
    path = MUNICIPALITIES_DIR / f"{municipality_id}.json"
    if not path.exists():
        print(f"  ⚠ Municipality not found: {path}")
        return None
    return load_json(path)


def find_all_municipalities():
    """Find all municipality JSON files (excluding template)."""
    results = []
    for root, dirs, files in os.walk(MUNICIPALITIES_DIR):
        for f in files:
            if f.endswith(".json") and f != "_template.json":
                full = Path(root) / f
                rel = full.relative_to(MUNICIPALITIES_DIR).with_suffix("")
                results.append(str(rel))
    return sorted(results)


def find_all_forms():
    """Find all form template JSON files (excluding schema)."""
    results = []
    for f in FORMS_DIR.glob("*.json"):
        if f.name.startswith("_"):
            continue
        results.append(f.stem)
    return sorted(results)


# ═══════════════════════════════════════════
# MARKDOWN GENERATOR
# ═══════════════════════════════════════════

def generate_markdown(form_id, municipality_id, fields, form, muni):
    """Generate a Markdown guide for a specific form + municipality combo."""

    out_dir = OUTPUT_DIR / "markdown" / municipality_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{form_id}.md"

    muni_name_en = muni["names"]["en"]
    muni_name_ja = muni["names"]["ja"]
    form_name_en = form["names"]["en"]
    form_name_ja = form["names"]["ja"]

    lines = []
    w = lines.append  # shorthand

    # ── Header ──
    w(f"# {form_name_en} ({form_name_ja}) — {muni_name_en} ({muni_name_ja})")
    w("")
    w(f"> **Form name:** {form_name_ja} ({form['names']['romaji']})")
    aliases = ", ".join(form["names"].get("aliases_ja", []))
    if aliases:
        w(f"> **Also called:** {aliases}")
    w(f"> **Cost:** ¥{form['legal_basis']['cost']} ({form['legal_basis']['cost_note_en']})")
    w(f"> **Deadline:** {form['legal_basis']['deadline_description_en']}")
    w(f"> **Penalty:** {form['legal_basis']['penalty_en']}")
    w("")
    w("---")
    w("")

    # ── Overview / Difficulty (if present) ──
    overview = form.get("overview", {})
    if overview:
        if overview.get("difficulty_en"):
            w(f"⚠️ **Difficulty:** {overview['difficulty_en']}")
            w("")
        if overview.get("timing_en"):
            w(f"⏰ **Timing:** {overview['timing_en']}")
            w("")
        if overview.get("strategy_en"):
            w(f"💡 **Strategy:** {overview['strategy_en']}")
            w("")
        w("---")
        w("")

    # ── Eligibility (if present) ──
    eligibility = form.get("eligibility", {})
    if eligibility:
        w("## Eligibility")
        w("")
        if eligibility.get("residence_requirement_en"):
            w(f"**Residence requirement:** {eligibility['residence_requirement_en']}")
            w("")
        if eligibility.get("visa_requirement_en"):
            w(f"**Visa requirement:** {eligibility['visa_requirement_en']}")
            w("")
        for exc in eligibility.get("exceptions", []):
            w(f"- {exc}")
        w("")
        w("---")
        w("")

    # ── Business Manager Visa Context (if present) ──
    bm = form.get("business_manager_visa_context", {})
    if bm:
        w("## Business Manager Visa Requirements")
        w("")
        if bm.get("capital_requirement_current"):
            w(f"**Capital (current):** {bm['capital_requirement_current']}")
        if bm.get("capital_requirement_new"):
            w(f"**Capital (from Oct 2025):** {bm['capital_requirement_new']}")
        if bm.get("capital_note_en"):
            w(f"")
            w(f"{bm['capital_note_en']}")
        if bm.get("employee_requirement"):
            w(f"")
            w(f"**Employees:** {bm['employee_requirement']}")
        if bm.get("japanese_ability_note"):
            w(f"")
            w(f"**Japanese ability:** {bm['japanese_ability_note']}")
        if bm.get("physical_office_note"):
            w(f"")
            w(f"**Office:** {bm['physical_office_note']}")
        w("")
        w("---")
        w("")

    # ── Bank Comparison Table (if present) ──
    for comp_key in ["bank_comparison", "bank_comparison_corporate"]:
        banks = form.get(comp_key, [])
        if banks:
            w("## Bank Comparison")
            w("")
            # Determine columns based on what's available
            if "approval_difficulty" in banks[0]:
                w("| Bank | Japanese | Approval Difficulty | Screening Time | English | Notes |")
                w("|------|----------|-------------------|----------------|---------|-------|")
                for b in banks:
                    w(f"| {b['bank_en']} | {b['bank_ja']} | {b.get('approval_difficulty','-')} | {b.get('screening_time','-')} | {b.get('english_support','-')} | {b.get('note','')} |")
            else:
                w("| Bank | Japanese | English Support | Min Residency | Hanko? | Debit Card | Best For |")
                w("|------|----------|----------------|---------------|--------|------------|----------|")
                for b in banks:
                    hanko = "Required" if b.get("hanko_required") else "Not needed"
                    debit = "✅" if b.get("debit_card") else "❌"
                    w(f"| {b['bank_en']} | {b['bank_ja']} | {b.get('english_support','-')} | {b.get('min_residency','-')} | {hanko} | {debit} | {b.get('best_for','')} |")
            w("")
            w("---")
            w("")

    # ── What to Bring (per scenario) ──
    w("## What to Bring")
    w("")
    for scenario_key, scenario in form.get("scenarios", {}).items():
        w(f"### {scenario['title_en']} ({scenario['title_ja']})")
        w("")
        w("| Document | Japanese | Required | Notes |")
        w("|----------|----------|----------|-------|")
        for doc in scenario["documents_required"]:
            req = "✅ Yes" if doc["required"] else "Optional"
            cond = doc.get("condition_en", "")
            w(f"| {doc['en']} | {doc['ja']} | {req} | {cond} |")
        w("")

    w("---")
    w("")

    # ── Field-by-Field Translation ──
    w("## Field-by-Field Translation Guide")
    w("")

    for section in form.get("sections", []):
        w(f"### Section {section['section_number']}: {section['title_en']} ({section['title_ja']})")
        w("")

        if section.get("note_en"):
            w(f"*{section['note_en']}*")
            w("")

        w("| Japanese | Reading | English | Required | Tip |")
        w("|----------|---------|---------|----------|-----|")

        for field_ref in section.get("fields", []):
            fid = field_ref["field_id"]
            field = fields.get(fid, {})
            if not field:
                w(f"| _{fid}_ | — | — | — | ⚠ Field not in dictionary |")
                continue

            kanji = field.get("kanji", "")
            reading = field.get("romaji", "")
            # Use context label if provided, otherwise field english
            en = field_ref.get("context_label_en", field.get("english", ""))
            req = "✅" if field_ref.get("required") else "—"
            tip = field_ref.get("note_en", field.get("tip_en", ""))

            # If field has options, append them
            opts = field.get("options", {})
            if opts and len(opts) <= 4:
                opt_str = ", ".join(f"{k}={v['english']}" for k, v in opts.items())
                tip = f"{tip} ({opt_str})" if tip else opt_str

            w(f"| {kanji} | {reading} | {en} | {req} | {tip} |")

        w("")

    # ── Counter Phrases ──
    phrases = form.get("counter_phrases", [])
    if phrases:
        w("---")
        w("")
        w("## Useful Phrases at the Counter")
        w("")
        w("| Situation | Japanese | Romaji | English |")
        w("|-----------|----------|--------|---------|")
        for p in phrases:
            w(f"| {p['situation_en']} | {p['ja']} | {p['romaji']} | {p['en']} |")
        w("")

    # ── After Submission ──
    steps = form.get("after_submission", [])
    if steps:
        w("---")
        w("")
        w("## What Happens After You Submit")
        w("")
        for s in steps:
            w(f"{s['step']}. **{s['ja']}** — {s['en']}")
        w("")

    # ── Common Mistakes ──
    mistakes = form.get("common_mistakes", [])
    if mistakes:
        w("---")
        w("")
        w("## Common Mistakes to Avoid")
        w("")
        for m in mistakes:
            w(f"**{m['mistake_en']}** — {m['fix_en']}")
            w("")

    # ── Screening Tips (if present) ──
    tips = form.get("screening_tips", [])
    if tips:
        w("---")
        w("")
        w("## Screening Tips (How to Get Approved)")
        w("")
        for t in tips:
            w(f"**{t['tip_en']}** — {t['detail_en']}")
            w("")

    # ── Rejection Handling (if present) ──
    rejection = form.get("rejection_handling", {})
    if rejection:
        w("---")
        w("")
        w("## If Your Application Is Rejected")
        w("")
        if rejection.get("explanation_en"):
            w(rejection["explanation_en"])
            w("")
        for reason in rejection.get("common_reasons", []):
            w(f"- {reason}")
        w("")
        if rejection.get("next_steps"):
            w("**What to do next:**")
            w("")
            for step in rejection["next_steps"]:
                w(f"- {step}")
            w("")

    # ── Middle Name Guide (if present) ──
    mn = form.get("middle_name_guide", {})
    if mn:
        w("---")
        w("")
        w("## Middle Name Problems")
        w("")
        if mn.get("explanation_en"):
            w(mn["explanation_en"])
            w("")
        for opt in mn.get("options", []):
            w(f"**{opt['option_en']}:** {opt.get('example','')} — *{opt.get('note','')}*")
            w("")

    # ── Municipality-Specific Notes ──
    form_local = muni.get("forms_available", {}).get(form_id, {})
    local_tips = form_local.get("local_tips", [])
    offices = muni.get("offices", [])

    if local_tips or offices:
        w("---")
        w("")
        w(f"## {muni_name_en}-Specific Notes")
        w("")

        if local_tips:
            for tip in local_tips:
                w(f"- {tip}")
            w("")

        if form_local.get("has_english_version"):
            w("✅ **This ward offers an English version of this form.**")
            w("")

        if offices:
            w("### Office Locations")
            w("")
            w("| Office | Address | Phone | English? |")
            w("|--------|---------|-------|----------|")
            for o in offices:
                eng = "✅" if o.get("english_support") == True else ("Partial" if o.get("english_support") == "partial" else "—")
                main = " **(Main)**" if o.get("is_main") else ""
                w(f"| {o['name_en']}{main} | {o.get('address_en', '')} | {o.get('phone', '')} | {eng} |")
            w("")

    # ── Footer ──
    w("---")
    w("")
    w(f"*Generated from [japan-forms](https://github.com/deep-japan/japan-forms) data on {date.today().isoformat()}.*")
    w(f"*Found an error? [Submit an issue](https://github.com/deep-japan/japan-forms/issues/new?template=translation_error.yml) or open a PR.*")
    w("")
    w(f"*This is a community resource and not an official government document. Always confirm with your local municipal office.*")

    # Write
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  ✅ Markdown: {out_path.relative_to(BASE_DIR)}")
    return out_path


# ═══════════════════════════════════════════
# PDF GENERATOR
# ═══════════════════════════════════════════

def generate_pdf(form_id, municipality_id, fields, form, muni):
    """Generate a bilingual PDF for a specific form + municipality combo."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.colors import HexColor
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        print("  ⚠ reportlab not installed — skipping PDF generation")
        print("    Install with: pip install reportlab")
        return None

    # Register Japanese font
    font_path = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"
    if not os.path.exists(font_path):
        # Fallback: try common locations
        for p in ["/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
                   "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"]:
            if os.path.exists(p):
                font_path = p
                break
        else:
            print("  ⚠ No Japanese font found — skipping PDF generation")
            return None

    try:
        pdfmetrics.registerFont(TTFont('JPFont', font_path))
    except Exception:
        print(f"  ⚠ Could not register font {font_path} — skipping PDF")
        return None

    out_dir = OUTPUT_DIR / "pdf" / municipality_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{form_id}.pdf"

    WIDTH, HEIGHT = A4
    NAVY = HexColor("#1a2744")
    BLUE = HexColor("#2980b9")
    GRAY = HexColor("#555555")
    LIGHT = HexColor("#ebf5fb")
    RED = HexColor("#c0392b")
    WHITE = HexColor("#ffffff")
    LGRAY = HexColor("#f2f3f4")

    muni_en = muni["names"]["en"]
    muni_ja = muni["names"]["ja"]
    form_en = form["names"]["en"]
    form_ja = form["names"]["ja"]

    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle(f"{form_en} ({form_ja}) — {muni_en}")
    c.setAuthor("Deep Japan / japan-forms")

    margin = 20

    def draw_header(page, total):
        c.setFillColor(NAVY)
        c.rect(0, HEIGHT - 40, WIDTH, 40, fill=True, stroke=False)
        c.setFillColor(WHITE)
        c.setFont('JPFont', 12)
        c.drawString(15, HEIGHT - 27, f"{form_ja}  {form_en}")
        c.setFont('JPFont', 7)
        c.setFillColor(HexColor("#aabbcc"))
        c.drawString(15, HEIGHT - 37, f"Deep Japan  ·  {muni_en} ({muni_ja})  ·  Bilingual Guide")
        c.drawRightString(WIDTH - 15, HEIGHT - 37, f"Page {page}/{total}")
        c.setStrokeColor(RED)
        c.setLineWidth(2)
        c.line(0, HEIGHT - 41, WIDTH, HEIGHT - 41)

    def draw_footer():
        c.setFont('JPFont', 5.5)
        c.setFillColor(HexColor("#bdc3c7"))
        c.drawString(15, 10, f"Generated {date.today().isoformat()} from github.com/deep-japan/japan-forms  |  Not an official government document")

    # ═══ PAGE 1: Field Translation Table ═══
    draw_header(1, 2)
    draw_footer()

    y = HEIGHT - 60
    row_h = 16

    for section in form.get("sections", []):
        # Section header
        if y < 100:
            c.showPage()
            draw_header(2, 2)
            draw_footer()
            y = HEIGHT - 60

        c.setFillColor(NAVY)
        c.setFont('JPFont', 10)
        c.drawString(margin, y, f"§{section['section_number']}  {section['title_ja']}  —  {section['title_en']}")
        y -= 5

        # Column headers
        y -= row_h
        col_defs = [
            ("Japanese", 80),
            ("Reading", 80),
            ("English", 160),
            ("Req", 28),
            ("Tip / Notes", 200),
        ]
        cx = margin
        for hdr, w in col_defs:
            c.setFillColor(NAVY)
            c.rect(cx, y, w, row_h, fill=True, stroke=False)
            c.setFillColor(WHITE)
            c.setFont('JPFont', 6.5)
            c.drawString(cx + 3, y + 4, hdr)
            cx += w

        # Field rows
        for field_ref in section.get("fields", []):
            y -= row_h
            if y < 40:
                c.showPage()
                draw_header(2, 2)
                draw_footer()
                y = HEIGHT - 60

            fid = field_ref["field_id"]
            field = fields.get(fid, {})
            if not field:
                continue

            kanji = field.get("kanji", fid)
            reading = field.get("romaji", "")
            en = field_ref.get("context_label_en", field.get("english", ""))
            req = "✓" if field_ref.get("required") else ""
            tip = field_ref.get("note_en", field.get("tip_en", ""))

            row_data = [kanji, reading, en, req, tip]
            bg = LIGHT if (section["fields"].index(field_ref) % 2 == 0) else WHITE

            cx = margin
            for i, ((_, w), text) in enumerate(zip(col_defs, row_data)):
                c.setFillColor(bg)
                c.rect(cx, y, w, row_h, fill=True, stroke=False)
                c.setStrokeColor(HexColor("#e0e0e0"))
                c.setLineWidth(0.3)
                c.line(cx, y, cx + w, y)

                if i == 0:
                    c.setFillColor(NAVY)
                    c.setFont('JPFont', 8)
                elif i == 3:
                    c.setFillColor(BLUE)
                    c.setFont('JPFont', 7)
                else:
                    c.setFillColor(GRAY)
                    c.setFont('JPFont', 6.5)

                # Truncate if too long
                max_chars = int(w / 4.5)
                display = text[:max_chars] + "…" if len(text) > max_chars else text
                c.drawString(cx + 3, y + 4, display)
                cx += w

        y -= 10

    # ═══ PAGE 2: Phrases & Checklist ═══
    c.showPage()
    draw_header(2, 2)
    draw_footer()
    y = HEIGHT - 60

    # Counter phrases
    phrases = form.get("counter_phrases", [])
    if phrases:
        c.setFillColor(NAVY)
        c.setFont('JPFont', 11)
        c.drawString(margin, y, "Useful Phrases — Print & bring to the ward office")
        y -= 8

        for p in phrases:
            y -= 42
            if y < 40:
                break

            c.setFillColor(BLUE)
            c.setFont('JPFont', 6.5)
            c.drawString(margin, y + 30, p["situation_en"].upper())

            c.setFillColor(HexColor("#fff3e0"))
            c.rect(margin, y, WIDTH - 2 * margin, 28, fill=True, stroke=False)
            c.setStrokeColor(RED)
            c.setLineWidth(1.5)
            c.line(margin, y, margin, y + 28)

            c.setFillColor(NAVY)
            c.setFont('JPFont', 10)
            c.drawString(margin + 6, y + 15, p["ja"])
            c.setFillColor(GRAY)
            c.setFont('JPFont', 6)
            c.drawString(margin + 6, y + 7, p["romaji"])
            c.setFillColor(BLUE)
            c.setFont('JPFont', 6.5)
            c.drawString(margin + 6, y + 0, p["en"])

    # CTA
    y -= 25
    if y > 30:
        c.setFillColor(NAVY)
        c.rect(margin, y - 5, WIDTH - 2 * margin, 22, fill=True, stroke=False)
        c.setFillColor(WHITE)
        c.setFont('JPFont', 7)
        c.drawCentredString(WIDTH / 2, y + 3, f"Deep Japan  ·  deepjapan.io  ·  Free guides + in-person assistance in {muni_en}")

    c.save()
    print(f"  ✅ PDF: {out_path.relative_to(BASE_DIR)}")
    return out_path


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate bilingual form guides from structured data")
    parser.add_argument("--municipality", help="Municipality ID (e.g. tokyo/minato-ku). Default: all.")
    parser.add_argument("--form", help="Form ID (e.g. residence_registration). Default: all.")
    parser.add_argument("--format", choices=["markdown", "pdf", "both"], default="both",
                        help="Output format. Default: both.")
    args = parser.parse_args()

    # Load field dictionary
    print("Loading field dictionary...")
    fields = load_field_dictionary()
    print(f"  {len(fields)} fields loaded")

    # Determine what to generate
    municipalities = [args.municipality] if args.municipality else find_all_municipalities()
    form_ids = [args.form] if args.form else find_all_forms()

    print(f"\nGenerating for {len(municipalities)} municipality(ies) × {len(form_ids)} form(s)")
    print(f"Output formats: {args.format}\n")

    generated = {"markdown": 0, "pdf": 0}

    for muni_id in municipalities:
        muni = load_municipality(muni_id)
        if not muni:
            continue

        print(f"📍 {muni['names']['en']} ({muni['names']['ja']})")

        for form_id in form_ids:
            form = load_form_template(form_id)
            if not form:
                continue

            # Check if municipality has this form
            if form_id not in muni.get("forms_available", {}):
                print(f"  ⏭ {form_id} — not listed for this municipality, generating with defaults")

            print(f"  📝 {form['names']['en']}")

            if args.format in ("markdown", "both"):
                result = generate_markdown(form_id, muni_id, fields, form, muni)
                if result:
                    generated["markdown"] += 1

            if args.format in ("pdf", "both"):
                result = generate_pdf(form_id, muni_id, fields, form, muni)
                if result:
                    generated["pdf"] += 1

    print(f"\n✅ Done! Generated {generated['markdown']} markdown + {generated['pdf']} PDF files")
    print(f"   Output: {OUTPUT_DIR.relative_to(BASE_DIR)}/")


if __name__ == "__main__":
    main()
