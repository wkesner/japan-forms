#!/usr/bin/env python3
"""
Automated PDF translation pipeline for japan-forms.

Given a Japanese government PDF form, this script:
1. Extracts all text + positions using pdfplumber
2. Clusters characters into logical field groups by proximity
3. Translates via dictionary lookup -> fragment matching -> Claude LLM
4. Renders cropped form sections as images using pdf2image
5. Generates a multi-page bilingual PDF guide using reportlab

Usage:
    python pipeline.py input.pdf                                # Process single PDF
    python pipeline.py input.pdf -o output_dir/                 # Custom output directory
    python pipeline.py input.pdf --no-llm                       # Dictionary-only mode
    python pipeline.py input.pdf --form residence_registration  # Specify form template
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

# ── Paths ──
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
FIELDS_PATH = DATA_DIR / "fields" / "dictionary.json"
FORMS_DIR = DATA_DIR / "forms"
OUTPUT_DIR = BASE_DIR / "output" / "guides"
CACHE_PATH = BASE_DIR / "translations_cache.json"

# ── Form zones for 住民異動届 (pdfplumber y-coordinates) ──
DEFAULT_ZONES = [
    {"name": "Header & Dates",   "title_en": "Header & Dates",   "title_ja": "タイトル・届出日",    "y_min": 0,   "y_max": 65},
    {"name": "Who Is Filing",    "title_en": "Who Is Filing",     "title_ja": "届出人情報",          "y_min": 65,  "y_max": 145},
    {"name": "Addresses",        "title_en": "Addresses",         "title_ja": "住所",                "y_min": 140, "y_max": 255},
    {"name": "Person Table",     "title_en": "Person Table",      "title_ja": "異動者",              "y_min": 250, "y_max": 500},
    {"name": "Staff Section",    "title_en": "Staff Section",     "title_ja": "職員記入欄",          "y_min": 500, "y_max": 600},
]


# ═══════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_field_dictionary():
    """Load the universal field dictionary. Returns dict keyed by field_id."""
    data = load_json(FIELDS_PATH)
    return data["fields"]


def load_form_template(form_id):
    """Load a form template by ID. Returns None if not found."""
    path = FORMS_DIR / f"{form_id}.json"
    if not path.exists():
        return None
    return load_json(path)


def load_translation_cache():
    """Load translation cache from disk. Returns empty dict if no file."""
    if CACHE_PATH.exists():
        try:
            return load_json(CACHE_PATH)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_translation_cache(cache):
    """Write translation cache to disk."""
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════
# FONT REGISTRATION
# ═══════════════════════════════════════════

_font_registered = False


def register_fonts():
    """Register Japanese font for reportlab. Returns True if successful."""
    global _font_registered
    if _font_registered:
        return True

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Try Windows MS Gothic first, then Linux IPA Gothic
    candidates = [
        ("C:/Windows/Fonts/msgothic.ttc", 0),  # Windows MS Gothic (TTC index 0)
        ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", None),
        ("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", None),
    ]

    for font_path, subfont_index in candidates:
        if os.path.exists(font_path):
            try:
                if subfont_index is not None:
                    pdfmetrics.registerFont(TTFont("JPFont", font_path, subfontIndex=subfont_index))
                else:
                    pdfmetrics.registerFont(TTFont("JPFont", font_path))
                _font_registered = True
                return True
            except Exception as e:
                print(f"  WARN: Could not register font {font_path}: {e}")
                continue

    print("  WARN: No Japanese font found. Japanese text in guide may not render correctly.")
    print("    Windows: msgothic.ttc should be at C:/Windows/Fonts/")
    print("    Linux: install fonts-ipafont-gothic")
    return False


# ═══════════════════════════════════════════
# TEXT EXTRACTION
# ═══════════════════════════════════════════

def extract_text(pdf_path, page_num=0):
    """
    Extract all characters with positions from a PDF page using pdfplumber.

    Returns list of dicts: {text, x0, y0, x1, y1, top, bottom}
    where y values are in pdfplumber coordinates (top of page = 0).
    """
    import pdfplumber

    chars = []
    with pdfplumber.open(pdf_path) as pdf:
        if page_num >= len(pdf.pages):
            print(f"  WARN: Page {page_num} not found in {pdf_path} (has {len(pdf.pages)} pages)")
            return chars

        page = pdf.pages[page_num]
        for char in page.chars:
            chars.append({
                "text": char.get("text", ""),
                "x0": float(char.get("x0", 0)),
                "y0": float(char.get("top", 0)),
                "x1": float(char.get("x1", 0)),
                "y1": float(char.get("bottom", 0)),
                "top": float(char.get("top", 0)),
                "size": float(char.get("size", 10)),
            })

    return chars


def cluster_fields(chars, y_threshold=3.0, x_gap_threshold=15.0):
    """
    Cluster characters into logical text runs by proximity.

    Groups characters that are on the same line (similar y) and close horizontally.
    Returns list of field groups: {text, x0, y0, x1, y1, chars}
    """
    if not chars:
        return []

    # Sort by y (top), then by x
    sorted_chars = sorted(chars, key=lambda c: (c["y0"], c["x0"]))

    # Group into lines by y-proximity
    lines = []
    current_line = [sorted_chars[0]]

    for ch in sorted_chars[1:]:
        if abs(ch["y0"] - current_line[-1]["y0"]) <= y_threshold:
            current_line.append(ch)
        else:
            lines.append(current_line)
            current_line = [ch]
    lines.append(current_line)

    # Within each line, split into field groups by x-gap
    fields = []
    for line in lines:
        line_sorted = sorted(line, key=lambda c: c["x0"])
        group = [line_sorted[0]]

        for ch in line_sorted[1:]:
            # If gap between previous char end and this char start is large, split
            prev_end = group[-1]["x1"]
            if ch["x0"] - prev_end > x_gap_threshold:
                fields.append(_make_field_group(group))
                group = [ch]
            else:
                group.append(ch)
        fields.append(_make_field_group(group))

    return fields


def _make_field_group(char_list):
    """Combine a list of character dicts into a single field group."""
    text = "".join(c["text"] for c in char_list)
    return {
        "text": text,
        "x0": min(c["x0"] for c in char_list),
        "y0": min(c["y0"] for c in char_list),
        "x1": max(c["x1"] for c in char_list),
        "y1": max(c["y1"] for c in char_list),
        "char_count": len(char_list),
    }


def fields_in_zone(fields, zone):
    """Return field groups whose y-center falls within a zone's y-range."""
    result = []
    for f in fields:
        y_center = (f["y0"] + f["y1"]) / 2
        if zone["y_min"] <= y_center <= zone["y_max"]:
            result.append(f)
    return result


# ═══════════════════════════════════════════
# TRANSLATION LAYER
# ═══════════════════════════════════════════

def dictionary_lookup(text, dictionary):
    """
    Look up text in the field dictionary.

    Tries exact kanji match first, then checks aliases.
    Returns dict {en, type, note} or None.
    """
    text = text.strip()
    if not text:
        return None

    for field_id, field in dictionary.items():
        # Exact kanji match
        if text == field.get("kanji", ""):
            return {
                "en": field["english"],
                "type": "dictionary",
                "note": field.get("tip_en", ""),
                "field_id": field_id,
            }
        # Check aliases
        for alias in field.get("aliases", []):
            if text == alias:
                return {
                    "en": field["english"],
                    "type": "dictionary",
                    "note": field.get("tip_en", ""),
                    "field_id": field_id,
                }
        # Check options
        for opt_key, opt_val in field.get("options", {}).items():
            if text == opt_key:
                return {
                    "en": opt_val["english"],
                    "type": "dictionary",
                    "note": f"Option for {field['english']}",
                    "field_id": field_id,
                }

    return None


def fragment_match(text, dictionary):
    """
    Try to match text as a combination of known dictionary terms.

    Handles compound labels like "新住所（方書）" by matching sub-parts.
    Returns dict {en, type, note} or None.
    """
    text = text.strip()
    if not text or len(text) < 2:
        return None

    # Try to find any dictionary kanji that is a substring
    matches = []
    for field_id, field in dictionary.items():
        kanji = field.get("kanji", "")
        if kanji and kanji in text:
            matches.append({
                "kanji": kanji,
                "english": field["english"],
                "field_id": field_id,
                "length": len(kanji),
            })

    if not matches:
        return None

    # Sort by length descending — prefer longest match
    matches.sort(key=lambda m: m["length"], reverse=True)

    # Build combined translation
    parts = [m["english"] for m in matches[:3]]  # limit to 3 parts
    combined = " / ".join(parts)

    return {
        "en": combined,
        "type": "fragment",
        "note": f"Matched: {', '.join(m['kanji'] for m in matches[:3])}",
    }


def llm_translate(text, use_llm=True):
    """
    Translate text using Claude Sonnet API.

    Returns dict {en, type, note} or None if LLM is disabled or fails.
    """
    if not use_llm:
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        print("  WARN: anthropic package not installed. Skipping LLM translation.")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Translate this Japanese government form label/text to English. "
                        f"Context: this appears on a municipal residence registration form (住民異動届).\n\n"
                        f"Text: {text}\n\n"
                        f"Reply in EXACTLY this format (two lines only):\n"
                        f"Translation: <english translation>\n"
                        f"Tip: <brief filling tip for a foreign resident, or N/A if it's just instructions/layout text>"
                    ),
                }
            ],
        )
        raw = message.content[0].text.strip()
        # Parse structured response
        translation = raw
        tip = ""
        for line in raw.split("\n"):
            line = line.strip()
            if line.lower().startswith("translation:"):
                translation = line.split(":", 1)[1].strip()
            elif line.lower().startswith("tip:"):
                tip = line.split(":", 1)[1].strip()
                if tip.lower() in ("n/a", "none", "n/a."):
                    tip = ""
        return {
            "en": translation,
            "type": "llm",
            "note": tip,
        }
    except Exception as e:
        print(f"  WARN: LLM translation failed for '{text}': {e}")
        return None


def translate_field(text, cache, dictionary, use_llm=True):
    """
    Translate a Japanese text field. Tries in order:
    1. Cache hit (by MD5 hash)
    2. Dictionary exact match
    3. Fragment matching
    4. LLM translation (if enabled)

    Updates cache in-place. Returns dict {en, type, note}.
    """
    text = text.strip()
    if not text:
        return {"en": "", "type": "empty", "note": ""}

    # Skip if clearly not Japanese (pure numbers, punctuation, etc.)
    if all(c.isascii() and not c.isalpha() for c in text):
        return {"en": text, "type": "passthrough", "note": "ASCII/numeric"}

    # Check cache
    cache_key = hashlib.md5(text.encode("utf-8")).hexdigest()
    if cache_key in cache:
        return cache[cache_key]

    # Dictionary lookup
    result = dictionary_lookup(text, dictionary)
    if result:
        cache[cache_key] = result
        return result

    # Fragment matching
    result = fragment_match(text, dictionary)
    if result:
        cache[cache_key] = result
        return result

    # LLM fallback
    result = llm_translate(text, use_llm=use_llm)
    if result:
        cache[cache_key] = result
        return result

    # No translation found
    return {"en": f"[{text}]", "type": "unknown", "note": "No translation available"}


# ═══════════════════════════════════════════
# IMAGE CROPPING
# ═══════════════════════════════════════════

def _find_poppler_path():
    """Find poppler binaries path on Windows. Returns path string or None."""
    if sys.platform != "win32":
        return None

    # Common install locations on Windows
    home = Path.home()
    candidates = [
        Path(os.environ.get("POPPLER_PATH", "")) if os.environ.get("POPPLER_PATH") else None,
        home / "poppler" / "poppler-25.12.0" / "Library" / "bin",
        Path("C:/Program Files/poppler/Library/bin"),
        Path("C:/Program Files (x86)/poppler/Library/bin"),
        Path("C:/poppler/Library/bin"),
        Path("C:/poppler/bin"),
        Path(os.environ.get("LOCALAPPDATA", ""), "poppler/Library/bin"),
    ]

    # Also scan ~/poppler for any version
    poppler_dir = home / "poppler"
    if poppler_dir.exists():
        for sub in sorted(poppler_dir.iterdir(), reverse=True):
            bin_path = sub / "Library" / "bin"
            if bin_path.exists():
                candidates.insert(1, bin_path)

    # Also check PATH
    for p in os.environ.get("PATH", "").split(";"):
        if "poppler" in p.lower():
            candidates.append(Path(p))

    for p in candidates:
        if p and p.exists() and (p / "pdftoppm.exe").exists():
            return str(p)

    return None


def render_page_image(pdf_path, page_num=0, dpi=200):
    """
    Render a PDF page as a PIL Image using pdf2image.

    Returns PIL Image or None on failure.
    """
    try:
        from pdf2image import convert_from_path
    except ImportError:
        print("  WARN: pdf2image not installed. Skipping image rendering.")
        print("    Install with: pip install pdf2image")
        return None

    poppler_path = _find_poppler_path()
    kwargs = {"dpi": dpi, "first_page": page_num + 1, "last_page": page_num + 1}
    if poppler_path:
        kwargs["poppler_path"] = poppler_path

    try:
        images = convert_from_path(pdf_path, **kwargs)
        if images:
            return images[0]
    except Exception as e:
        print(f"  WARN: Could not render page image: {e}")
        if sys.platform == "win32" and "poppler" in str(e).lower():
            print("    Windows requires poppler. Install from:")
            print("    https://github.com/oschwartz10612/poppler-windows/releases")
            print("    Then set POPPLER_PATH environment variable to the bin/ directory.")
    return None


def crop_section(page_image, zone, page_height_pts, image_height_px):
    """
    Crop a section from a rendered page image based on zone y-coordinates.

    zone: dict with y_min, y_max (in pdfplumber points, top=0)
    page_height_pts: height of the PDF page in points
    image_height_px: height of the rendered image in pixels

    Returns cropped PIL Image.
    """
    if page_image is None:
        return None

    scale = image_height_px / page_height_pts
    y_top_px = int(zone["y_min"] * scale)
    y_bottom_px = int(zone["y_max"] * scale)

    # Add padding
    padding = 10
    y_top_px = max(0, y_top_px - padding)
    y_bottom_px = min(image_height_px, y_bottom_px + padding)

    return page_image.crop((0, y_top_px, page_image.width, y_bottom_px))


# ═══════════════════════════════════════════
# PDF GUIDE GENERATION
# ═══════════════════════════════════════════

def generate_guide(pdf_path, translations_by_zone, form_template, output_path,
                   page_image=None, page_height_pts=842, zones=None):
    """
    Generate the multi-page bilingual PDF guide.

    Pages:
    1. Original form (embedded untouched)
    2. Cover page — what to bring, common mistakes, what happens after
    3-7. Zoomed form sections — cropped image + translations
    8. Counter phrases
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    import io

    has_font = register_fonts()
    font_ja = "JPFont" if has_font else "Helvetica"
    font_en = "Helvetica"

    if zones is None:
        zones = DEFAULT_ZONES

    WIDTH, HEIGHT = A4
    NAVY = HexColor("#1a2744")
    BLUE = HexColor("#2980b9")
    GRAY = HexColor("#555555")
    LIGHT = HexColor("#ebf5fb")
    RED = HexColor("#c0392b")
    WHITE = HexColor("#ffffff")
    WARM_BG = HexColor("#fff3e0")
    LGRAY = HexColor("#f2f3f4")

    margin = 28
    form_name_en = "Residence Registration"
    form_name_ja = "住民異動届"
    if form_template:
        form_name_en = form_template.get("names", {}).get("en", form_name_en)
        form_name_ja = form_template.get("names", {}).get("ja", form_name_ja)

    # Count total pages
    section_count = len([z for z in zones if translations_by_zone.get(z["name"])])
    total_pages = 2 + section_count + 1  # original + cover + sections + phrases
    if not form_template:
        total_pages = 2 + section_count  # no cover/phrases without template

    c = canvas.Canvas(str(output_path), pagesize=A4)
    c.setTitle(f"{form_name_en} ({form_name_ja}) — Bilingual Guide")
    c.setAuthor("japan-forms")

    current_page = [0]

    def next_page():
        current_page[0] += 1

    def draw_header():
        c.setFillColor(NAVY)
        c.rect(0, HEIGHT - 40, WIDTH, 40, fill=True, stroke=False)
        c.setFillColor(WHITE)
        c.setFont(font_ja, 12)
        c.drawString(15, HEIGHT - 27, f"{form_name_ja}  {form_name_en}")
        c.setFont(font_en, 7)
        c.setFillColor(HexColor("#aabbcc"))
        c.drawString(15, HEIGHT - 37, f"japan-forms  ·  Bilingual Guide")
        c.drawRightString(WIDTH - 15, HEIGHT - 37, f"Page {current_page[0]}/{total_pages}")
        c.setStrokeColor(RED)
        c.setLineWidth(2)
        c.line(0, HEIGHT - 41, WIDTH, HEIGHT - 41)

    def draw_footer():
        c.setFont(font_en, 5.5)
        c.setFillColor(HexColor("#bdc3c7"))
        c.drawString(15, 10,
            f"Generated {date.today().isoformat()} from github.com/wkesner/japan-forms  |  Not an official government document")

    # ═══ PAGE 1: Original Form (embedded) ═══
    next_page()

    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(pdf_path)
        if reader.pages:
            # Save first page as temp PDF, then embed via reportlab
            writer = PdfWriter()
            writer.add_page(reader.pages[0])
            temp_buf = io.BytesIO()
            writer.write(temp_buf)
            temp_buf.seek(0)

            # Embed as full-page image instead (more reliable rendering)
            if page_image:
                draw_header()
                draw_footer()
                img_buf = io.BytesIO()
                page_image.save(img_buf, format="PNG")
                img_buf.seek(0)
                img_reader = ImageReader(img_buf)

                # Scale to fit within margins
                avail_w = WIDTH - 2 * margin
                avail_h = HEIGHT - 80  # header + footer
                img_w, img_h = page_image.size
                scale = min(avail_w / img_w, avail_h / img_h)
                draw_w = img_w * scale
                draw_h = img_h * scale
                x = (WIDTH - draw_w) / 2
                y = 25  # above footer

                c.drawImage(img_reader, x, y, width=draw_w, height=draw_h)
            else:
                # No image available — just add a note
                draw_header()
                draw_footer()
                c.setFont(font_en, 14)
                c.setFillColor(GRAY)
                c.drawCentredString(WIDTH / 2, HEIGHT / 2,
                    "Original form — print the source PDF separately")
    except Exception as e:
        draw_header()
        draw_footer()
        c.setFont(font_en, 12)
        c.setFillColor(GRAY)
        c.drawCentredString(WIDTH / 2, HEIGHT / 2, f"Could not embed original form: {e}")

    # ═══ PAGE 2: Cover Page ═══
    if form_template:
        c.showPage()
        next_page()
        draw_header()
        draw_footer()

        y = HEIGHT - 65

        # Title
        c.setFont(font_ja, 16)
        c.setFillColor(NAVY)
        c.drawString(margin, y, f"{form_name_ja}  —  {form_name_en}")
        y -= 25

        c.setFont(font_en, 9)
        c.setFillColor(GRAY)
        legal = form_template.get("legal_basis", {})
        if legal:
            c.drawString(margin, y, f"Deadline: {legal.get('deadline_description_en', 'N/A')}  |  Cost: Free  |  Penalty: {legal.get('penalty_en', 'N/A')}")
        y -= 25

        # ── What to Bring (scenarios) ──
        scenarios = form_template.get("scenarios", {})
        if scenarios:
            c.setFont(font_en, 11)
            c.setFillColor(NAVY)
            c.drawString(margin, y, "WHAT TO BRING")
            y -= 5

            c.setStrokeColor(RED)
            c.setLineWidth(1.5)
            c.line(margin, y, margin + 120, y)
            y -= 15

            for scenario_key, scenario in scenarios.items():
                if y < 100:
                    break
                c.setFont(font_en, 9)
                c.setFillColor(BLUE)
                c.drawString(margin, y, f">> {scenario['title_en']}")
                y -= 14

                for doc in scenario.get("documents_required", []):
                    if y < 60:
                        break
                    marker = "* " if doc["required"] else "  "
                    c.setFont(font_en, 7.5)
                    c.setFillColor(NAVY if doc["required"] else GRAY)
                    line = f"{marker}{doc['en']}"
                    cond = doc.get("condition_en", "")
                    if cond:
                        line += f"  ({cond})"
                    c.drawString(margin + 10, y, line)

                    c.setFont(font_ja, 7)
                    c.setFillColor(GRAY)
                    c.drawString(margin + 300, y, doc["ja"])
                    y -= 12
                y -= 6

        # ── Common Mistakes ──
        mistakes = form_template.get("common_mistakes", [])
        if mistakes and y > 120:
            y -= 10
            c.setFont(font_en, 11)
            c.setFillColor(NAVY)
            c.drawString(margin, y, "COMMON MISTAKES")
            y -= 5
            c.setStrokeColor(RED)
            c.setLineWidth(1.5)
            c.line(margin, y, margin + 140, y)
            y -= 15

            for m in mistakes[:4]:
                if y < 80:
                    break
                c.setFont(font_en, 7.5)
                c.setFillColor(RED)
                c.drawString(margin, y, f"X  {m['mistake_en']}")
                y -= 11
                c.setFillColor(GRAY)
                c.drawString(margin + 15, y, f"-> {m['fix_en']}")
                y -= 14

        # ── After Submission ──
        after = form_template.get("after_submission", [])
        if after and y > 80:
            y -= 10
            c.setFont(font_en, 11)
            c.setFillColor(NAVY)
            c.drawString(margin, y, "AFTER YOU SUBMIT")
            y -= 5
            c.setStrokeColor(RED)
            c.setLineWidth(1.5)
            c.line(margin, y, margin + 140, y)
            y -= 15

            for step in after[:5]:
                if y < 40:
                    break
                c.setFont(font_en, 7.5)
                c.setFillColor(NAVY)
                c.drawString(margin, y, f"{step['step']}.  {step['en']}")
                y -= 12

    # ═══ PAGES 3-7: Zoomed Form Sections ═══
    for zone in zones:
        zone_name = zone["name"]
        zone_translations = translations_by_zone.get(zone_name, [])
        if not zone_translations:
            continue

        c.showPage()
        next_page()
        draw_header()
        draw_footer()

        y = HEIGHT - 65

        # Zone title
        c.setFont(font_ja, 13)
        c.setFillColor(NAVY)
        c.drawString(margin, y, f"{zone['title_ja']}  —  {zone['title_en']}")
        y -= 8
        c.setStrokeColor(RED)
        c.setLineWidth(1.5)
        c.line(margin, y, WIDTH - margin, y)
        y -= 10

        # Cropped form image (left half, top portion)
        cropped = None
        if page_image:
            cropped = crop_section(page_image, zone, page_height_pts, page_image.height)

        if cropped:
            img_buf = io.BytesIO()
            cropped.save(img_buf, format="PNG")
            img_buf.seek(0)
            img_reader = ImageReader(img_buf)

            # Scale cropped image to fit left portion
            avail_w = WIDTH - 2 * margin
            avail_h = min(200, (HEIGHT - 120) * 0.4)
            img_w, img_h = cropped.size
            scale = min(avail_w / img_w, avail_h / img_h)
            draw_w = img_w * scale
            draw_h = img_h * scale

            # Draw light background behind image
            c.setFillColor(LGRAY)
            c.rect(margin - 2, y - draw_h - 4, draw_w + 4, draw_h + 4, fill=True, stroke=False)
            c.drawImage(img_reader, margin, y - draw_h, width=draw_w, height=draw_h)

            # Border
            c.setStrokeColor(HexColor("#cccccc"))
            c.setLineWidth(0.5)
            c.rect(margin - 2, y - draw_h - 4, draw_w + 4, draw_h + 4, fill=False, stroke=True)

            y -= draw_h + 20

        # Staff section — just show a note
        if zone_name == "Staff Section":
            c.setFont(font_en, 10)
            c.setFillColor(RED)
            c.drawString(margin, y, "DO NOT FILL IN — Office use only (職員記入欄)")
            y -= 20
            c.setFont(font_en, 8)
            c.setFillColor(GRAY)
            c.drawString(margin, y, "This section is completed by ward office staff after you submit the form.")
            continue

        # Translation table
        # Column headers
        col_defs = [
            ("Japanese", 110),
            ("English", 170),
            ("Type", 50),
            ("Tip / Notes", 200),
        ]
        row_h = 16

        cx = margin
        for hdr, w in col_defs:
            c.setFillColor(NAVY)
            c.rect(cx, y - row_h, w, row_h, fill=True, stroke=False)
            c.setFillColor(WHITE)
            c.setFont(font_en, 7)
            c.drawString(cx + 3, y - row_h + 4, hdr)
            cx += w
        y -= row_h

        # Translation rows
        for i, trans in enumerate(zone_translations):
            if y < 40:
                break

            y -= row_h
            bg = LIGHT if i % 2 == 0 else WHITE

            ja_text = trans.get("ja", "")
            en_text = trans.get("en", "")
            trans_type = trans.get("type", "")
            note = trans.get("note", "")

            row_data = [ja_text, en_text, trans_type, note]
            cx = margin
            for j, ((_, w), text) in enumerate(zip(col_defs, row_data)):
                c.setFillColor(bg)
                c.rect(cx, y, w, row_h, fill=True, stroke=False)

                if j == 0:
                    c.setFillColor(NAVY)
                    c.setFont(font_ja, 8)
                elif j == 2:
                    c.setFillColor(BLUE)
                    c.setFont(font_en, 6.5)
                else:
                    c.setFillColor(GRAY)
                    c.setFont(font_en, 6.5)

                # Truncate
                max_chars = int(w / 4.5)
                display = text[:max_chars] + "..." if len(text) > max_chars else text
                c.drawString(cx + 3, y + 4, display)
                cx += w

    # ═══ LAST PAGE: Counter Phrases ═══
    if form_template:
        phrases = form_template.get("counter_phrases", [])
        if phrases:
            c.showPage()
            next_page()
            draw_header()
            draw_footer()

            y = HEIGHT - 65

            c.setFont(font_en, 13)
            c.setFillColor(NAVY)
            c.drawString(margin, y, "COUNTER PHRASES")
            y -= 5

            c.setFont(font_en, 8)
            c.setFillColor(GRAY)
            c.drawString(margin, y, "Point and show these to ward office staff")
            y -= 5

            c.setStrokeColor(RED)
            c.setLineWidth(1.5)
            c.line(margin, y, margin + 200, y)
            y -= 15

            for p in phrases:
                if y < 60:
                    break

                # Situation label
                c.setFillColor(BLUE)
                c.setFont(font_en, 7)
                c.drawString(margin, y, p["situation_en"].upper())
                y -= 3

                # Phrase card
                card_h = 38
                c.setFillColor(WARM_BG)
                c.rect(margin, y - card_h, WIDTH - 2 * margin, card_h, fill=True, stroke=False)
                c.setStrokeColor(RED)
                c.setLineWidth(2)
                c.line(margin, y - card_h, margin, y)

                # Japanese (large)
                c.setFillColor(NAVY)
                c.setFont(font_ja, 12)
                c.drawString(margin + 8, y - 15, p["ja"])

                # Romaji
                c.setFillColor(GRAY)
                c.setFont(font_en, 6.5)
                c.drawString(margin + 8, y - 25, p.get("romaji", ""))

                # English
                c.setFillColor(BLUE)
                c.setFont(font_en, 7)
                c.drawString(margin + 8, y - 35, p["en"])

                y -= card_h + 12

    c.save()
    return output_path


# ═══════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════

def process_pdf(pdf_path, output_dir, form_id="residence_registration",
                use_llm=True, zones=None):
    """
    Main entry point: process a single PDF and generate a bilingual guide.

    Called by scraper.py --generate and by CLI.

    Args:
        pdf_path: Path to the input Japanese PDF
        output_dir: Directory for output guide PDF
        form_id: Form template ID (default: residence_registration)
        use_llm: Whether to use Claude API for unknown terms
        zones: List of zone dicts (default: DEFAULT_ZONES for 住民異動届)
    """
    pdf_path = str(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if zones is None:
        zones = DEFAULT_ZONES

    pdf_name = Path(pdf_path).stem
    output_path = output_dir / f"{pdf_name}_guide.pdf"

    print(f"  Pipeline: {Path(pdf_path).name}")

    # Load data
    dictionary = load_field_dictionary()
    form_template = load_form_template(form_id)
    cache = load_translation_cache()

    if not form_template:
        print(f"    WARN: Form template '{form_id}' not found. Generating without template content.")

    # Step 1: Extract text
    print(f"    Extracting text...")
    chars = extract_text(pdf_path)
    print(f"    Found {len(chars)} characters")

    if not chars:
        print(f"    WARN: No text found in PDF. May be image-only.")

    # Step 2: Cluster into fields
    fields = cluster_fields(chars)
    print(f"    Clustered into {len(fields)} field groups")

    # Step 3: Get page height for coordinate mapping
    page_height_pts = 842  # A4 default
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if pdf.pages:
                page_height_pts = float(pdf.pages[0].height)
    except Exception:
        pass

    # Step 4: Translate fields by zone
    print(f"    Translating fields...")
    translations_by_zone = {}
    dict_hits = 0
    frag_hits = 0
    llm_hits = 0
    unknown = 0

    for zone in zones:
        zone_fields = fields_in_zone(fields, zone)
        zone_translations = []

        for field in zone_fields:
            text = field["text"].strip()
            if not text or len(text) < 2:
                continue

            result = translate_field(text, cache, dictionary, use_llm=use_llm)
            zone_translations.append({
                "ja": text,
                "en": result.get("en", ""),
                "type": result.get("type", "unknown"),
                "note": result.get("note", ""),
                "x0": field["x0"],
                "y0": field["y0"],
            })

            t = result.get("type", "")
            if t == "dictionary":
                dict_hits += 1
            elif t == "fragment":
                frag_hits += 1
            elif t == "llm":
                llm_hits += 1
            elif t == "unknown":
                unknown += 1

        translations_by_zone[zone["name"]] = zone_translations

    total = dict_hits + frag_hits + llm_hits + unknown
    print(f"    Translations: {dict_hits} dictionary, {frag_hits} fragment, {llm_hits} LLM, {unknown} unknown (of {total})")

    # Save cache after translating
    save_translation_cache(cache)

    # Step 5: Render page image
    print(f"    Rendering page image...")
    page_image = render_page_image(pdf_path, page_num=0, dpi=200)
    if page_image:
        print(f"    Image: {page_image.size[0]}x{page_image.size[1]} px")
    else:
        print(f"    WARN: Could not render page image (poppler may not be installed)")

    # Step 6: Generate guide PDF
    print(f"    Generating guide PDF...")
    result = generate_guide(
        pdf_path=pdf_path,
        translations_by_zone=translations_by_zone,
        form_template=form_template,
        output_path=output_path,
        page_image=page_image,
        page_height_pts=page_height_pts,
        zones=zones,
    )

    if result:
        size_kb = output_path.stat().st_size / 1024
        print(f"    OK: {output_path.name} ({size_kb:.0f} KB)")
    else:
        print(f"    FAIL: Could not generate guide")

    return str(output_path) if result else None


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Translate Japanese government form PDFs into bilingual English/Japanese guides"
    )
    parser.add_argument("input", help="Path to input PDF file")
    parser.add_argument("-o", "--output", default=None,
                        help=f"Output directory (default: {OUTPUT_DIR.relative_to(BASE_DIR)})")
    parser.add_argument("--form", default="residence_registration",
                        help="Form template ID (default: residence_registration)")
    parser.add_argument("--no-llm", action="store_true",
                        help="Dictionary-only mode — no Claude API calls")
    parser.add_argument("--dpi", type=int, default=200,
                        help="DPI for page rendering (default: 200)")

    args = parser.parse_args()

    # Validate input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {args.input}")
        sys.exit(1)
    if not input_path.suffix.lower() == ".pdf":
        print(f"ERROR: Input must be a PDF file: {args.input}")
        sys.exit(1)

    # Output directory
    output_dir = Path(args.output) if args.output else OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    use_llm = not args.no_llm
    if use_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        print("NOTE: ANTHROPIC_API_KEY not set. Running in dictionary-only mode.")
        print("  Set the environment variable to enable LLM translations.")
        use_llm = False

    print(f"japan-forms translation pipeline")
    print(f"  Input:  {input_path}")
    print(f"  Output: {output_dir}")
    print(f"  Form:   {args.form}")
    print(f"  LLM:    {'enabled' if use_llm else 'disabled (dictionary-only)'}")
    print()

    result = process_pdf(
        pdf_path=str(input_path),
        output_dir=str(output_dir),
        form_id=args.form,
        use_llm=use_llm,
    )

    if result:
        print(f"\nDone! Guide saved to: {result}")
    else:
        print(f"\nFailed to generate guide.")
        sys.exit(1)


if __name__ == "__main__":
    main()
