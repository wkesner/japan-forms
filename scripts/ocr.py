#!/usr/bin/env python3
"""
OCR extraction for scanned Japanese government forms.

Uses easyOCR for precise text detection and bounding boxes. Known labels are
identified via dictionary lookup (free, instant). Remaining detections are
sent to Claude Vision for field-label classification.

Usage:
    python scripts/ocr.py input.pdf                    # Process all pages
    python scripts/ocr.py input.pdf --page 1           # Process specific page
    python scripts/ocr.py input.pdf -o output.json     # Save to JSON
"""

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

# Optional imports handled gracefully
try:
    from pdf2image import convert_from_path
    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import easyocr
    HAS_EASYOCR = True
except ImportError:
    HAS_EASYOCR = False


# Lazy-loaded easyOCR reader
_easyocr_reader = None


def _get_easyocr_reader():
    """Lazy-initialize easyOCR reader (model loading is slow, so cache it)."""
    global _easyocr_reader
    if _easyocr_reader is None:
        if not HAS_EASYOCR:
            print("ERROR: easyocr not installed. Run: pip install easyocr")
            return None
        print("    OCR: Loading easyOCR models (first run may be slow)...")
        _easyocr_reader = easyocr.Reader(['ja', 'en'], gpu=False)
    return _easyocr_reader


def image_to_base64(image) -> str:
    """Convert PIL Image to base64 string."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def render_pdf_page(pdf_path: Path, page_num: int = 0, dpi: int = 200):
    """Render a PDF page to PIL Image."""
    if not HAS_PDF2IMAGE:
        print("ERROR: pdf2image not installed. Run: pip install pdf2image")
        return None

    try:
        images = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            first_page=page_num + 1,
            last_page=page_num + 1
        )
        return images[0] if images else None
    except Exception as e:
        print(f"ERROR: Could not render PDF page: {e}")
        return None


def get_pdf_page_count(pdf_path: Path) -> int:
    """Get number of pages in a PDF."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:
        return 1


# ═══════════════════════════════════════════
# EASYOCR DETECTION
# ═══════════════════════════════════════════

def _easyocr_detect(image) -> list:
    """
    Run easyOCR on a PIL Image. Returns precise bounding boxes + text.

    Returns:
        List of detections: [{"text": str, "confidence": float,
                              "x_min": float, "y_min": float,
                              "x_max": float, "y_max": float}, ...]
        Coordinates are in pixels relative to the image.
    """
    import numpy as np

    reader = _get_easyocr_reader()
    if reader is None:
        return []

    img_array = np.array(image)
    results = reader.readtext(img_array)

    detections = []
    for bbox, text, conf in results:
        text = text.strip()
        if not text:
            continue
        # bbox: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] four corners
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        detections.append({
            "text": text,
            "confidence": float(conf),
            "x_min": float(min(xs)),
            "y_min": float(min(ys)),
            "x_max": float(max(xs)),
            "y_max": float(max(ys)),
        })
    return detections


# ═══════════════════════════════════════════
# DICTIONARY PRE-FILTER
# ═══════════════════════════════════════════

def _dictionary_filter(detections, dictionary):
    """
    Classify detections using the field dictionary (exact match).

    Returns:
        (matched, unmatched) - matched are confirmed field labels,
        unmatched need Claude Vision classification.
    """
    if not dictionary:
        return [], detections

    # Build set of known kanji labels
    known_labels = set()
    for field_id, field in dictionary.items():
        kanji = field.get("kanji", "")
        if kanji:
            known_labels.add(kanji)
        for alias in field.get("aliases", []):
            if alias:
                known_labels.add(alias)

    matched = []
    unmatched = []

    for det in detections:
        if det["text"] in known_labels:
            matched.append(det)
        else:
            unmatched.append(det)

    return matched, unmatched


# ═══════════════════════════════════════════
# CLAUDE VISION FILTERING
# ═══════════════════════════════════════════

def _vision_filter_detections(image, detections, api_key=None):
    """
    Send easyOCR detections + image to Claude Vision to identify field labels.

    Claude sees both the form image and the numbered OCR detection list.
    It identifies which detections are field labels and corrects misreadings.

    Args:
        image: PIL Image
        detections: List of easyOCR detection dicts
        api_key: Anthropic API key

    Returns:
        List of dicts: [{"index": 1, "text": "届出日"}, ...]
        Indices are 1-based, matching the numbered list sent to Claude.
        Merged detections use "indices": [N, M].
    """
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not installed. Run: pip install anthropic")
        return []

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return []

    if not detections:
        return []

    img_b64 = image_to_base64(image)

    det_list = "\n".join(f'{i+1}. "{d["text"]}"' for i, d in enumerate(detections))

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""This is a scanned Japanese government form. OCR detected these text regions:

{det_list}

Identify which are form FIELD LABELS (like 届出人, 氏名, 住所, 転出先, 届出日, 世帯主, 続柄, 生年月日, 届出の理由, etc.).

Instructions:
1. Include section headers and instructional text
2. SKIP: page numbers, decorative elements, filled-in values, watermarks, examples
3. If OCR misread text, provide the corrected Japanese reading
4. If a label was split across multiple OCR regions, list all relevant indices

Return a JSON array:
[
  {{"index": 1, "text": "届出日"}},
  {{"indices": [3, 4], "text": "届出人"}},
  ...
]

Use "index" for single detections, "indices" for merged.
Return ONLY the JSON array."""

    try:
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
                    {"type": "text", "text": prompt}
                ],
            }],
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
            if text.startswith("json"):
                text = text[4:].strip()

        items = json.loads(text)
        if not isinstance(items, list):
            return []
        return items

    except json.JSONDecodeError as e:
        print(f"ERROR: Could not parse Vision response: {e}")
        return []
    except Exception as e:
        print(f"ERROR: Vision API call failed: {e}")
        return []


def _resolve_label_bbox(label, detections):
    """
    Resolve a Claude-identified label to its easyOCR bounding box.

    Handles both single ("index") and merged ("indices") detections.
    Returns (text, x_min, y_min, x_max, y_max) or None.
    """
    text = label.get("text", "")
    if not text:
        return None

    # Get detection indices (1-based from Claude)
    indices = label.get("indices", [])
    if not indices:
        idx = label.get("index")
        if idx is not None:
            indices = [int(idx)]

    # Convert 1-based to 0-based, validate bounds
    det_indices = [i - 1 for i in indices if 1 <= i <= len(detections)]
    if not det_indices:
        return None

    # Compute merged bounding box
    x_min = min(detections[i]["x_min"] for i in det_indices)
    y_min = min(detections[i]["y_min"] for i in det_indices)
    x_max = max(detections[i]["x_max"] for i in det_indices)
    y_max = max(detections[i]["y_max"] for i in det_indices)

    return text, x_min, y_min, x_max, y_max


# ═══════════════════════════════════════════
# VISION-ONLY FALLBACK
# ═══════════════════════════════════════════

def _vision_only_extract(image, api_key=None, include_positions=True):
    """Fallback: Claude Vision only (when easyOCR is unavailable)."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not installed")
        return []

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return []

    img_b64 = image_to_base64(image)
    client = anthropic.Anthropic(api_key=api_key)

    if include_positions:
        prompt = """This is a scanned Japanese government form. Extract ALL Japanese text that represents form field labels, WITH their positions and sizes.

Instructions:
1. Read the form top-to-bottom, left-to-right
2. Extract field labels like: 届出人, 氏名, 住所, 転出先, 届出日, 世帯主, 続柄, 生年月日, 届出の理由, etc.
3. Include section headers and instructional text
4. SKIP: page numbers, decorative elements, example/sample filled-in values, watermarks
5. For each item, estimate:
   - x_pct, y_pct: CENTER position as percentages from top-left (0-100)
   - w_pct, h_pct: WIDTH and HEIGHT as percentages of page size

Return a JSON array of objects in reading order:
[
  {"text": "届出日", "x_pct": 15.5, "y_pct": 8.2, "w_pct": 5.0, "h_pct": 2.0},
  ...
]

Return ONLY the JSON array, no explanation."""
    else:
        prompt = """This is a scanned Japanese government form. Extract ALL Japanese text that represents form field labels.

Instructions:
1. Read the form top-to-bottom, left-to-right
2. Extract field labels like: 届出人, 氏名, 住所, 転出先, 届出日, 世帯主, 続柄, 生年月日, 届出の理由, etc.
3. Include section headers and instructional text
4. SKIP: page numbers, decorative elements, example/sample filled-in values, watermarks

Return a JSON array of strings in reading order.
Return ONLY the JSON array, no explanation."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                    },
                    {"type": "text", "text": prompt}
                ],
            }],
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
            if text.startswith("json"):
                text = text[4:].strip()

        items = json.loads(text)
        if not isinstance(items, list):
            return []

        if include_positions:
            results = []
            for item in items:
                if isinstance(item, dict) and item.get("text"):
                    results.append({
                        "text": str(item["text"]),
                        "x_pct": float(item.get("x_pct", 50)),
                        "y_pct": float(item.get("y_pct", 50)),
                        "w_pct": float(item.get("w_pct", 5)),
                        "h_pct": float(item.get("h_pct", 2)),
                    })
            return results
        else:
            return [str(item) for item in items if item]

    except Exception as e:
        print(f"ERROR: Vision fallback failed: {e}")
        return []


# ═══════════════════════════════════════════
# MAIN EXTRACTION FUNCTION
# ═══════════════════════════════════════════

def extract_text_from_image(image, api_key: str = None, include_positions: bool = True,
                            dictionary: dict = None) -> list:
    """
    Extract Japanese field labels from a form image.

    Pipeline:
      1. easyOCR detects all text regions with precise pixel bounding boxes
      2. Dictionary pre-filter identifies known labels (free, instant)
      3. Claude Vision classifies remaining detections as labels or noise
      4. Results returned with easyOCR's precise positions

    Args:
        image: PIL Image
        api_key: Anthropic API key (for Claude Vision filtering)
        include_positions: If True, returns dicts with text and positions.
                          If False, returns list of strings.
        dictionary: Optional field dictionary for pre-filtering known labels.

    Returns:
        If include_positions=True:
            List of dicts: [{"text": "届出日", "x_pct": 15.5, "y_pct": 8.2,
                             "w_pct": 5.0, "h_pct": 2.0}, ...]
        If include_positions=False:
            List of strings: ["届出日", "届出人", ...]
    """
    if not HAS_EASYOCR:
        print("    WARN: easyocr not available, falling back to Claude Vision only")
        return _vision_only_extract(image, api_key, include_positions)

    # Step 1: easyOCR detection (precise bounding boxes + text)
    print("    OCR: Running easyOCR detection...")
    detections = _easyocr_detect(image)
    print(f"    OCR: easyOCR found {len(detections)} text regions")

    if not detections:
        print("    OCR: No text detected, falling back to Claude Vision")
        return _vision_only_extract(image, api_key, include_positions)

    img_w, img_h = image.size

    # Step 2: Dictionary pre-filter (free, instant)
    dict_matched, remaining = _dictionary_filter(detections, dictionary)
    if dict_matched:
        print(f"    OCR: Dictionary identified {len(dict_matched)} known labels")

    # Step 3: Claude Vision filters remaining detections
    vision_results = []
    if remaining:
        print(f"    OCR: Sending {len(remaining)} unclassified detections to Claude Vision...")
        labels = _vision_filter_detections(image, remaining, api_key)
        print(f"    OCR: Claude identified {len(labels)} field labels")

        for label in labels:
            resolved = _resolve_label_bbox(label, remaining)
            if resolved:
                text, x_min, y_min, x_max, y_max = resolved
                vision_results.append({
                    "text": text,
                    "x_min": x_min,
                    "y_min": y_min,
                    "x_max": x_max,
                    "y_max": y_max,
                })

    # Step 4: Combine and format
    all_labels = []

    for det in dict_matched:
        all_labels.append({
            "text": det["text"],
            "x_min": det["x_min"],
            "y_min": det["y_min"],
            "x_max": det["x_max"],
            "y_max": det["y_max"],
        })

    all_labels.extend(vision_results)

    # Sort by reading order (top-to-bottom, left-to-right)
    all_labels.sort(key=lambda l: (l["y_min"], l["x_min"]))

    print(f"    OCR: Total {len(all_labels)} field labels "
          f"({len(dict_matched)} dict, {len(vision_results)} vision)")

    if include_positions:
        results = []
        for label in all_labels:
            cx = (label["x_min"] + label["x_max"]) / 2
            cy = (label["y_min"] + label["y_max"]) / 2
            w = label["x_max"] - label["x_min"]
            h = label["y_max"] - label["y_min"]

            results.append({
                "text": label["text"],
                "x_pct": (cx / img_w) * 100,
                "y_pct": (cy / img_h) * 100,
                "w_pct": (w / img_w) * 100,
                "h_pct": (h / img_h) * 100,
            })
        return results
    else:
        return [l["text"] for l in all_labels]


# ═══════════════════════════════════════════
# PDF / IMAGE WRAPPERS
# ═══════════════════════════════════════════

def _load_default_dictionary():
    """Try to load the field dictionary from the default location."""
    dict_path = Path(__file__).parent.parent / "data" / "fields" / "dictionary.json"
    if dict_path.exists():
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Dictionary file has {"_meta": ..., "fields": {...}} structure
            return data.get("fields", data)
        except Exception:
            pass
    return None


def extract_from_pdf(pdf_path: Path, page_num: int = None,
                     include_positions: bool = True, dictionary: dict = None) -> dict:
    """
    Extract Japanese text from a PDF.

    Args:
        pdf_path: Path to the PDF file
        page_num: Specific page (0-indexed), or None for all pages
        include_positions: If True, include x_pct/y_pct positions for each text
        dictionary: Optional field dictionary for pre-filtering

    Returns:
        dict with structure:
        {
            "source": "path/to/file.pdf",
            "pages": [
                {
                    "page": 0,
                    "width_pts": 595,
                    "height_pts": 842,
                    "items": [
                        {"text": "届出日", "x_pct": 15.5, "y_pct": 8.2, ...},
                        ...
                    ]
                },
                ...
            ]
        }
    """
    result = {
        "source": str(pdf_path),
        "pages": []
    }

    # Get page dimensions
    page_dims = []
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            page_dims = [(p.width, p.height) for p in pdf.pages]
    except Exception:
        pass

    if page_num is not None:
        pages_to_process = [page_num]
    else:
        pages_to_process = range(get_pdf_page_count(pdf_path))

    for pnum in pages_to_process:
        print(f"  Processing page {pnum + 1}...")

        image = render_pdf_page(pdf_path, pnum)
        if image is None:
            print(f"  WARN: Could not render page {pnum + 1}")
            continue

        items = extract_text_from_image(image, include_positions=include_positions,
                                        dictionary=dictionary)
        print(f"  Found {len(items)} text items on page {pnum + 1}")

        # Get page dimensions
        if pnum < len(page_dims):
            width_pts, height_pts = page_dims[pnum]
        else:
            width_pts, height_pts = 595, 842  # A4 default

        result["pages"].append({
            "page": pnum,
            "width_pts": float(width_pts),
            "height_pts": float(height_pts),
            "items": items
        })

    return result


def extract_from_image(image_path: Path, include_positions: bool = True,
                       dictionary: dict = None) -> dict:
    """
    Extract Japanese text from an image file.

    Returns:
        dict with same structure as extract_from_pdf.
    """
    if not HAS_PIL:
        print("ERROR: PIL not installed. Run: pip install Pillow")
        return {"source": str(image_path), "pages": []}

    try:
        image = Image.open(image_path)
    except Exception as e:
        print(f"ERROR: Could not open image: {e}")
        return {"source": str(image_path), "pages": []}

    items = extract_text_from_image(image, include_positions=include_positions,
                                    dictionary=dictionary)
    print(f"  Found {len(items)} text items")

    # Estimate page dimensions from image (assume ~200 DPI)
    img_width, img_height = image.size
    width_pts = img_width * 72 / 200
    height_pts = img_height * 72 / 200

    return {
        "source": str(image_path),
        "pages": [{
            "page": 0,
            "width_pts": round(width_pts, 1),
            "height_pts": round(height_pts, 1),
            "items": items
        }]
    }


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Extract Japanese text from scanned forms")
    parser.add_argument("input", type=str, help="Input PDF or image file")
    parser.add_argument("--page", "-p", type=int, help="Specific page number (0-indexed)")
    parser.add_argument("--output", "-o", type=str, help="Output JSON file")
    parser.add_argument("--no-positions", action="store_true", help="Extract text only, no positions")
    parser.add_argument("--no-easyocr", action="store_true", help="Skip easyOCR, use Claude Vision only")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        return 1

    # Force Vision-only mode if requested
    if args.no_easyocr:
        global HAS_EASYOCR
        HAS_EASYOCR = False

    include_positions = not args.no_positions
    dictionary = _load_default_dictionary()
    if dictionary:
        print(f"Loaded dictionary ({len(dictionary)} entries)")

    print(f"Extracting text from: {input_path.name}")

    # Process based on file type
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        result = extract_from_pdf(input_path, args.page,
                                  include_positions=include_positions,
                                  dictionary=dictionary)
    elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".bmp"):
        result = extract_from_image(input_path, include_positions=include_positions,
                                    dictionary=dictionary)
    else:
        print(f"ERROR: Unsupported file type: {suffix}")
        return 1

    # Output
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nSaved to: {output_path}")
    else:
        # Print to stdout
        print("\n" + "=" * 70)
        for page_data in result["pages"]:
            dims = f"({page_data.get('width_pts', '?')} x {page_data.get('height_pts', '?')} pts)"
            print(f"\nPage {page_data['page'] + 1} {dims}:")

            items = page_data.get("items", [])
            if items and isinstance(items[0], dict):
                print(f"  {'#':<4} {'X%':<6} {'Y%':<6} {'W%':<5} {'H%':<5} Text")
                print("  " + "-" * 65)
                for i, item in enumerate(items, 1):
                    x = f"{item.get('x_pct', 0):.1f}"
                    y = f"{item.get('y_pct', 0):.1f}"
                    w = f"{item.get('w_pct', 0):.1f}"
                    h = f"{item.get('h_pct', 0):.1f}"
                    print(f"  {i:<4} {x:<6} {y:<6} {w:<5} {h:<5} {item.get('text', '')}")
            else:
                for i, text in enumerate(items, 1):
                    print(f"  {i:2}. {text}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
