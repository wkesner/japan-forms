#!/usr/bin/env python3
"""
Position mapping for OCR-extracted text.

Takes Japanese text (already extracted and translated) and locates each item
on the source image. Returns coordinates for annotation/cropping.

This separates the "what text exists" problem from the "where is it" problem.

Usage:
    python scripts/ocr_position.py image.png --texts "届出日" "届出人" "氏名"
    python scripts/ocr_position.py image.png --json extracted.json
    python scripts/ocr_position.py input.pdf --page 0 --json extracted.json
"""

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

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


def locate_texts_on_image(
    image,
    texts: list[str],
    page_width_pts: float = 595,  # A4 width
    page_height_pts: float = 842,  # A4 height
    api_key: str = None
) -> list[dict]:
    """
    Use Claude Vision to locate specific text items on an image.

    Args:
        image: PIL Image
        texts: List of Japanese text strings to locate (in order)
        page_width_pts: PDF page width in points
        page_height_pts: PDF page height in points
        api_key: Anthropic API key

    Returns:
        List of dicts with structure:
        [
            {
                "index": 0,
                "text": "届出日",
                "x_pct": 15.5,
                "y_pct": 8.2,
                "x0": 92.2,  # PDF coordinates
                "y0": 69.0,
                "found": True
            },
            ...
        ]
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

    if not texts:
        return []

    img_b64 = image_to_base64(image)
    img_width, img_height = image.size

    # Build numbered list of texts to find
    text_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))

    client = anthropic.Anthropic(api_key=api_key)

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
                    {
                        "type": "text",
                        "text": f"""Locate each of these Japanese text items on the form image.

Items to find:
{text_list}

For each item, provide the CENTER position as percentages (0-100) from the top-left corner.

Return a JSON array in the same order as the input list:
[
  {{"index": 1, "text": "...", "x_pct": 15.5, "y_pct": 8.2, "found": true}},
  {{"index": 2, "text": "...", "x_pct": 45.0, "y_pct": 12.1, "found": true}},
  {{"index": 3, "text": "...", "x_pct": 0, "y_pct": 0, "found": false}}
]

If you cannot find a text item, set found=false and use 0 for coordinates.
Return ONLY the JSON array."""
                    }
                ],
            }],
        )

        # Parse response
        text = response.content[0].text.strip()

        # Handle markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
            if text.startswith("json"):
                text = text[4:].strip()

        items = json.loads(text)

        # Convert to PDF coordinates and normalize
        results = []
        for i, original_text in enumerate(texts):
            # Find matching item in response
            match = None
            for item in items:
                if item.get("index") == i + 1:
                    match = item
                    break

            if match and match.get("found", False):
                x_pct = float(match.get("x_pct", 0))
                y_pct = float(match.get("y_pct", 0))

                # Convert percentages to PDF points
                x0 = (x_pct / 100) * page_width_pts
                y0 = (y_pct / 100) * page_height_pts

                results.append({
                    "index": i,
                    "text": original_text,
                    "x_pct": x_pct,
                    "y_pct": y_pct,
                    "x0": round(x0, 1),
                    "y0": round(y0, 1),
                    "found": True
                })
            else:
                results.append({
                    "index": i,
                    "text": original_text,
                    "x_pct": 0,
                    "y_pct": 0,
                    "x0": 0,
                    "y0": 0,
                    "found": False
                })

        return results

    except json.JSONDecodeError as e:
        print(f"ERROR: Could not parse Vision response as JSON: {e}")
        return []
    except Exception as e:
        print(f"ERROR: Vision API call failed: {e}")
        return []


def locate_texts_batch(
    image,
    texts: list[str],
    page_width_pts: float = 595,
    page_height_pts: float = 842,
    batch_size: int = 20,
    api_key: str = None
) -> list[dict]:
    """
    Locate texts in batches to handle large lists.

    Args:
        image: PIL Image
        texts: List of Japanese text strings
        page_width_pts: PDF page width in points
        page_height_pts: PDF page height in points
        batch_size: Max texts per API call
        api_key: Anthropic API key

    Returns:
        Combined list of position results
    """
    all_results = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"  Locating batch {i // batch_size + 1} ({len(batch)} items)...")

        results = locate_texts_on_image(
            image,
            batch,
            page_width_pts=page_width_pts,
            page_height_pts=page_height_pts,
            api_key=api_key
        )

        # Adjust indices for batch offset
        for r in results:
            r["index"] += i

        all_results.extend(results)

    return all_results


def get_pdf_page_dimensions(pdf_path: Path, page_num: int = 0) -> tuple[float, float]:
    """Get PDF page dimensions in points."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < len(pdf.pages):
                page = pdf.pages[page_num]
                return page.width, page.height
    except Exception:
        pass
    return 595, 842  # Default A4


def main():
    parser = argparse.ArgumentParser(description="Locate text positions on form images")
    parser.add_argument("input", type=str, help="Input image or PDF file")
    parser.add_argument("--page", "-p", type=int, default=0, help="PDF page number (0-indexed)")
    parser.add_argument("--texts", "-t", nargs="+", help="Text items to locate")
    parser.add_argument("--json", "-j", type=str, help="JSON file with texts (from ocr.py)")
    parser.add_argument("--output", "-o", type=str, help="Output JSON file")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        return 1

    # Get texts to locate
    texts = []
    if args.json:
        json_path = Path(args.json)
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Find texts for the specified page
        for page_data in data.get("pages", []):
            if page_data.get("page") == args.page:
                texts = page_data.get("texts", [])
                break

        if not texts and data.get("pages"):
            # Fallback to first page
            texts = data["pages"][0].get("texts", [])

    elif args.texts:
        texts = args.texts
    else:
        print("ERROR: Provide --texts or --json")
        return 1

    if not texts:
        print("ERROR: No texts to locate")
        return 1

    print(f"Locating {len(texts)} text items on: {input_path.name}")

    # Load image
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        image = render_pdf_page(input_path, args.page)
        page_width, page_height = get_pdf_page_dimensions(input_path, args.page)
    elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".bmp"):
        if not HAS_PIL:
            print("ERROR: PIL not installed. Run: pip install Pillow")
            return 1
        image = Image.open(input_path)
        # Assume A4 proportions for images
        page_width, page_height = 595, 842
    else:
        print(f"ERROR: Unsupported file type: {suffix}")
        return 1

    if image is None:
        print("ERROR: Could not load image")
        return 1

    # Locate texts
    results = locate_texts_batch(
        image,
        texts,
        page_width_pts=page_width,
        page_height_pts=page_height
    )

    # Summary
    found = sum(1 for r in results if r["found"])
    print(f"\nLocated {found}/{len(texts)} text items")

    # Output
    output_data = {
        "source": str(input_path),
        "page": args.page,
        "page_width_pts": page_width,
        "page_height_pts": page_height,
        "positions": results
    }

    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        print(f"Saved to: {output_path}")
    else:
        # Print to stdout
        print("\n" + "=" * 60)
        print(f"{'#':<4} {'Found':<6} {'X%':<8} {'Y%':<8} Text")
        print("=" * 60)
        for r in results:
            status = "Yes" if r["found"] else "No"
            x_str = f"{r['x_pct']:.1f}" if r["found"] else "-"
            y_str = f"{r['y_pct']:.1f}" if r["found"] else "-"
            print(f"{r['index']+1:<4} {status:<6} {x_str:<8} {y_str:<8} {r['text'][:30]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
