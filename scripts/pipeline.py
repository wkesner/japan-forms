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
OUTPUT_DIR = BASE_DIR / "output" / "walkthroughs"
CACHE_PATH = BASE_DIR / "translations_cache.json"

# ── Form zones for 住民異動届 (pdfplumber y-coordinates) ──
# A4 page height is ~842 points. Extended to capture full page content.
DEFAULT_ZONES = [
    {"name": "Header & Dates",   "title_en": "Header & Dates",   "title_ja": "タイトル・届出日",    "y_min": 0,   "y_max": 65},
    {"name": "Who Is Filing",    "title_en": "Who Is Filing",     "title_ja": "届出人情報",          "y_min": 65,  "y_max": 145},
    {"name": "Addresses",        "title_en": "Addresses",         "title_ja": "住所",                "y_min": 140, "y_max": 255},
    {"name": "Person Table",     "title_en": "Person Table",      "title_ja": "異動者",              "y_min": 250, "y_max": 500},
    {"name": "ID & Documents",   "title_en": "ID & Documents",    "title_ja": "本人確認書類",        "y_min": 500, "y_max": 650},
    {"name": "Instructions",     "title_en": "Instructions",      "title_ja": "記入方法・注意事項",  "y_min": 650, "y_max": 750},
    {"name": "Footer & Notes",   "title_en": "Footer & Notes",    "title_ja": "備考・その他",        "y_min": 750, "y_max": 900},
]


def create_dynamic_zones_for_fields(fields, page_height_pts, max_fields_per_zone=15):
    """
    Create zones dynamically based on actual field positions.

    Used for OCR pages where predefined A4 zones don't apply.
    Groups fields by y-position into chunks of max_fields_per_zone.

    Args:
        fields: List of field dicts with y0, y1 coordinates
        page_height_pts: Page height in points
        max_fields_per_zone: Max fields per zone (for readable output)

    Returns:
        List of zone dicts with y_min, y_max based on actual field positions
    """
    if not fields:
        return []

    # Sort fields by y position
    sorted_fields = sorted(fields, key=lambda f: f.get("y0", 0))

    zones = []
    zone_num = 1

    # Group into chunks
    for i in range(0, len(sorted_fields), max_fields_per_zone):
        chunk = sorted_fields[i:i + max_fields_per_zone]
        if not chunk:
            continue

        # Get y bounds for this chunk
        y_min = min(f.get("y0", 0) for f in chunk)
        y_max = max(f.get("y1", f.get("y0", 0) + 20) for f in chunk)

        # Add padding
        y_min = max(0, y_min - 15)
        y_max = min(page_height_pts, y_max + 15)

        zones.append({
            "name": f"Section {zone_num}",
            "title_en": f"Section {zone_num}",
            "title_ja": f"セクション {zone_num}",
            "y_min": y_min,
            "y_max": y_max,
        })
        zone_num += 1

    return zones


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

    # Try Windows MS Gothic, macOS Arial Unicode, then Linux IPA Gothic
    candidates = [
        ("C:/Windows/Fonts/msgothic.ttc", 0),  # Windows MS Gothic (TTC index 0)
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", None),  # macOS Arial Unicode
        ("/System/Library/Fonts/STHeiti Medium.ttc", 0),  # macOS STHeiti fallback
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

def classify_pdf_pages(pdf_path, min_chars=50):
    """
    Classify each page of a PDF as 'text' or 'image' based on extractable
    character count.

    Args:
        pdf_path: Path to PDF file
        min_chars: Minimum extractable characters to consider a page text-based

    Returns:
        List of dicts: [{"page": 0, "type": "text"|"image", "char_count": 152}, ...]
    """
    import pdfplumber

    results = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                char_count = len(page.chars)
                page_type = "text" if char_count >= min_chars else "image"
                results.append({
                    "page": i,
                    "type": page_type,
                    "char_count": char_count,
                    "width_pts": float(page.width),
                    "height_pts": float(page.height),
                })
    except Exception as e:
        print(f"  WARN: Could not classify PDF: {e}")

    return results


def find_best_page(pdf_path, min_chars=50):
    """
    Find the page with the most extractable text content.

    For multi-page PDFs where page 1 might be instructions or mostly blank,
    this finds the page that likely contains the actual form.

    Returns (page_num, char_count) tuple.
    """
    import pdfplumber

    best_page = 0
    best_count = 0

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            char_count = len(page.chars)
            if char_count > best_count:
                best_count = char_count
                best_page = i

    # If page 0 has very few chars but another page has more, use that
    if best_page != 0 and best_count > min_chars:
        return best_page, best_count

    return 0, best_count


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


def deduplicate_fields(fields, position_threshold=15):
    """Remove duplicate fields that occupy similar positions.

    If two fields have similar x0/y0 coordinates (within threshold),
    keep only the one with more characters (likely more complete).
    """
    if not fields:
        return []

    # Sort by position for consistent ordering
    sorted_fields = sorted(fields, key=lambda f: (f["y0"], f["x0"]))

    kept = []
    for field in sorted_fields:
        is_duplicate = False
        for i, existing in enumerate(kept):
            # Check if positions are very close
            if (abs(field["x0"] - existing["x0"]) < position_threshold and
                abs(field["y0"] - existing["y0"]) < position_threshold):
                # Keep the one with more characters
                if field["char_count"] > existing["char_count"]:
                    kept[i] = field
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(field)

    return kept


def fields_in_zone(fields, zone, padding=10):
    """Return field groups whose y-center falls within a zone's y-range.

    Args:
        padding: Extra points to add to zone boundaries to catch edge cases.

    If zone has a 'page' attribute, only fields from that page are considered.
    """
    result = []
    y_min = zone["y_min"] - padding
    y_max = zone["y_max"] + padding
    zone_page = zone.get("page")  # None for non-page-specific zones

    for f in fields:
        # If zone is page-specific, skip fields from other pages
        if zone_page is not None and f.get("page") != zone_page:
            continue

        y_center = (f["y0"] + f["y1"]) / 2
        if y_min <= y_center <= y_max:
            result.append(f)
    return result


def collect_unassigned_fields(fields, zones, padding=10):
    """Find fields that don't fall into any defined zone.

    Returns a synthetic zone dict and list of unassigned fields.
    Useful for capturing content outside standard zone definitions.
    """
    assigned = set()
    for zone in zones:
        y_min = zone["y_min"] - padding
        y_max = zone["y_max"] + padding
        for i, f in enumerate(fields):
            y_center = (f["y0"] + f["y1"]) / 2
            if y_min <= y_center <= y_max:
                assigned.add(i)

    unassigned = [f for i, f in enumerate(fields) if i not in assigned]
    if not unassigned:
        return None, []

    # Create a catch-all zone
    y_coords = [f["y0"] for f in unassigned] + [f["y1"] for f in unassigned]
    catch_zone = {
        "name": "Other Fields",
        "title_en": "Other Fields",
        "title_ja": "その他",
        "y_min": min(y_coords) - 5,
        "y_max": max(y_coords) + 5,
    }
    return catch_zone, unassigned


def ocr_with_vision(page_image, page_height_pts=842):
    """
    DEPRECATED: Use ocr_extract_translate_locate() instead.

    This function tries to extract text AND positions in one shot,
    which breaks for non-A4 aspect ratios.

    Use Claude vision to OCR text from a scanned PDF page image.

    Returns list of field groups similar to cluster_fields output.
    Falls back gracefully if API unavailable.
    """
    if page_image is None:
        return []

    try:
        import anthropic
        import base64
        import json
        import io
    except ImportError:
        print("  WARN: anthropic package not installed. Skipping OCR.")
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []

    # Convert image to base64
    img_buf = io.BytesIO()
    page_image.save(img_buf, format="PNG")
    img_buf.seek(0)
    img_b64 = base64.b64encode(img_buf.getvalue()).decode("utf-8")

    # Scale factor: image pixels to PDF points
    img_height = page_image.size[1]
    scale = page_height_pts / img_height

    client = anthropic.Anthropic(api_key=api_key)

    try:
        print("    OCR: Extracting text from scanned form...")
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": """This is a Japanese government form. Extract ALL Japanese text you can see, with approximate positions.

Return a JSON array where each item has:
- "text": the Japanese text (field labels, not filled-in values)
- "y_pct": approximate vertical position as percentage from top (0-100)
- "x_pct": approximate horizontal position as percentage from left (0-100)

Focus on form field labels like: 届出人, 氏名, 住所, 転出先, 届出日, 世帯主, 続柄, 生年月日, etc.
Skip decorative text, page numbers, and pre-filled example values.

Return ONLY the JSON array, no other text."""
                    }
                ],
            }],
        )

        # Parse response
        text = response.content[0].text.strip()
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        items = json.loads(text)

        # Convert to field groups format
        fields = []
        img_width, img_height = page_image.size

        for item in items:
            if not item.get("text"):
                continue

            # Convert percentages to approximate PDF coordinates
            y_pct = float(item.get("y_pct", 50))
            x_pct = float(item.get("x_pct", 50))

            # Estimate field bounds (approximate)
            y0 = (y_pct / 100) * page_height_pts
            x0 = (x_pct / 100) * (page_height_pts * img_width / img_height)  # Approximate width

            fields.append({
                "text": item["text"],
                "x0": x0,
                "y0": y0,
                "x1": x0 + 50,  # Approximate width
                "y1": y0 + 12,  # Approximate height
                "char_count": len(item["text"]),
            })

        print(f"    OCR: Found {len(fields)} text fields")
        return fields

    except Exception as e:
        print(f"  WARN: OCR failed: {e}")
        return []


def ocr_extract_translate_locate(
    page_image,
    page_width_pts=595,
    page_height_pts=842,
    dictionary=None,
    cache=None,
    use_llm=True
):
    """
    New OCR workflow: Extract text+positions → Translate.

    Uses a single Vision API call to get both text and positions,
    then translates. Works with any aspect ratio.

    Args:
        page_image: PIL Image of the page
        page_width_pts: PDF page width in points
        page_height_pts: PDF page height in points
        dictionary: Field dictionary for translation
        cache: Translation cache
        use_llm: Whether to use LLM for unknown terms

    Returns:
        Tuple of (fields, translation_stats)
        fields: List of field dicts with text, positions, and translations
        translation_stats: Dict with dict_hits, frag_hits, llm_hits, unknown counts
    """
    if page_image is None:
        return [], {"dict_hits": 0, "frag_hits": 0, "llm_hits": 0, "unknown": 0}

    # Import OCR module
    try:
        from ocr import extract_text_from_image
    except ImportError:
        # Try relative import
        import sys
        scripts_dir = Path(__file__).parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from ocr import extract_text_from_image

    stats = {"dict_hits": 0, "frag_hits": 0, "llm_hits": 0, "unknown": 0}

    # Step 1: Extract Japanese text WITH positions (easyOCR + dictionary + Vision)
    print("    OCR: Extracting text and positions...")
    items = extract_text_from_image(page_image, include_positions=True,
                                    dictionary=dictionary)

    if not items:
        print("    OCR: No text found")
        return [], stats

    print(f"    OCR: Found {len(items)} text items with positions")

    # Step 2: Translate and build fields
    print("    OCR: Translating...")
    fields = []

    for item in items:
        ja_text = item.get("text", "").strip()
        if not ja_text or len(ja_text) < 2:
            continue

        # Convert percentage positions to PDF points
        x_pct = item.get("x_pct", 50)
        y_pct = item.get("y_pct", 50)
        w_pct = item.get("w_pct", 5)  # Width percentage
        h_pct = item.get("h_pct", 2)  # Height percentage

        # Calculate bounding box from center + size
        width_pts = (w_pct / 100) * page_width_pts
        height_pts = (h_pct / 100) * page_height_pts
        x_center = (x_pct / 100) * page_width_pts
        y_center = (y_pct / 100) * page_height_pts

        x0 = x_center - width_pts / 2
        y0 = y_center - height_pts / 2
        x1 = x_center + width_pts / 2
        y1 = y_center + height_pts / 2

        # Translate
        result = translate_field(ja_text, cache, dictionary, use_llm=use_llm)

        # Track stats
        t = result.get("type", "")
        if t == "dictionary":
            stats["dict_hits"] += 1
        elif t == "fragment":
            stats["frag_hits"] += 1
        elif t == "llm":
            stats["llm_hits"] += 1
        elif t == "unknown":
            stats["unknown"] += 1

        fields.append({
            "text": ja_text,
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "char_count": len(ja_text),
            "translation": {
                "ja": ja_text,
                "en": result.get("en", ""),
                "type": result.get("type", "unknown"),
                "note": result.get("note", ""),
            },
        })

    print(f"    OCR: Processed {len(fields)} fields")
    return fields, stats


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

    # Add padding to prevent text cutoff at zone boundaries
    padding = 20
    y_top_px = max(0, y_top_px - padding)
    y_bottom_px = min(image_height_px, y_bottom_px + padding)

    # Check for invalid coordinates (can happen with multi-page offset)
    if y_bottom_px <= y_top_px or y_top_px >= image_height_px or y_bottom_px <= 0:
        return None

    return page_image.crop((0, y_top_px, page_image.width, y_bottom_px))


# ═══════════════════════════════════════════
# ANNOTATED IMAGE + EXPLANATIONS
# ═══════════════════════════════════════════

def annotate_section_image(cropped_image, zone_translations, zone, page_height_pts, image_height_px):
    """
    Draw numbered red circles in nearby white space with routed leader lines.

    Analyzes the cropped form image to find clear areas near each field,
    places numbered circles there, and draws leader lines routed to
    minimize crossing through text and form borders.

    Args:
        cropped_image: PIL Image (already cropped to zone)
        zone_translations: list of dicts with ja, en, x0, y0 (in PDF points)
        zone: zone dict with y_min, y_max
        page_height_pts: full PDF page height in points
        image_height_px: full rendered page image height in pixels

    Returns:
        (annotated_image, numbered_entries) where numbered_entries is
        [(number, translation_entry), ...] sorted by reading order.
    """
    from PIL import Image, ImageDraw, ImageFont
    import math

    if cropped_image is None or not zone_translations:
        return cropped_image, []

    radius = 12
    DARK_THRESHOLD = 230  # pixels darker than this are "content"

    scale = image_height_px / page_height_pts
    # Padding must match crop_section (20px) for accurate arrow placement
    crop_y_top_px = max(0, int(zone["y_min"] * scale) - 20)

    # Sort fields by (y0, x0) for natural reading order
    sorted_trans = sorted(zone_translations, key=lambda t: (t.get("y0", 0), t.get("x0", 0)))

    # --- Step 1: Build occupancy reference from grayscale ---
    gray = cropped_image.convert("L")
    img_w, img_h = gray.size
    gray_pixels = gray.load()

    # Work directly on the original image dimensions (no gutter)
    annotated = cropped_image.convert("RGBA")
    draw = ImageDraw.Draw(annotated)

    # Try to load a font for the numbers
    try:
        num_font = ImageFont.truetype("arial.ttf", 14)
    except (OSError, IOError):
        try:
            num_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except (OSError, IOError):
            num_font = ImageFont.load_default()

    # List of placed circle centers for overlap avoidance
    occupied_circles = []

    # --- Helper: check if a circle position is clear ---
    def _circle_clear(cx, cy):
        # Bounds check
        if cx - radius < 0 or cx + radius >= img_w:
            return False
        if cy - radius < 0 or cy + radius >= img_h:
            return False
        # No overlap with already-placed circles
        min_dist = 2 * radius + 4
        for (ox, oy) in occupied_circles:
            if math.hypot(cx - ox, cy - oy) < min_dist:
                return False
        # Sample pixels within circle area; reject if too many are dark
        dark_count = 0
        total_count = 0
        for sy in range(cy - radius, cy + radius + 1, 3):
            for sx in range(cx - radius, cx + radius + 1, 3):
                if (sx - cx) ** 2 + (sy - cy) ** 2 <= radius * radius:
                    total_count += 1
                    if gray_pixels[sx, sy] < DARK_THRESHOLD:
                        dark_count += 1
        if total_count == 0:
            return False
        return (dark_count / total_count) <= 0.12

    # --- Helper: measure dark-pixel fraction along a line segment ---
    def _line_darkness(x1, y1, x2, y2):
        n_samples = 80
        dark = 0
        total = 0
        dx = x2 - x1
        dy = y2 - y1
        for i in range(n_samples + 1):
            t = i / n_samples
            sx = int(x1 + dx * t)
            sy = int(y1 + dy * t)
            if 0 <= sx < img_w and 0 <= sy < img_h:
                total += 1
                if gray_pixels[sx, sy] < DARK_THRESHOLD:
                    dark += 1
        return dark / total if total > 0 else 1.0

    # --- Helper: pick best route (direct, L-horiz-first, L-vert-first) ---
    def _best_route(cx, cy, fx, fy):
        routes = [
            # Direct
            [(cx, cy), (fx, fy)],
            # L-shape horizontal-first: circle → (fx, cy) → field
            [(cx, cy), (fx, cy), (fx, fy)],
            # L-shape vertical-first: circle → (cx, fy) → field
            [(cx, cy), (cx, fy), (fx, fy)],
        ]
        best = None
        best_score = float("inf")
        for route in routes:
            score = 0.0
            for j in range(len(route) - 1):
                score += _line_darkness(route[j][0], route[j][1],
                                        route[j + 1][0], route[j + 1][1])
            if score < best_score:
                best_score = score
                best = route
        return best

    # --- Step 2 & 3 & 4: Place circles, route lines, draw ---
    # Search directions: upper-left, up, upper-right, left, right,
    #                    lower-left, down, lower-right
    directions = [
        (-1, -1), (0, -1), (1, -1),
        (-1, 0), (1, 0),
        (-1, 1), (0, 1), (1, 1),
    ]
    search_distances = [18, 28, 40, 55, 70, 90]

    numbered_entries = []

    # First pass: compute field pixel positions and place circles
    placements = []  # list of (cx, cy, field_x, field_y, entry)
    for idx, entry in enumerate(sorted_trans):
        # Use center of field if bounding box available, otherwise use x0/y0
        if "x1" in entry and "y1" in entry:
            # OCR fields with bounding box - use center
            field_y_pts = (entry.get("y0", 0) + entry.get("y1", 0)) / 2
            field_x_pts = (entry.get("x0", 0) + entry.get("x1", 0)) / 2
        else:
            # Text-based fields - use x0/y0 (top-left)
            field_y_pts = entry.get("y0", 0)
            field_x_pts = entry.get("x0", 0)

        field_y_px = field_y_pts * scale - crop_y_top_px + 4
        field_x_px = field_x_pts * scale
        # Clamp field position to image bounds
        field_x_px = int(max(2, min(img_w - 2, field_x_px)))
        field_y_px = int(max(2, min(img_h - 2, field_y_px)))

        # Search outward for clear white space
        placed = False
        for dist in search_distances:
            if placed:
                break
            for (ddx, ddy) in directions:
                cx = field_x_px + int(ddx * dist)
                cy = field_y_px + int(ddy * dist)
                if _circle_clear(cx, cy):
                    occupied_circles.append((cx, cy))
                    placements.append((cx, cy, field_x_px, field_y_px, entry))
                    placed = True
                    break
        if not placed:
            # Fallback: offset left of field
            cx = max(radius + 1, field_x_px - 40)
            cy = max(radius + 1, min(img_h - radius - 1, field_y_px))
            occupied_circles.append((cx, cy))
            placements.append((cx, cy, field_x_px, field_y_px, entry))

    # Second pass: draw in correct order (lines → dots → circles)
    # Draw all leader lines first
    for idx, (cx, cy, fx, fy, entry) in enumerate(placements):
        dist = math.hypot(cx - fx, cy - fy)
        if dist >= radius + 5:
            route = _best_route(cx, cy, fx, fy)
            for j in range(len(route) - 1):
                draw.line(
                    [route[j], route[j + 1]],
                    fill=(220, 50, 50, 90),
                    width=1,
                )

    # Draw all field anchor dots
    dot_r = 3
    for idx, (cx, cy, fx, fy, entry) in enumerate(placements):
        dist = math.hypot(cx - fx, cy - fy)
        if dist >= radius + 5:
            draw.ellipse(
                [fx - dot_r, fy - dot_r, fx + dot_r, fy + dot_r],
                fill=(220, 50, 50, 160),
            )

    # Draw all circles and numbers on top
    for idx, (cx, cy, fx, fy, entry) in enumerate(placements):
        number = idx + 1
        # Red filled circle
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(220, 50, 50, 255),
            outline=(180, 30, 30, 255),
            width=2,
        )
        # White number text centered in circle
        num_str = str(number)
        bbox = draw.textbbox((0, 0), num_str, font=num_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(
            (cx - tw / 2, cy - th / 2 - 1),
            num_str,
            fill=(255, 255, 255, 255),
            font=num_font,
        )
        numbered_entries.append((number, entry))

    # Convert back to RGB for compatibility
    annotated = annotated.convert("RGB")
    return annotated, numbered_entries


def _compute_vision_cache_key(zone_name, field_texts):
    """Deterministic cache key from zone name + sorted field texts."""
    combined = zone_name + "|" + "|".join(sorted(field_texts))
    digest = hashlib.md5(combined.encode("utf-8")).hexdigest()
    return f"_vision_explanations:{zone_name}:{digest}"


def vision_explain_fields(cropped_image, numbered_entries, zone_name):
    """
    Send annotated cropped section image to Claude Sonnet via Anthropic messages API
    and ask for 1-2 sentence practical explanations per numbered field.

    Returns dict {number: explanation_string} or empty dict on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {}

    try:
        import anthropic
        import base64
    except ImportError:
        print("  WARN: anthropic package not installed. Skipping vision explanations.")
        return {}

    if cropped_image is None:
        return {}

    # Encode image as base64 PNG
    import io as _io
    img_buf = _io.BytesIO()
    cropped_image.save(img_buf, format="PNG")
    img_b64 = base64.b64encode(img_buf.getvalue()).decode("utf-8")

    # Build field list for prompt
    field_list = "\n".join(
        f"  {num}: {entry.get('ja', '')} ({entry.get('en', '')})"
        for num, entry in numbered_entries
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"This is a section of a Japanese government form (zone: {zone_name}). "
                                f"The numbered red circles mark these fields:\n{field_list}\n\n"
                                f"For each numbered field, give a 1-2 sentence practical explanation "
                                f"of what to write and any tips for a foreign resident filling this out. "
                                f"Reply as JSON object mapping number to explanation string, e.g.:\n"
                                f'{{"1": "Write your full name...", "2": "Enter today\'s date..."}}'
                            ),
                        },
                    ],
                }
            ],
        )
        raw = message.content[0].text.strip()

        # Handle markdown-fenced JSON
        if raw.startswith("```"):
            # Strip ```json ... ``` fencing
            lines = raw.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines)

        result = json.loads(raw)
        # Normalize keys to int
        return {int(k): v for k, v in result.items()}

    except (json.JSONDecodeError, ValueError) as e:
        print(f"  WARN: Could not parse vision response for zone '{zone_name}': {e}")
        return {}
    except Exception as e:
        print(f"  WARN: Vision explanation failed for zone '{zone_name}': {e}")
        return {}


def _clean_explanation(text):
    """Strip internal metadata from explanation text that would render as box chars.

    Removes 'Matched: ...' fragment notes (contain kanji Helvetica can't render)
    and 'No translation available' noise.
    """
    if not text:
        return ""
    # Remove "Matched: 住所, 方書" fragment notes
    if text.startswith("Matched:"):
        return ""
    # Remove unhelpful filler
    if text.strip().lower() == "no translation available":
        return ""
    return text


def resolve_explanations(numbered_entries, zone_name, dictionary, cache,
                         annotated_image=None, use_llm=True):
    """
    Resolve a contextual explanation for each numbered field.

    Strategy (in order):
    1. Dictionary path: if field was translated via dictionary, use tip_en
    2. Fragment path: look up matched kanji fragments in dictionary for tip_en
    3. LLM path: use the 'note' from LLM translation if non-empty
    4. Vision fallback: batch remaining fields into one Sonnet vision call per zone

    Returns list of (number, entry, explanation) tuples.
    """
    results = []
    need_vision = []

    for number, entry in numbered_entries:
        explanation = ""
        trans_type = entry.get("type", "")
        ja_text = entry.get("ja", "").strip()
        note = entry.get("note", "")

        # 1. Dictionary path — look up tip_en directly
        if trans_type == "dictionary":
            # Find the matching dictionary entry by field_id or kanji
            field_id = entry.get("field_id", "")
            if field_id and field_id in dictionary:
                tip = dictionary[field_id].get("tip_en", "")
                if tip:
                    explanation = tip
            if not explanation:
                # Search by kanji match
                for fid, fdata in dictionary.items():
                    if ja_text == fdata.get("kanji", ""):
                        tip = fdata.get("tip_en", "")
                        if tip:
                            explanation = tip
                        break
                    for alias in fdata.get("aliases", []):
                        if ja_text == alias:
                            tip = fdata.get("tip_en", "")
                            if tip:
                                explanation = tip
                            break
                    if explanation:
                        break

        # 2. Fragment path — look up the matched kanji fragments
        if not explanation and trans_type == "fragment" and note:
            # note format: "Matched: 住所, 方書" — look up each kanji
            if note.startswith("Matched:"):
                kanji_parts = [k.strip() for k in note.split(":", 1)[1].split(",")]
                tips = []
                for kanji in kanji_parts:
                    for fid, fdata in dictionary.items():
                        if kanji == fdata.get("kanji", ""):
                            tip = fdata.get("tip_en", "")
                            if tip:
                                tips.append(tip)
                            break
                if tips:
                    explanation = " ".join(tips[:2])

        # 3. LLM path — use note from LLM translation
        if not explanation and trans_type == "llm" and note:
            explanation = _clean_explanation(note)

        if explanation:
            results.append((number, entry, explanation))
        else:
            need_vision.append((number, entry))
            results.append((number, entry, ""))  # placeholder

    # 4. Vision fallback — batch remaining unexplained fields
    if need_vision and use_llm and annotated_image is not None:
        # Check cache first
        field_texts = [e.get("ja", "") for _, e in need_vision]
        cache_key = _compute_vision_cache_key(zone_name, field_texts)

        vision_results = None
        if cache and cache_key in cache:
            vision_results = cache[cache_key]
        else:
            print(f"    Vision explaining {len(need_vision)} fields in '{zone_name}'...")
            vision_results = vision_explain_fields(annotated_image, need_vision, zone_name)
            if cache is not None and vision_results:
                cache[cache_key] = vision_results

        if vision_results:
            # Fill in placeholders
            for i, (number, entry, explanation) in enumerate(results):
                if not explanation and number in vision_results:
                    results[i] = (number, entry, vision_results[number])

    return results


# ═══════════════════════════════════════════
# PDF GUIDE GENERATION
# ═══════════════════════════════════════════

def _split_zone_into_chunks(zone, zone_translations, max_fields=15):
    """Split a dense zone into smaller chunks for readable guide pages.

    For multi-page forms, groups fields by page first to ensure each chunk
    only contains fields from a single page (critical for correct arrow placement).

    Within each page group, sorts by y0 and splits into chunks of max_fields,
    with sub-zone y_min/y_max derived from that chunk's field positions.
    """
    if len(zone_translations) <= max_fields:
        # Check if all fields are from the same page
        pages = set(t.get("page", 0) for t in zone_translations)
        if len(pages) <= 1:
            return [(zone, zone_translations)]

    # Group fields by page first (critical for multi-page forms)
    from collections import defaultdict
    by_page = defaultdict(list)
    for t in zone_translations:
        by_page[t.get("page", 0)].append(t)

    chunks = []
    # Process each page's fields separately
    for page_num in sorted(by_page.keys()):
        page_fields = by_page[page_num]
        # Sort by y position within this page
        sorted_trans = sorted(page_fields, key=lambda t: t.get("y0", 0))

        # Split into chunks of max_fields
        for i in range(0, len(sorted_trans), max_fields):
            chunk_trans = sorted_trans[i:i + max_fields]
            # Derive sub-zone y bounds from field positions (with padding)
            y_positions = [t.get("y0", 0) for t in chunk_trans]
            padding = 5
            sub_zone = dict(zone)  # shallow copy
            sub_zone["y_min"] = max(0, min(y_positions) - padding)
            sub_zone["y_max"] = max(y_positions) + padding
            chunks.append((sub_zone, chunk_trans))

    return chunks


def generate_guide(pdf_path, translations_by_zone, form_template, output_path,
                   page_image=None, page_images=None, page_height_pts=842,
                   page_heights=None, zones=None, dictionary=None, cache=None,
                   use_llm=True, ward_name=""):
    """
    Generate the multi-page bilingual PDF guide.

    Pages:
    1-N. Original form pages (embedded untouched)
    N+1. Cover page — what to bring, common mistakes, what happens after
    N+2-M. Zoomed form sections — annotated cropped image + numbered explanations
    M+1. Counter phrases

    Args:
        page_images: List of (page_num, PIL.Image) tuples for all source pages
        page_heights: List of page heights in points for each source page
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    import io

    has_font = register_fonts()
    font_ja = "JPFont" if has_font else "Helvetica"
    font_en = font_ja

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

    def pick_font(text, size, prefer_en=True):
        """Set canvas font, auto-switching to Japanese font if text contains CJK."""
        if any(ord(ch) >= 0x3000 for ch in text):
            c.setFont(font_ja, size)
        else:
            c.setFont(font_en if prefer_en else font_ja, size)

    def draw_fitted_string(x, y, text, font_name, max_size, min_size, max_width):
        """Draw text, shrinking font size if needed to fit within max_width."""
        size = max_size
        while size >= min_size:
            w = pdfmetrics.stringWidth(text, font_name, size)
            if w <= max_width:
                break
            size -= 0.5
        c.setFont(font_name, size)
        c.drawString(x, y, text)

    margin = 28
    form_name_en = "Residence Registration"
    form_name_ja = "住民異動届"
    if form_template:
        form_name_en = form_template.get("names", {}).get("en", form_name_en)
        form_name_ja = form_template.get("names", {}).get("ja", form_name_ja)

    # Count total pages (account for dense zone splitting into chunks)
    section_count = 0
    for z in zones:
        zt = translations_by_zone.get(z["name"], [])
        if zt:
            section_count += len(_split_zone_into_chunks(z, zt))
    total_pages = 2 + section_count + 1  # original + cover + sections + phrases
    if not form_template:
        total_pages = 2 + section_count  # no cover/phrases without template

    c = canvas.Canvas(str(output_path), pagesize=A4)
    ward_label = ward_name.replace("-", " ").title() if ward_name else ""
    title_prefix = f"{ward_label} — " if ward_label else ""
    c.setTitle(f"{title_prefix}{form_name_en} ({form_name_ja}) — Bilingual Guide")
    c.setAuthor("japan-forms")

    current_page = [0]

    def next_page():
        current_page[0] += 1

    def draw_header():
        c.setFillColor(NAVY)
        c.rect(0, HEIGHT - 40, WIDTH, 40, fill=True, stroke=False)
        c.setFillColor(WHITE)
        header_text = f"{form_name_ja}  {form_name_en}"
        if ward_label:
            header_text = f"{ward_label}  —  {header_text}"
        draw_fitted_string(15, HEIGHT - 27, header_text, font_ja, 12, 7, WIDTH - 30)
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

    # ═══ PAGES 1-N: Original Form (all pages embedded) ═══
    # Use page_images if available, fall back to single page_image
    images_to_embed = page_images if page_images else ([(0, page_image)] if page_image else [])

    if images_to_embed:
        for page_idx, (src_page_num, img) in enumerate(images_to_embed):
            if page_idx > 0:
                c.showPage()
            next_page()
            draw_header()
            draw_footer()

            try:
                img_buf = io.BytesIO()
                img.save(img_buf, format="PNG")
                img_buf.seek(0)
                img_reader = ImageReader(img_buf)

                # Scale to fit within margins
                avail_w = WIDTH - 2 * margin
                avail_h = HEIGHT - 80  # header + footer
                img_w, img_h = img.size
                scale = min(avail_w / img_w, avail_h / img_h)
                draw_w = img_w * scale
                draw_h = img_h * scale
                x = (WIDTH - draw_w) / 2
                y = 25  # above footer

                c.drawImage(img_reader, x, y, width=draw_w, height=draw_h)
            except Exception as e:
                c.setFont(font_en, 12)
                c.setFillColor(GRAY)
                c.drawCentredString(WIDTH / 2, HEIGHT / 2, f"Could not embed page {src_page_num + 1}: {e}")
    else:
        # No images available — just add a note
        next_page()
        draw_header()
        draw_footer()
        c.setFont(font_en, 14)
        c.setFillColor(GRAY)
        c.drawCentredString(WIDTH / 2, HEIGHT / 2,
            "Original form — print the source PDF separately")

    # ═══ PAGE 2: Cover Page ═══
    if form_template:
        c.showPage()
        next_page()
        draw_header()
        draw_footer()

        y = HEIGHT - 65

        # Title
        c.setFillColor(NAVY)
        cover_title = f"{form_name_ja}  —  {form_name_en}"
        if ward_label:
            cover_title = f"{ward_label}  —  {cover_title}"
        draw_fitted_string(margin, y, cover_title, font_ja, 16, 10, WIDTH - 2 * margin)
        y -= 25

        c.setFillColor(GRAY)
        legal = form_template.get("legal_basis", {})
        if legal:
            legal_text = f"Deadline: {legal.get('deadline_description_en', 'N/A')}  |  Cost: Free  |  Penalty: {legal.get('penalty_en', 'N/A')}"
            draw_fitted_string(margin, y, legal_text, font_en, 9, 6, WIDTH - 2 * margin)
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
                    c.setFillColor(NAVY if doc["required"] else GRAY)
                    line = f"{marker}{doc['en']}"
                    cond = doc.get("condition_en", "")
                    if cond:
                        line += f"  ({cond})"
                    pick_font(line, 7.5)
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
                mistake_text = f"X  {m['mistake_en']}"
                pick_font(mistake_text, 7.5)
                c.setFillColor(RED)
                c.drawString(margin, y, mistake_text)
                y -= 11
                fix_text = f"-> {m['fix_en']}"
                pick_font(fix_text, 7.5)
                c.setFillColor(GRAY)
                c.drawString(margin + 15, y, fix_text)
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
                step_text = f"{step['step']}.  {step['en']}"
                pick_font(step_text, 7.5)
                c.setFillColor(NAVY)
                c.drawString(margin, y, step_text)
                y -= 12

    # ═══ PAGES 3-7: Zoomed Form Sections (Annotated) ═══
    if dictionary is None:
        dictionary = {}

    for zone in zones:
        zone_name = zone["name"]
        zone_translations = translations_by_zone.get(zone_name, [])
        if not zone_translations:
            continue

        # Split dense zones into manageable chunks (Staff Section stays as-is)
        if zone_name == "Staff Section":
            chunks = [(zone, zone_translations)]
        else:
            chunks = _split_zone_into_chunks(zone, zone_translations)
        num_chunks = len(chunks)

        for chunk_idx, (chunk_zone, chunk_translations) in enumerate(chunks):
            c.showPage()
            next_page()
            draw_header()
            draw_footer()

            y = HEIGHT - 65

            # Zone title (with part suffix for multi-chunk zones)
            title_suffix = ""
            if num_chunks > 1:
                title_suffix = f"  (Part {chunk_idx + 1}/{num_chunks})"
            c.setFont(font_ja, 13)
            c.setFillColor(NAVY)
            c.drawString(margin, y, f"{zone['title_ja']}  —  {zone['title_en']}{title_suffix}")
            y -= 8
            c.setStrokeColor(RED)
            c.setLineWidth(1.5)
            c.line(margin, y, WIDTH - margin, y)
            y -= 10

            # Crop and annotate the section image
            # For multi-page forms, determine which page this zone's fields are from
            cropped = None
            annotated_img = None
            numbered_entries = []

            # Find the source page for this chunk's translations
            chunk_page_nums = set(t.get("page", 0) for t in chunk_translations if "page" in t)
            source_page = min(chunk_page_nums) if chunk_page_nums else 0

            # Select the appropriate page image
            selected_image = None
            selected_height = page_height_pts
            if page_images:
                for pg_num, pg_img in page_images:
                    if pg_num == source_page:
                        selected_image = pg_img
                        if page_heights and source_page < len(page_heights):
                            selected_height = page_heights[source_page]
                        break
            elif page_image:
                selected_image = page_image

            if selected_image:
                cropped = crop_section(selected_image, chunk_zone, selected_height, selected_image.height)

            if cropped:
                annotated_img, numbered_entries = annotate_section_image(
                    cropped, chunk_translations, chunk_zone, selected_height, selected_image.height
                )
                display_img = annotated_img if annotated_img else cropped

                img_buf = io.BytesIO()
                display_img.save(img_buf, format="PNG")
                img_buf.seek(0)
                img_reader = ImageReader(img_buf)

                # Scale annotated image to fit page width
                avail_w = WIDTH - 2 * margin
                avail_h = min(200, (HEIGHT - 120) * 0.4)
                img_w, img_h = display_img.size
                img_scale = min(avail_w / img_w, avail_h / img_h)
                draw_w = img_w * img_scale
                draw_h = img_h * img_scale

                # Draw light background behind image
                c.setFillColor(LGRAY)
                c.rect(margin - 2, y - draw_h - 4, draw_w + 4, draw_h + 4, fill=True, stroke=False)
                c.drawImage(img_reader, margin, y - draw_h, width=draw_w, height=draw_h)

                # Border
                c.setStrokeColor(HexColor("#cccccc"))
                c.setLineWidth(0.5)
                c.rect(margin - 2, y - draw_h - 4, draw_w + 4, draw_h + 4, fill=False, stroke=True)

                # Mini-map: show location on full form page
                if selected_image:
                    mini_h = 80  # thumbnail height
                    mini_scale = mini_h / selected_image.height
                    mini_w = selected_image.width * mini_scale

                    # Position in bottom right
                    mini_x = WIDTH - margin - mini_w
                    mini_y = 25  # above footer

                    # Draw thumbnail
                    thumb = selected_image.copy()
                    thumb.thumbnail((int(mini_w * 2), int(mini_h * 2)))  # 2x for quality
                    thumb_buf = io.BytesIO()
                    thumb.save(thumb_buf, format="PNG")
                    thumb_buf.seek(0)
                    thumb_reader = ImageReader(thumb_buf)
                    c.drawImage(thumb_reader, mini_x, mini_y, width=mini_w, height=mini_h)

                    # Draw rectangle showing cropped region
                    crop_scale = mini_h / selected_image.height
                    crop_y_top = chunk_zone["y_min"] * (selected_image.height / selected_height) * crop_scale
                    crop_y_bottom = chunk_zone["y_max"] * (selected_image.height / selected_height) * crop_scale
                    rect_y = mini_y + mini_h - crop_y_bottom
                    rect_h = crop_y_bottom - crop_y_top

                    c.setStrokeColor(RED)
                    c.setLineWidth(1.5)
                    c.rect(mini_x, rect_y, mini_w, rect_h, fill=False, stroke=True)

                    # Page indicator
                    c.setFont(font_en, 6)
                    c.setFillColor(GRAY)
                    c.drawString(mini_x, mini_y - 8, f"Form p.{source_page + 1}")

                y -= draw_h + 20

            # Staff section — just show a note
            if zone_name == "Staff Section":
                staff_text = "DO NOT FILL IN — Office use only (職員記入欄)"
                pick_font(staff_text, 10)
                c.setFillColor(RED)
                c.drawString(margin, y, staff_text)
                y -= 20
                c.setFont(font_en, 8)
                c.setFillColor(GRAY)
                c.drawString(margin, y, "This section is completed by ward office staff after you submit the form.")
                continue

            # Resolve explanations for numbered fields
            if numbered_entries:
                explanations = resolve_explanations(
                    numbered_entries, zone_name, dictionary, cache,
                    annotated_image=annotated_img, use_llm=use_llm,
                )
            else:
                # Fallback: build entries without annotation (no image available)
                explanations = [
                    (i + 1, t, _clean_explanation(t.get("note", "")))
                    for i, t in enumerate(chunk_translations)
                ]

            # Draw numbered explanation list
            avail_w = WIDTH - 2 * margin
            circle_r = 8
            line_spacing = 7  # extra spacing between entries

            for number, entry, explanation in explanations:
                ja_text = entry.get("ja", "")
                en_text = entry.get("en", "")
                # Replace fullwidth spaces (from Japanese form layout) with regular spaces
                en_text = en_text.replace("\u3000", " ")

                # Pre-compute whether English wraps to next line
                text_x = margin + circle_r * 2 + 12
                ja_w = pdfmetrics.stringWidth(ja_text, font_ja, 9)
                en_w = pdfmetrics.stringWidth(en_text, font_en, 8)
                en_on_next_line = (text_x + ja_w + 15 + en_w) > (WIDTH - margin)

                # Calculate space needed for this entry
                # Header line (~16pt, or +12 if en wraps) + explanation wrap lines (~11pt each)
                explanation_lines = []
                if explanation:
                    # Word-wrap explanation to ~80 chars
                    words = explanation.split()
                    line_buf = ""
                    for word in words:
                        test = f"{line_buf} {word}".strip()
                        if len(test) > 80:
                            if line_buf:
                                explanation_lines.append(line_buf)
                            line_buf = word
                        else:
                            line_buf = test
                    if line_buf:
                        explanation_lines.append(line_buf)

                entry_height = 16 + len(explanation_lines) * 11 + line_spacing
                if en_on_next_line:
                    entry_height += 12

                # Page break if not enough room
                if y - entry_height < 45:
                    c.showPage()
                    next_page()
                    draw_header()
                    draw_footer()
                    y = HEIGHT - 65

                    # Continuation header
                    c.setFont(font_ja, 11)
                    c.setFillColor(NAVY)
                    c.drawString(margin, y, f"{zone['title_ja']}  —  {zone['title_en']}{title_suffix}  (continued)")
                    y -= 8
                    c.setStrokeColor(RED)
                    c.setLineWidth(1.5)
                    c.line(margin, y, WIDTH - margin, y)
                    y -= 15

                # Draw red circle with number
                cx = margin + circle_r + 2
                cy = y - circle_r
                c.setFillColor(RED)
                c.circle(cx, cy, circle_r, fill=True, stroke=False)
                c.setFillColor(WHITE)
                c.setFont(font_en, 8)
                num_str = str(number)
                c.drawCentredString(cx, cy - 3, num_str)

                # Japanese + English on same line (or wrapped to next line)
                text_x = margin + circle_r * 2 + 12
                c.setFillColor(NAVY)
                c.setFont(font_ja, 9)
                c.drawString(text_x, y - 5, ja_text)

                # Measure actual Japanese text width
                ja_display_width = pdfmetrics.stringWidth(ja_text, font_ja, 9)
                en_x = text_x + ja_display_width + 15
                en_on_next_line = en_x + pdfmetrics.stringWidth(en_text, font_en, 8) > WIDTH - margin

                c.setFillColor(GRAY)
                pick_font(en_text, 8)
                if en_on_next_line:
                    # English wraps to next line, indented at text_x
                    y -= 12
                    c.drawString(text_x, y - 5, en_text)
                else:
                    c.drawString(en_x, y - 5, en_text)

                y -= 16

                # Explanation paragraph below
                if explanation_lines:
                    c.setFillColor(HexColor("#444444"))
                    for exp_line in explanation_lines:
                        pick_font(exp_line, 7.5)
                        c.drawString(text_x, y - 2, exp_line)
                        y -= 11

                y -= line_spacing

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

                # Romaji (may contain macrons like ū, ō — use pick_font)
                c.setFillColor(GRAY)
                romaji_text = p.get("romaji", "")
                pick_font(romaji_text, 6.5)
                c.drawString(margin + 8, y - 25, romaji_text)

                # English (may contain ○ or other non-ASCII)
                c.setFillColor(BLUE)
                pick_font(p["en"], 7)
                c.drawString(margin + 8, y - 35, p["en"])

                y -= card_h + 12

    c.save()
    return output_path


# ═══════════════════════════════════════════
# TEMPLATE-ONLY WALKTHROUGH
# ═══════════════════════════════════════════

def _template_only_output_path(form_id, output_dir):
    """Derive output PDF path from form_id for template-only mode."""
    mapping = {
        "bank_account_personal": Path("bank") / "japanpost" / "personal_account_walkthrough.pdf",
        "bank_account_corporate": Path("bank") / "japanpost" / "corporate_account_walkthrough.pdf",
    }
    rel = mapping.get(form_id, Path(f"{form_id}_walkthrough.pdf"))
    return Path(output_dir) / rel


def generate_template_walkthrough(form_template, dictionary, output_path):
    """
    Generate a multi-page walkthrough PDF purely from a form template + field dictionary.

    No input PDF needed. Produces:
    1. Cover page — form name, legal basis, eligibility
    2. Bank comparison table
    3. What to Bring — scenarios with document checklists
    4. Field-by-field translation — sections + fields from dictionary
    5. Common mistakes
    6. Middle name guide
    7. Counter phrases
    8. After submission steps
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics

    has_font = register_fonts()
    font_ja = "JPFont" if has_font else "Helvetica"
    font_en = font_ja  # MS Gothic handles both

    WIDTH, HEIGHT = A4
    NAVY = HexColor("#1a2744")
    BLUE = HexColor("#2980b9")
    GRAY = HexColor("#555555")
    RED = HexColor("#c0392b")
    WHITE = HexColor("#ffffff")
    WARM_BG = HexColor("#fff3e0")
    LIGHT = HexColor("#ebf5fb")
    GREEN = HexColor("#27ae60")
    LGRAY = HexColor("#f2f3f4")
    margin = 28

    form_name_en = form_template.get("names", {}).get("en", "Form Guide")
    form_name_ja = form_template.get("names", {}).get("ja", "")

    c = canvas.Canvas(str(output_path), pagesize=A4)
    c.setTitle(f"{form_name_en} ({form_name_ja}) — Walkthrough Guide")
    c.setAuthor("japan-forms")

    current_page = [0]

    # Count total pages: cover + bank comparison + what-to-bring + process timeline +
    # fields(1-2) + mistakes/middle-name + screening tips + rejection handling +
    # totalization countries + counter phrases + after submission
    total_pages = 1  # cover
    if form_template.get("bank_comparison") or form_template.get("bank_comparison_corporate"):
        total_pages += 1
    if form_template.get("scenarios"):
        total_pages += 1
    if form_template.get("process_timeline"):
        total_pages += 1
    if form_template.get("sections"):
        total_pages += 2  # field guide often spans 2 pages
    if form_template.get("common_mistakes"):
        total_pages += 1  # mistakes + middle name guide
    if form_template.get("screening_tips"):
        total_pages += 1
    if form_template.get("rejection_handling"):
        total_pages += 1
    if form_template.get("totalization_countries"):
        total_pages += 1
    if form_template.get("counter_phrases"):
        total_pages += 1
    if form_template.get("after_submission"):
        total_pages += 1

    def pick_font(text, size):
        if any(ord(ch) >= 0x3000 for ch in text):
            c.setFont(font_ja, size)
        else:
            c.setFont(font_en, size)

    def draw_fitted_string(x, y, text, font_name, max_size, min_size, max_width):
        size = max_size
        while size >= min_size:
            w = pdfmetrics.stringWidth(text, font_name, size)
            if w <= max_width:
                break
            size -= 0.5
        c.setFont(font_name, size)
        c.drawString(x, y, text)

    def draw_header():
        current_page[0] += 1
        c.setFillColor(NAVY)
        c.rect(0, HEIGHT - 40, WIDTH, 40, fill=True, stroke=False)
        c.setFillColor(WHITE)
        header_text = f"{form_name_ja}  {form_name_en}"
        draw_fitted_string(15, HEIGHT - 27, header_text, font_ja, 12, 7, WIDTH - 30)
        c.setFont(font_en, 7)
        c.setFillColor(HexColor("#aabbcc"))
        c.drawString(15, HEIGHT - 37, "japan-forms  ·  Walkthrough Guide")
        c.drawRightString(WIDTH - 15, HEIGHT - 37, f"Page {current_page[0]}/{total_pages}")
        c.setStrokeColor(RED)
        c.setLineWidth(2)
        c.line(0, HEIGHT - 41, WIDTH, HEIGHT - 41)

    def draw_footer():
        c.setFont(font_en, 5.5)
        c.setFillColor(HexColor("#bdc3c7"))
        c.drawString(15, 10,
            f"Generated {date.today().isoformat()} from github.com/wkesner/japan-forms  |  Not an official document")

    def section_heading(y, title, underline_w=160):
        c.setFont(font_en, 13)
        c.setFillColor(NAVY)
        c.drawString(margin, y, title)
        y -= 5
        c.setStrokeColor(RED)
        c.setLineWidth(1.5)
        c.line(margin, y, margin + underline_w, y)
        return y - 15

    def new_page():
        c.showPage()
        draw_header()
        draw_footer()

    def wrap_text(text, font_name, font_size, max_width):
        """Split text into lines that fit within max_width."""
        words = text.split()
        lines = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            if pdfmetrics.stringWidth(test, font_name, font_size) <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [""]

    # ═══ PAGE 1: Cover Page ═══
    draw_header()
    draw_footer()

    y = HEIGHT - 75

    # Title
    c.setFillColor(NAVY)
    draw_fitted_string(margin, y, f"{form_name_ja}  —  {form_name_en}", font_ja, 18, 11, WIDTH - 2 * margin)
    y -= 8

    # Romaji subtitle
    romaji = form_template.get("names", {}).get("romaji", "")
    if romaji:
        c.setFillColor(GRAY)
        pick_font(romaji, 9)
        c.drawString(margin, y, romaji)
    y -= 22

    # Legal basis box
    legal = form_template.get("legal_basis", {})
    if legal:
        max_text_w = WIDTH - 2 * margin - 20
        bx = margin + 8

        # Pre-compute all lines to size the box dynamically
        law_en = legal.get("law_en", "")
        law_lines = wrap_text(law_en, font_en, 7, max_text_w) if law_en else []
        deadline = legal.get("deadline_description_en", "N/A")
        deadline_lines = wrap_text(f"Deadline: {deadline}", font_en, 7, max_text_w)
        cost_note = legal.get("cost_note_en", f"Cost: ¥{legal.get('cost', 0)}")
        cost_lines = wrap_text(cost_note, font_en, 7, max_text_w)

        box_h = 16 + (len(law_lines) + len(deadline_lines) + len(cost_lines)) * 10 + 4
        c.setFillColor(LIGHT)
        c.rect(margin, y - box_h, WIDTH - 2 * margin, box_h, fill=True, stroke=False)
        c.setStrokeColor(BLUE)
        c.setLineWidth(2)
        c.line(margin, y - box_h, margin, y)

        by = y - 13
        c.setFillColor(NAVY)
        c.setFont(font_en, 8)
        c.drawString(bx, by, "LEGAL BASIS")
        by -= 12
        c.setFillColor(GRAY)
        c.setFont(font_en, 7)
        for line in law_lines:
            pick_font(line, 7)
            c.drawString(bx, by, line)
            by -= 10

        c.setFillColor(NAVY)
        c.setFont(font_en, 7)
        for line in deadline_lines:
            c.drawString(bx, by, line)
            by -= 10
        for line in cost_lines:
            c.drawString(bx, by, line)
            by -= 10

        y -= box_h + 15

    # Eligibility
    elig = form_template.get("eligibility", {})
    if elig:
        y = section_heading(y, "ELIGIBILITY", 100)

        for key in ("residence_requirement_en", "visa_requirement_en"):
            text = elig.get(key, "")
            if text:
                c.setFillColor(NAVY)
                for line in wrap_text(text, font_en, 7.5, WIDTH - 2 * margin - 10):
                    pick_font(line, 7.5)
                    c.drawString(margin + 5, y, line)
                    y -= 11
                y -= 4

        # List-based requirements (pension template style)
        for req in elig.get("requirements_en", []):
            if y < 60:
                new_page()
                y = HEIGHT - 60
            c.setFillColor(NAVY)
            c.setFont(font_en, 6)
            c.drawString(margin + 5, y + 2, "\u2022")
            c.setFillColor(NAVY)
            for line in wrap_text(req, font_en, 7.5, WIDTH - 2 * margin - 20):
                pick_font(line, 7.5)
                c.drawString(margin + 15, y, line)
                y -= 11
            y -= 3

        for exc in elig.get("exceptions", []):
            c.setFillColor(GREEN)
            c.setFont(font_en, 6)
            c.drawString(margin + 5, y + 2, "\u25b6")
            c.setFillColor(GRAY)
            for line in wrap_text(exc, font_en, 7, WIDTH - 2 * margin - 20):
                pick_font(line, 7)
                c.drawString(margin + 15, y, line)
                y -= 10
            y -= 3

        # Eligibility warnings (pension template)
        for warn_key in ("important_warning_en", "totalization_warning_en"):
            warn_text = elig.get(warn_key, "")
            if warn_text and y > 60:
                box_w = WIDTH - 2 * margin
                warn_lines = wrap_text(warn_text, font_en, 7, box_w - 20)
                box_h = 10 + len(warn_lines) * 10
                if y - box_h < 40:
                    new_page()
                    y = HEIGHT - 60
                c.setFillColor(HexColor("#fdecea"))
                c.rect(margin, y - box_h, box_w, box_h, fill=True, stroke=False)
                c.setStrokeColor(RED)
                c.setLineWidth(2)
                c.line(margin, y - box_h, margin, y)
                bx = margin + 8
                by = y - 11
                c.setFillColor(RED)
                c.setFont(font_en, 7)
                for line in warn_lines:
                    c.drawString(bx, by, line)
                    by -= 10
                y -= box_h + 6

    # Overview (difficulty warning + strategy)
    overview = form_template.get("overview", {})
    if overview:
        difficulty = overview.get("difficulty_en", "")
        if difficulty:
            # Red-accented warning box
            box_w = WIDTH - 2 * margin
            diff_lines = wrap_text(difficulty, font_en, 7.5, box_w - 20)
            box_h = 10 + len(diff_lines) * 11
            c.setFillColor(HexColor("#fdecea"))
            c.rect(margin, y - box_h, box_w, box_h, fill=True, stroke=False)
            c.setStrokeColor(RED)
            c.setLineWidth(2)
            c.line(margin, y - box_h, margin, y)
            bx = margin + 8
            by = y - 12
            c.setFillColor(RED)
            c.setFont(font_en, 7.5)
            for line in diff_lines:
                c.drawString(bx, by, line)
                by -= 11
            y -= box_h + 8

        # Timing + strategy as bullet points
        for key in ("timing_en", "strategy_en"):
            text = overview.get(key, "")
            if text:
                c.setFillColor(NAVY)
                c.setFont(font_en, 6)
                c.drawString(margin + 5, y + 2, "\u2022")
                c.setFillColor(GRAY)
                for line in wrap_text(text, font_en, 7, WIDTH - 2 * margin - 20):
                    pick_font(line, 7)
                    c.drawString(margin + 15, y, line)
                    y -= 10
                y -= 3

    # Pension Types
    pension_types = form_template.get("pension_types", {})
    if pension_types:
        if y < 120:
            new_page()
            y = HEIGHT - 60
        y = section_heading(y, "PENSION TYPES", 130)

        for ptype_key, ptype in pension_types.items():
            if y < 80:
                new_page()
                y = HEIGHT - 60
            box_w = WIDTH - 2 * margin
            ja_name = ptype.get("ja", "")
            en_name = ptype.get("en", "")
            who = ptype.get("who", "")
            lump_note = ptype.get("lump_sum_note", "")

            content_lines = []
            if who:
                content_lines.extend(wrap_text(f"Who: {who}", font_en, 7, box_w - 20))
            if lump_note:
                content_lines.extend(wrap_text(lump_note, font_en, 7, box_w - 20))

            box_h = 18 + len(content_lines) * 10
            c.setFillColor(LIGHT)
            c.rect(margin, y - box_h, box_w, box_h, fill=True, stroke=False)
            c.setStrokeColor(BLUE)
            c.setLineWidth(2)
            c.line(margin, y - box_h, margin, y)

            bx = margin + 8
            by = y - 13
            c.setFillColor(NAVY)
            c.setFont(font_ja, 9)
            c.drawString(bx, by, ja_name)
            ja_w = pdfmetrics.stringWidth(ja_name, font_ja, 9)
            c.setFillColor(BLUE)
            c.setFont(font_en, 8)
            c.drawString(bx + ja_w + 10, by, en_name)
            by -= 14
            c.setFillColor(GRAY)
            c.setFont(font_en, 7)
            for line in content_lines:
                c.drawString(bx, by, line)
                by -= 10
            y -= box_h + 8

    # Payment Amounts
    payment = form_template.get("payment_amounts", {})
    if payment:
        if y < 120:
            new_page()
            y = HEIGHT - 60
        y = section_heading(y, "PAYMENT AMOUNTS", 160)

        for note_key in ("national_pension_note", "employee_pension_note"):
            note_text = payment.get(note_key, "")
            if note_text:
                c.setFillColor(GRAY)
                for line in wrap_text(note_text, font_en, 7, WIDTH - 2 * margin - 10):
                    pick_font(line, 7)
                    c.drawString(margin + 5, y, line)
                    y -= 10
                y -= 4

        # Maximum months highlighted box
        max_months = payment.get("maximum_months", "")
        max_note = payment.get("maximum_months_note", "")
        if max_months or max_note:
            box_w = WIDTH - 2 * margin
            cap_text = f"Maximum: {max_months} months"
            cap_lines = [cap_text]
            if max_note:
                cap_lines.extend(wrap_text(max_note, font_en, 7, box_w - 20))
            box_h = 8 + len(cap_lines) * 11
            c.setFillColor(HexColor("#fdecea"))
            c.rect(margin, y - box_h, box_w, box_h, fill=True, stroke=False)
            c.setStrokeColor(RED)
            c.setLineWidth(2)
            c.line(margin, y - box_h, margin, y)
            bx = margin + 8
            by = y - 11
            c.setFillColor(RED)
            c.setFont(font_en, 8)
            c.drawString(bx, by, cap_lines[0])
            by -= 12
            c.setFillColor(HexColor("#444444"))
            c.setFont(font_en, 7)
            for line in cap_lines[1:]:
                c.drawString(bx, by, line)
                by -= 11
            y -= box_h + 6

        # Reform note
        reform_note = payment.get("reform_note_en", "")
        if reform_note:
            c.setFillColor(BLUE)
            for line in wrap_text(reform_note, font_en, 7, WIDTH - 2 * margin - 10):
                pick_font(line, 7)
                c.drawString(margin + 5, y, line)
                y -= 10
            y -= 4

    # Business Manager Visa Context
    visa_ctx = form_template.get("business_manager_visa_context", {})
    if visa_ctx:
        if y < 140:
            new_page()
            y = HEIGHT - 60
        y = section_heading(y, "BUSINESS MANAGER VISA CONTEXT", 260)
        box_w = WIDTH - 2 * margin
        info_items = [
            ("Capital requirement", visa_ctx.get("capital_requirement", "")),
            ("Capital note", visa_ctx.get("capital_note_en", "")),
            ("Employee requirement", visa_ctx.get("employee_requirement", "")),
            ("Japanese ability", visa_ctx.get("japanese_ability_note", "")),
            ("Physical office", visa_ctx.get("physical_office_note", "")),
        ]
        # Info box with blue accent
        c.setFillColor(LIGHT)
        # Estimate height
        info_h = 8
        for label, val in info_items:
            if val:
                info_h += 12 + len(wrap_text(val, font_en, 6.5, box_w - 80)) * 9
        c.rect(margin, y - info_h, box_w, info_h, fill=True, stroke=False)
        c.setStrokeColor(BLUE)
        c.setLineWidth(2)
        c.line(margin, y - info_h, margin, y)
        bx = margin + 8
        by = y - 5
        for label, val in info_items:
            if val:
                c.setFillColor(NAVY)
                c.setFont(font_en, 7)
                c.drawString(bx, by, f"{label}:")
                by -= 10
                c.setFillColor(GRAY)
                for line in wrap_text(val, font_en, 6.5, box_w - 20):
                    pick_font(line, 6.5)
                    c.drawString(bx + 5, by, line)
                    by -= 9
                by -= 3
        y -= info_h + 10

    # ═══ PAGE 2: Bank Comparison Table ═══
    banks = form_template.get("bank_comparison", []) or form_template.get("bank_comparison_corporate", [])
    if banks:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "BANK COMPARISON", 170)

        # Detect column layout: corporate has approval_difficulty/screening_time/note;
        # personal has min_residency/hanko_required/debit_card/best_for
        is_corporate = "approval_difficulty" in banks[0]

        if is_corporate:
            cols = [margin, margin + 95, margin + 185, margin + 240, margin + 310]
            headers = ["Bank", "English Support", "Difficulty", "Screening", "Note"]
        else:
            cols = [margin, margin + 95, margin + 195, margin + 265, margin + 310, margin + 350]
            headers = ["Bank", "English Support", "Min. Residency", "Hanko?", "Debit?", "Best For"]

        c.setFillColor(NAVY)
        c.rect(margin, y - 2, WIDTH - 2 * margin, 14, fill=True, stroke=False)
        c.setFillColor(WHITE)
        c.setFont(font_en, 6.5)
        for i, h in enumerate(headers):
            c.drawString(cols[i] + 3, y + 1, h)
        y -= 16

        for idx, bank in enumerate(banks):
            if y < 60:
                new_page()
                y = HEIGHT - 60

            row_h = 26
            # Alternating row background
            if idx % 2 == 0:
                c.setFillColor(LGRAY)
                c.rect(margin, y - row_h + 10, WIDTH - 2 * margin, row_h, fill=True, stroke=False)

            # Bank name (ja + en)
            c.setFillColor(NAVY)
            c.setFont(font_ja, 7)
            c.drawString(cols[0] + 3, y + 2, bank["bank_ja"])
            c.setFillColor(BLUE)
            c.setFont(font_en, 6)
            c.drawString(cols[0] + 3, y - 8, bank["bank_en"])

            # English support (wrap in narrow column)
            c.setFillColor(GRAY)
            c.setFont(font_en, 5.5)
            support_lines = wrap_text(bank.get("english_support", ""), font_en, 5.5, 85)
            for j, sl in enumerate(support_lines[:2]):
                c.drawString(cols[1] + 3, y + 2 - j * 9, sl)

            if is_corporate:
                # Approval difficulty
                diff = bank.get("approval_difficulty", "")
                diff_color = RED if "High" in diff or "Very" in diff else NAVY
                c.setFillColor(diff_color)
                c.setFont(font_en, 7)
                c.drawString(cols[2] + 3, y + 2, diff)

                # Screening time
                c.setFillColor(NAVY)
                c.setFont(font_en, 6.5)
                c.drawString(cols[3] + 3, y + 2, bank.get("screening_time", ""))

                # Note (wrap)
                c.setFillColor(GRAY)
                c.setFont(font_en, 5.5)
                note_lines = wrap_text(bank.get("note", ""), font_en, 5.5, WIDTH - cols[4] - margin - 5)
                for j, nl in enumerate(note_lines[:3]):
                    c.drawString(cols[4] + 3, y + 2 - j * 8, nl)
            else:
                # Min residency
                c.setFillColor(NAVY)
                c.setFont(font_en, 7)
                c.drawString(cols[2] + 3, y + 2, bank.get("min_residency", ""))

                # Hanko
                hanko = bank.get("hanko_required", False)
                c.setFillColor(RED if hanko else GREEN)
                c.setFont(font_en, 7)
                c.drawString(cols[3] + 3, y + 2, "Yes" if hanko else "No")

                # Debit card
                debit = bank.get("debit_card", False)
                c.setFillColor(GREEN if debit else GRAY)
                c.drawString(cols[4] + 3, y + 2, "Yes" if debit else "No")

                # Best for
                c.setFillColor(GRAY)
                c.setFont(font_en, 5.5)
                best_lines = wrap_text(bank.get("best_for", ""), font_en, 5.5, WIDTH - cols[5] - margin - 5)
                for j, bl in enumerate(best_lines[:2]):
                    c.drawString(cols[5] + 3, y + 2 - j * 9, bl)

            y -= row_h + 2

    # ═══ PAGE 3: What to Bring (Scenarios) ═══
    scenarios = form_template.get("scenarios", {})
    if scenarios:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "WHAT TO BRING", 140)

        for scenario_key, scenario in scenarios.items():
            if y < 120:
                new_page()
                y = HEIGHT - 60

            # Scenario title
            c.setFillColor(BLUE)
            c.setFont(font_en, 10)
            title_line = f"{scenario['title_en']}"
            c.drawString(margin, y, title_line)
            c.setFillColor(GRAY)
            c.setFont(font_ja, 7.5)
            c.drawString(margin + pdfmetrics.stringWidth(title_line, font_en, 10) + 10, y + 1, scenario.get("title_ja", ""))
            y -= 14

            # Recommended bank
            rec = scenario.get("recommended_bank", "")
            if rec:
                c.setFillColor(GREEN)
                c.setFont(font_en, 7)
                c.drawString(margin + 5, y, f"Recommended: {rec}")
                y -= 14

            # Document list
            for doc in scenario.get("documents_required", []):
                if y < 60:
                    new_page()
                    y = HEIGHT - 60

                req = doc.get("required", False)
                marker = "\u2713" if req else "\u25cb"
                c.setFillColor(RED if req else GRAY)
                c.setFont(font_ja, 7.5)
                c.drawString(margin + 10, y, marker)

                c.setFillColor(NAVY if req else GRAY)
                en_text = doc["en"]
                cond = doc.get("condition_en", "")
                if cond:
                    en_text += f"  ({cond})"
                for line in wrap_text(en_text, font_en, 7.5, WIDTH - 2 * margin - 130):
                    pick_font(line, 7.5)
                    c.drawString(margin + 22, y, line)
                    y -= 10

                c.setFillColor(GRAY)
                c.setFont(font_ja, 7)
                c.drawString(WIDTH - margin - 100, y + 10, doc.get("ja", ""))

                y -= 4

            # Additional documents (e.g. tax refund scenario)
            additional = scenario.get("additional_documents", [])
            if additional:
                y -= 4
                if y < 80:
                    new_page()
                    y = HEIGHT - 60
                c.setFillColor(BLUE)
                c.setFont(font_en, 8)
                c.drawString(margin + 10, y, "Additional Documents for This Scenario:")
                y -= 14

                for doc in additional:
                    if y < 60:
                        new_page()
                        y = HEIGHT - 60

                    req = doc.get("required", False)
                    marker = "\u2713" if req else "\u25cb"
                    c.setFillColor(RED if req else GRAY)
                    c.setFont(font_ja, 7.5)
                    c.drawString(margin + 10, y, marker)

                    c.setFillColor(NAVY if req else GRAY)
                    en_text = doc["en"]
                    cond = doc.get("condition_en", "")
                    if cond:
                        en_text += f"  ({cond})"
                    for line in wrap_text(en_text, font_en, 7.5, WIDTH - 2 * margin - 130):
                        pick_font(line, 7.5)
                        c.drawString(margin + 22, y, line)
                        y -= 10

                    c.setFillColor(GRAY)
                    c.setFont(font_ja, 7)
                    c.drawString(WIDTH - margin - 100, y + 10, doc.get("ja", ""))

                    y -= 4

            y -= 12

    # ═══ Process Timeline Page ═══
    timeline = form_template.get("process_timeline", [])
    if timeline:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "PREPARATION TIMELINE", 210)

        for step_data in timeline:
            if y < 100:
                new_page()
                y = HEIGHT - 60

            step_num = step_data.get("step", "")
            when = step_data.get("when", "")
            title_en = step_data.get("title_en", "")
            title_ja = step_data.get("title_ja", "")
            details = step_data.get("details_en", "")

            # Step number circle
            cx = margin + 10
            cy = y + 3
            c.setFillColor(BLUE)
            c.circle(cx, cy, 10, fill=True, stroke=False)
            c.setFillColor(WHITE)
            c.setFont(font_en, 9)
            c.drawCentredString(cx, cy - 3, str(step_num))

            # When label
            c.setFillColor(BLUE)
            c.setFont(font_en, 7)
            c.drawString(margin + 25, y + 6, when)

            # Title (en + ja)
            c.setFillColor(NAVY)
            c.setFont(font_en, 9)
            c.drawString(margin + 25, y - 6, title_en)
            c.setFillColor(GRAY)
            c.setFont(font_ja, 7)
            c.drawString(margin + 25 + pdfmetrics.stringWidth(title_en, font_en, 9) + 10, y - 5, title_ja)
            y -= 20

            # Details (wrapped)
            if details:
                c.setFillColor(HexColor("#444444"))
                for line in wrap_text(details, font_en, 7, WIDTH - 2 * margin - 35):
                    if y < 50:
                        new_page()
                        y = HEIGHT - 60
                    pick_font(line, 7)
                    c.drawString(margin + 30, y, line)
                    y -= 10
            y -= 10

    # ═══ PAGE 4+: Field-by-Field Translation ═══
    sections = form_template.get("sections", [])
    if sections:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "FIELD-BY-FIELD GUIDE", 180)

        for section in sections:
            if y < 120:
                new_page()
                y = HEIGHT - 60

            # Section header
            sec_num = section.get("section_number", "")
            sec_title_en = section.get("title_en", "")
            sec_title_ja = section.get("title_ja", "")
            c.setFillColor(NAVY)
            c.rect(margin, y - 2, WIDTH - 2 * margin, 16, fill=True, stroke=False)
            c.setFillColor(WHITE)
            c.setFont(font_en, 9)
            c.drawString(margin + 5, y + 1, f"Section {sec_num}: {sec_title_en}")
            c.setFont(font_ja, 8)
            c.drawString(margin + 250, y + 1, sec_title_ja)
            y -= 20

            # Section note
            sec_note = section.get("note_en", "")
            if sec_note:
                c.setFillColor(BLUE)
                for line in wrap_text(sec_note, font_en, 7, WIDTH - 2 * margin - 20):
                    c.setFont(font_en, 7)
                    c.drawString(margin + 10, y, line)
                    y -= 10
                y -= 4

            # Fields
            for field_ref in section.get("fields", []):
                if y < 80:
                    new_page()
                    y = HEIGHT - 60

                fid = field_ref["field_id"]
                field = dictionary.get(fid, {})
                required = field_ref.get("required", False)
                field_note = field_ref.get("note_en", "")

                kanji = field.get("kanji", fid)
                romaji_f = field.get("romaji", "")
                english = field.get("english", fid)
                context_label = field_ref.get("context_label_en", "")
                tip = field.get("tip_en", "")

                # Field row
                # Required marker
                if required:
                    c.setFillColor(RED)
                    c.setFont(font_en, 6)
                    c.drawString(margin + 2, y + 1, "REQ")
                else:
                    c.setFillColor(GRAY)
                    c.setFont(font_en, 6)
                    c.drawString(margin + 2, y + 1, "OPT")

                # Kanji
                c.setFillColor(NAVY)
                c.setFont(font_ja, 10)
                c.drawString(margin + 25, y, kanji)
                kanji_w = pdfmetrics.stringWidth(kanji, font_ja, 10)

                # Romaji
                c.setFillColor(GRAY)
                pick_font(romaji_f, 7)
                c.drawString(margin + 25 + kanji_w + 8, y + 1, romaji_f)

                # English + context label
                c.setFillColor(BLUE)
                c.setFont(font_en, 8)
                en_display = english
                if context_label:
                    en_display = f"{english} ({context_label})"
                en_lines = wrap_text(en_display, font_en, 8, WIDTH - margin - 205)
                for el in en_lines:
                    c.drawString(margin + 200, y + 1, el)
                    y -= 13

                # Tip
                if tip:
                    c.setFillColor(HexColor("#444444"))
                    for line in wrap_text(tip, font_en, 6.5, WIDTH - 2 * margin - 40):
                        c.setFont(font_en, 6.5)
                        c.drawString(margin + 30, y, line)
                        y -= 9

                # Field-specific note from template
                if field_note:
                    c.setFillColor(GREEN)
                    for line in wrap_text(f"\u25b6 {field_note}", font_en, 6.5, WIDTH - 2 * margin - 40):
                        pick_font(line, 6.5)
                        c.drawString(margin + 30, y, line)
                        y -= 9

                # Options
                options = field.get("options", {})
                if options:
                    for opt_key, opt_val in options.items():
                        if y < 60:
                            new_page()
                            y = HEIGHT - 60
                        opt_en = opt_val.get("english", "")
                        c.setFillColor(GRAY)
                        c.setFont(font_ja, 6.5)
                        c.drawString(margin + 35, y, f"{opt_key}")
                        c.setFont(font_en, 6.5)
                        c.drawString(margin + 100, y, f"= {opt_en}")
                        y -= 9

                y -= 5

    # ═══ Common Mistakes Page ═══
    mistakes = form_template.get("common_mistakes", [])
    if mistakes:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "COMMON MISTAKES", 160)

        for i, m in enumerate(mistakes):
            if y < 100:
                new_page()
                y = HEIGHT - 60

            # Mistake
            c.setFillColor(RED)
            c.setFont(font_en, 8)
            c.drawString(margin, y, f"{i + 1}.")
            c.setFillColor(NAVY)
            for line in wrap_text(m["mistake_en"], font_en, 8, WIDTH - 2 * margin - 20):
                c.setFont(font_en, 8)
                c.drawString(margin + 15, y, line)
                y -= 11
            y -= 2

            # Fix
            c.setFillColor(GREEN)
            c.setFont(font_en, 6)
            c.drawString(margin + 15, y + 2, "\u25b6")
            c.setFillColor(HexColor("#444444"))
            for line in wrap_text(m["fix_en"], font_en, 7, WIDTH - 2 * margin - 30):
                pick_font(line, 7)
                c.drawString(margin + 25, y, line)
                y -= 10
            y -= 10

    # ═══ Totalization Countries Page ═══
    totalization = form_template.get("totalization_countries", {})
    if totalization:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "TOTALIZATION COUNTRIES", 210)

        # Explanation intro
        explanation = totalization.get("explanation_en", "")
        if explanation:
            box_w = WIDTH - 2 * margin
            exp_lines = wrap_text(explanation, font_en, 7.5, box_w - 20)
            box_h = 10 + len(exp_lines) * 11
            c.setFillColor(HexColor("#fdecea"))
            c.rect(margin, y - box_h, box_w, box_h, fill=True, stroke=False)
            c.setStrokeColor(RED)
            c.setLineWidth(2)
            c.line(margin, y - box_h, margin, y)
            bx = margin + 8
            by = y - 11
            c.setFillColor(RED)
            c.setFont(font_en, 7.5)
            for line in exp_lines:
                c.drawString(bx, by, line)
                by -= 11
            y -= box_h + 12

        # Country list in 3 columns
        countries = totalization.get("countries", [])
        if countries:
            col_w = (WIDTH - 2 * margin) / 3
            col_x = [margin + col_w * i for i in range(3)]
            rows = (len(countries) + 2) // 3  # ceil division

            for row_idx in range(rows):
                if y < 50:
                    new_page()
                    y = HEIGHT - 60
                for col_idx in range(3):
                    ci = row_idx * 3 + col_idx
                    if ci < len(countries):
                        c.setFillColor(NAVY)
                        c.setFont(font_en, 7.5)
                        c.drawString(col_x[col_idx] + 5, y, f"\u2022 {countries[ci]}")
                y -= 12

        # Footer note
        note = totalization.get("note_en", "")
        if note:
            y -= 6
            c.setFillColor(GRAY)
            for line in wrap_text(note, font_en, 7, WIDTH - 2 * margin - 10):
                pick_font(line, 7)
                c.drawString(margin + 5, y, line)
                y -= 10

    # ═══ Middle Name Guide ═══
    mng = form_template.get("middle_name_guide", {})
    if mng:
        if y < 200:
            new_page()
            y = HEIGHT - 60
        y = section_heading(y, "MIDDLE NAME GUIDE", 170)

        explanation = mng.get("explanation_en", "")
        if explanation:
            c.setFillColor(GRAY)
            for line in wrap_text(explanation, font_en, 7.5, WIDTH - 2 * margin - 10):
                pick_font(line, 7.5)
                c.drawString(margin + 5, y, line)
                y -= 11
            y -= 8

        for opt in mng.get("options", []):
            if y < 80:
                new_page()
                y = HEIGHT - 60

            # Option title
            c.setFillColor(BLUE)
            c.setFont(font_en, 8)
            c.drawString(margin + 5, y, opt["option_en"])
            y -= 12

            # Example
            example = opt.get("example", "")
            if example:
                c.setFillColor(NAVY)
                for line in wrap_text(example, font_ja, 7.5, WIDTH - 2 * margin - 25):
                    pick_font(line, 7.5)
                    c.drawString(margin + 15, y, line)
                    y -= 10

            # Note
            note = opt.get("note", "")
            if note:
                c.setFillColor(HexColor("#444444"))
                for line in wrap_text(note, font_en, 6.5, WIDTH - 2 * margin - 25):
                    pick_font(line, 6.5)
                    c.drawString(margin + 15, y, line)
                    y -= 9
            y -= 8

    # ═══ Screening Tips Page ═══
    screening_tips = form_template.get("screening_tips", [])
    if screening_tips:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "SCREENING TIPS", 150)

        for i, tip in enumerate(screening_tips):
            if y < 100:
                new_page()
                y = HEIGHT - 60

            # Tip heading (bold-style)
            c.setFillColor(NAVY)
            c.setFont(font_en, 8.5)
            tip_heading = f"{i + 1}. {tip['tip_en']}"
            for line in wrap_text(tip_heading, font_en, 8.5, WIDTH - 2 * margin - 15):
                c.drawString(margin, y, line)
                y -= 12
            y -= 2

            # Detail
            c.setFillColor(HexColor("#444444"))
            for line in wrap_text(tip["detail_en"], font_en, 7, WIDTH - 2 * margin - 25):
                pick_font(line, 7)
                c.drawString(margin + 15, y, line)
                y -= 10
            y -= 10

    # ═══ Rejection Handling Page ═══
    rejection = form_template.get("rejection_handling", {})
    if rejection:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "REJECTION HANDLING", 180)

        # Explanation intro
        explanation = rejection.get("explanation_en", "")
        if explanation:
            c.setFillColor(GRAY)
            for line in wrap_text(explanation, font_en, 7.5, WIDTH - 2 * margin - 10):
                pick_font(line, 7.5)
                c.drawString(margin + 5, y, line)
                y -= 11
            y -= 10

        # Common reasons — numbered list
        reasons = rejection.get("common_reasons", [])
        if reasons:
            c.setFillColor(NAVY)
            c.setFont(font_en, 9)
            c.drawString(margin, y, "Common reasons for rejection:")
            y -= 16

            for i, reason in enumerate(reasons):
                if y < 60:
                    new_page()
                    y = HEIGHT - 60
                c.setFillColor(RED)
                c.setFont(font_en, 7.5)
                c.drawString(margin + 5, y, f"{i + 1}.")
                c.setFillColor(NAVY)
                for line in wrap_text(reason, font_en, 7.5, WIDTH - 2 * margin - 30):
                    pick_font(line, 7.5)
                    c.drawString(margin + 20, y, line)
                    y -= 11
                y -= 4
            y -= 8

        # Next steps — bulleted action list
        next_steps = rejection.get("next_steps", [])
        if next_steps:
            c.setFillColor(NAVY)
            c.setFont(font_en, 9)
            c.drawString(margin, y, "Next steps:")
            y -= 16

            for step in next_steps:
                if y < 60:
                    new_page()
                    y = HEIGHT - 60
                c.setFillColor(GREEN)
                c.setFont(font_en, 7)
                c.drawString(margin + 5, y + 2, "\u25b6")
                c.setFillColor(HexColor("#444444"))
                for line in wrap_text(step, font_en, 7, WIDTH - 2 * margin - 25):
                    pick_font(line, 7)
                    c.drawString(margin + 18, y, line)
                    y -= 10
                y -= 4

    # ═══ Counter Phrases Page ═══
    phrases = form_template.get("counter_phrases", [])
    if phrases:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "COUNTER PHRASES", 160)

        c.setFillColor(GRAY)
        c.setFont(font_en, 8)
        c.drawString(margin, y, "Point and show these to bank staff")
        y -= 18

        for p in phrases:
            if y < 70:
                new_page()
                y = HEIGHT - 60

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
            romaji_text = p.get("romaji", "")
            pick_font(romaji_text, 6.5)
            c.drawString(margin + 8, y - 25, romaji_text)

            # English
            c.setFillColor(BLUE)
            pick_font(p["en"], 7)
            c.drawString(margin + 8, y - 35, p["en"])

            y -= card_h + 12

    # ═══ After Submission Page ═══
    after = form_template.get("after_submission", [])
    if after:
        new_page()
        y = HEIGHT - 60
        y = section_heading(y, "AFTER SUBMISSION", 160)

        for step in after:
            if y < 70:
                new_page()
                y = HEIGHT - 60

            step_num = step.get("step", "")
            # Step number circle
            c.setFillColor(NAVY)
            cx = margin + 10
            cy = y + 3
            c.circle(cx, cy, 8, fill=True, stroke=False)
            c.setFillColor(WHITE)
            c.setFont(font_en, 8)
            c.drawCentredString(cx, cy - 3, str(step_num))

            # Japanese
            c.setFillColor(NAVY)
            c.setFont(font_ja, 9)
            c.drawString(margin + 25, y + 2, step.get("ja", ""))
            y -= 14

            # English
            c.setFillColor(GRAY)
            for line in wrap_text(step.get("en", ""), font_en, 7.5, WIDTH - 2 * margin - 30):
                pick_font(line, 7.5)
                c.drawString(margin + 25, y, line)
                y -= 10
            y -= 8

        # Mailing address
        mailing = form_template.get("mailing_address", {})
        if mailing:
            if y < 100:
                new_page()
                y = HEIGHT - 60
            y -= 5
            c.setFillColor(NAVY)
            c.setFont(font_en, 9)
            c.drawString(margin, y, "MAILING ADDRESS")
            y -= 14

            box_w = WIDTH - 2 * margin
            addr_ja = mailing.get("ja", "")
            addr_en = mailing.get("en", "")
            ja_lines = addr_ja.split("\n") if addr_ja else []
            en_lines = addr_en.split("\n") if addr_en else []
            box_h = 14 + (len(ja_lines) + len(en_lines)) * 12
            c.setFillColor(LIGHT)
            c.rect(margin, y - box_h, box_w, box_h, fill=True, stroke=False)
            c.setStrokeColor(BLUE)
            c.setLineWidth(2)
            c.line(margin, y - box_h, margin, y)
            bx = margin + 8
            by = y - 12
            c.setFillColor(NAVY)
            c.setFont(font_ja, 9)
            for line in ja_lines:
                c.drawString(bx, by, line)
                by -= 12
            by -= 4
            c.setFillColor(GRAY)
            c.setFont(font_en, 8)
            for line in en_lines:
                c.drawString(bx, by, line)
                by -= 12
            y -= box_h + 10

        # Download / Info URLs
        download_url = form_template.get("download_url", "")
        jps_info_url = form_template.get("jps_info_url", "")
        if download_url or jps_info_url:
            if y < 60:
                new_page()
                y = HEIGHT - 60
            c.setFillColor(BLUE)
            c.setFont(font_en, 7.5)
            if download_url:
                c.drawString(margin, y, f"Download form: {download_url}")
                y -= 12
            if jps_info_url:
                c.drawString(margin, y, f"More info: {jps_info_url}")
                y -= 12

    c.save()
    print(f"    Generated {output_path.name}")
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
    # Extract city and ward from path (e.g., input/tokyo/katsushika/ido.pdf)
    ward_name = ""
    city_name = ""
    parts = Path(pdf_path).parts
    for i, part in enumerate(parts):
        if part.lower() in ("tokyo", "osaka", "kyoto", "nagoya") and i + 1 < len(parts) - 1:
            city_name = part.lower()
            ward_name = parts[i + 1]
            break
    if city_name and ward_name:
        ward_dir = output_dir / city_name / ward_name
        ward_dir.mkdir(parents=True, exist_ok=True)
        output_path = ward_dir / f"{pdf_name}_walkthrough.pdf"
    else:
        output_path = output_dir / f"{pdf_name}_walkthrough.pdf"

    print(f"  Pipeline: {Path(pdf_path).name}")

    # Load data
    dictionary = load_field_dictionary()
    form_template = load_form_template(form_id)
    cache = load_translation_cache()

    if not form_template:
        print(f"    WARN: Form template '{form_id}' not found. Generating without template content.")

    # Step 1: Get page count and extract text from ALL pages
    print(f"    Extracting text...")
    import pdfplumber

    num_pages = 1
    page_heights = []
    page_widths = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            num_pages = len(pdf.pages)
            page_heights = [float(p.height) for p in pdf.pages]
            page_widths = [float(p.width) for p in pdf.pages]
    except Exception:
        page_heights = [842]  # A4 default
        page_widths = [595]   # A4 default

    # Extract and cluster fields from each page
    all_fields = []  # List of (page_num, fields_list, is_ocr)
    ocr_translations = {}  # Pre-computed translations from OCR workflow
    total_chars = 0
    ocr_stats = {"dict_hits": 0, "frag_hits": 0, "llm_hits": 0, "unknown": 0}

    # Classify pages upfront (text vs image)
    MIN_CHARS_FOR_TEXT = 50
    page_classifications = classify_pdf_pages(pdf_path, min_chars=MIN_CHARS_FOR_TEXT)
    text_pages = sum(1 for p in page_classifications if p["type"] == "text")
    image_pages = sum(1 for p in page_classifications if p["type"] == "image")
    print(f"    Page classification: {text_pages} text-based, {image_pages} image-based")

    for page_num in range(num_pages):
        chars = extract_text(pdf_path, page_num=page_num)
        page_height_pts = page_heights[page_num] if page_num < len(page_heights) else 842
        page_width_pts = page_widths[page_num] if page_num < len(page_widths) else 595
        is_ocr_page = False

        # Use OCR if page is classified as image-based (< MIN_CHARS_FOR_TEXT chars)
        if len(chars) < MIN_CHARS_FOR_TEXT:
            page_image_for_ocr = render_page_image(pdf_path, page_num=page_num, dpi=200)
            if page_image_for_ocr and use_llm:
                page_label = f"image ({len(chars)} chars)" if chars else "image"
                print(f"    Page {page_num + 1} [{page_label}]: Using OCR workflow...")
                fields, page_stats = ocr_extract_translate_locate(
                    page_image_for_ocr,
                    page_width_pts=page_width_pts,
                    page_height_pts=page_height_pts,
                    dictionary=dictionary,
                    cache=cache,
                    use_llm=use_llm
                )
                is_ocr_page = True
                # Accumulate OCR stats
                for k in ocr_stats:
                    ocr_stats[k] += page_stats.get(k, 0)
                # Store pre-computed translations
                for f in fields:
                    if "translation" in f:
                        ocr_translations[f["text"]] = f["translation"]
            else:
                fields = []
        else:
            print(f"    Page {page_num + 1} [text ({len(chars)} chars)]: Extracting fields...")
            fields = cluster_fields(chars)

        if fields:
            # Keep original y-coordinates, just track page number and OCR flag
            for f in fields:
                f["page"] = page_num
                f["is_ocr"] = is_ocr_page
            all_fields.append((page_num, fields, page_height_pts))
            total_chars += len(chars) if chars else sum(f.get("char_count", 0) for f in fields)
            print(f"    Page {page_num + 1}: {len(chars) if chars else 'OCR'}, {len(fields)} fields")

    # Combine all fields for translation
    fields = []
    for page_num, page_fields, _ in all_fields:
        fields.extend(page_fields)

    # Deduplicate fields that occupy similar positions (prevents duplicate annotations)
    fields_before = len(fields)
    fields = deduplicate_fields(fields)
    if len(fields) < fields_before:
        print(f"    Deduplicated: {fields_before} → {len(fields)} fields")

    print(f"    Found {total_chars} characters across {num_pages} page(s)")
    print(f"    Clustered into {len(fields)} field groups")

    # Use first page height for zone mapping (zones are relative to page)
    page_height_pts = page_heights[0] if page_heights else 842

    # Check if we have OCR fields - if so, use dynamic zones instead of predefined
    has_ocr_fields = any(f.get("is_ocr", False) for f in fields)
    if has_ocr_fields:
        # Create dynamic zones based on actual field positions
        # Group fields by page first
        fields_by_page = {}
        for f in fields:
            page = f.get("page", 0)
            if page not in fields_by_page:
                fields_by_page[page] = []
            fields_by_page[page].append(f)

        # Create zones for each page's fields
        dynamic_zones = []
        for page_num in sorted(fields_by_page.keys()):
            page_fields = fields_by_page[page_num]
            page_h = page_heights[page_num] if page_num < len(page_heights) else 842
            page_zones = create_dynamic_zones_for_fields(page_fields, page_h, max_fields_per_zone=12)
            # Tag zones with page number
            for z in page_zones:
                z["page"] = page_num
                z["name"] = f"Page {page_num + 1} - {z['name']}"
            dynamic_zones.extend(page_zones)

        if dynamic_zones:
            zones = dynamic_zones
            print(f"    Using {len(zones)} dynamic zones for OCR content")

    # Step 4: Translate fields by zone
    # Use pre-computed translations for OCR fields, regular translation for text-based
    print(f"    Translating fields...")
    translations_by_zone = {}
    dict_hits = ocr_stats["dict_hits"]  # Start with OCR stats
    frag_hits = ocr_stats["frag_hits"]
    llm_hits = ocr_stats["llm_hits"]
    unknown = ocr_stats["unknown"]

    for zone in zones:
        zone_fields = fields_in_zone(fields, zone)
        zone_translations = []

        for field in zone_fields:
            text = field["text"].strip()
            if not text or len(text) < 2:
                continue

            # Check for pre-computed OCR translation first
            if text in ocr_translations:
                result = ocr_translations[text]
            elif field.get("translation"):
                # Translation embedded in field from OCR workflow
                result = field["translation"]
            else:
                # Regular translation for text-based fields
                result = translate_field(text, cache, dictionary, use_llm=use_llm)
                t = result.get("type", "")
                if t == "dictionary":
                    dict_hits += 1
                elif t == "fragment":
                    frag_hits += 1
                elif t == "llm":
                    llm_hits += 1
                elif t == "unknown":
                    unknown += 1

            zone_translations.append({
                "ja": text,
                "en": result.get("en", ""),
                "type": result.get("type", "unknown"),
                "note": result.get("note", ""),
                "x0": field["x0"],
                "y0": field["y0"],
                "page": field.get("page", 0),
                "is_ocr": field.get("is_ocr", False),
            })

        translations_by_zone[zone["name"]] = zone_translations

    # Collect any fields that fell outside defined zones
    catch_zone, unassigned = collect_unassigned_fields(fields, zones)
    if unassigned:
        print(f"    Found {len(unassigned)} fields outside defined zones")
        zone_translations = []
        for field in unassigned:
            text = field["text"].strip()
            if not text or len(text) < 2:
                continue

            # Check for pre-computed OCR translation first
            if text in ocr_translations:
                result = ocr_translations[text]
            elif field.get("translation"):
                result = field["translation"]
            else:
                result = translate_field(text, cache, dictionary, use_llm=use_llm)
                t = result.get("type", "")
                if t == "dictionary":
                    dict_hits += 1
                elif t == "fragment":
                    frag_hits += 1
                elif t == "llm":
                    llm_hits += 1
                elif t == "unknown":
                    unknown += 1

            zone_translations.append({
                "ja": text,
                "en": result.get("en", ""),
                "type": result.get("type", "unknown"),
                "note": result.get("note", ""),
                "x0": field["x0"],
                "y0": field["y0"],
                "page": field.get("page", 0),
                "is_ocr": field.get("is_ocr", False),
            })
        if zone_translations:
            translations_by_zone[catch_zone["name"]] = zone_translations
            zones = zones + [catch_zone]  # Add to zones list for rendering

    total = dict_hits + frag_hits + llm_hits + unknown
    print(f"    Translations: {dict_hits} dictionary, {frag_hits} fragment, {llm_hits} LLM, {unknown} unknown (of {total})")

    # Save cache after translating
    save_translation_cache(cache)

    # Step 5: Render page images for ALL pages
    print(f"    Rendering page image(s)...")
    page_images = []
    for page_num in range(num_pages):
        img = render_page_image(pdf_path, page_num=page_num, dpi=200)
        if img:
            page_images.append((page_num, img))
            print(f"    Page {page_num + 1}: {img.size[0]}x{img.size[1]} px")

    if not page_images:
        print(f"    WARN: Could not render any page images (poppler may not be installed)")

    # For backward compatibility, use first image as primary
    page_image = page_images[0][1] if page_images else None

    # Step 6: Generate guide PDF
    print(f"    Generating guide PDF...")
    result = generate_guide(
        pdf_path=pdf_path,
        translations_by_zone=translations_by_zone,
        form_template=form_template,
        output_path=output_path,
        page_image=page_image,
        page_images=page_images,  # Pass all page images
        page_height_pts=page_height_pts,
        page_heights=page_heights,  # Pass all page heights
        zones=zones,
        dictionary=dictionary,
        cache=cache,
        use_llm=use_llm,
        ward_name=ward_name,
    )

    # Save cache again after guide generation (persists vision explanation cache)
    save_translation_cache(cache)

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
    parser.add_argument("input", nargs="?", default=None,
                        help="Path to input PDF file (not required with --template-only)")
    parser.add_argument("-o", "--output", default=None,
                        help=f"Output directory (default: {OUTPUT_DIR.relative_to(BASE_DIR)})")
    parser.add_argument("--form", default="residence_registration",
                        help="Form template ID (default: residence_registration)")
    parser.add_argument("--no-llm", action="store_true",
                        help="Dictionary-only mode — no Claude API calls")
    parser.add_argument("--dpi", type=int, default=200,
                        help="DPI for page rendering (default: 200)")
    parser.add_argument("--template-only", action="store_true",
                        help="Generate walkthrough from template only (no PDF input)")

    args = parser.parse_args()

    # ── Template-only mode ──
    if args.template_only:
        form_template = load_form_template(args.form)
        if not form_template:
            print(f"ERROR: Form template '{args.form}' not found in {FORMS_DIR}")
            sys.exit(1)

        dictionary = load_field_dictionary()
        output_dir = Path(args.output) if args.output else OUTPUT_DIR
        output_path = _template_only_output_path(args.form, output_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"japan-forms template-only walkthrough")
        print(f"  Form:   {args.form}")
        print(f"  Output: {output_path}")
        print()

        result = generate_template_walkthrough(form_template, dictionary, output_path)
        if result:
            size_kb = output_path.stat().st_size / 1024
            print(f"\nDone! Walkthrough saved to: {result} ({size_kb:.0f} KB)")
        else:
            print(f"\nFailed to generate walkthrough.")
            sys.exit(1)
        return

    # ── Normal PDF mode ──
    if not args.input:
        parser.error("input PDF is required (or use --template-only)")

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
