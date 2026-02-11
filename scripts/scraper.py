#!/usr/bin/env python3
"""
Ward PDF scraper and batch pipeline runner for japan-forms.

Downloads Japanese government form PDFs from all 23 Tokyo special ward websites,
then optionally runs the translation pipeline on each.

Usage:
    python scraper.py --list                          # Show all configured wards
    python scraper.py --scrape                        # Download PDFs from all wards
    python scraper.py --scrape --ward adachi          # Single ward
    python scraper.py --generate                      # Run pipeline on all downloaded PDFs
    python scraper.py --scrape --generate --manifest  # Full pipeline + manifest
"""

import argparse
import json
import os
import re
import sys
import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install requests beautifulsoup4")
    sys.exit(1)

# ── Paths ──
BASE_DIR = Path(__file__).parent.parent
DOWNLOADS_DIR = BASE_DIR / "input" / "tokyo"
OUTPUT_DIR = BASE_DIR / "output" / "walkthroughs"
MANIFEST_PATH = BASE_DIR / "input" / "manifest.json"

# ── Search terms ──
# Japanese form names we look for in page text and PDF link text
SEARCH_TERMS = [
    "住民異動届",       # Residence change notification
    "転入届",           # Moving-in notification
    "転出届",           # Moving-out notification
    "転居届",           # Change of address within ward
    "住民異動届出書",   # Residence change notification form
    "引越し手続",       # Moving procedures guide
    "引っ越し手続",     # Moving procedures guide (alternate)
]

# ── Request config ──
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}
TIMEOUT = 30

# ═══════════════════════════════════════════════════════════════
# WARD CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════
# Each ward has:
#   name_ja/name_en: Display names
#   domain: Base URL for resolving relative links
#   download_index: Page listing downloadable forms (or the specific form page)
#   direct_pdfs: Known direct PDF URLs (skip scraping, just download these)
#   notes: Special handling notes
#
# URLs change frequently — test with --scrape and fix as needed.

WARDS = {
    "adachi": {
        "name_ja": "足立区",
        "name_en": "Adachi Ward",
        "domain": "https://www.city.adachi.tokyo.jp",
        "download_index": "https://www.city.adachi.tokyo.jp/on-line/shinsesho/hikkoshi.html",
        "direct_pdfs": [],
        "notes": "Low English support — high value target",
    },
    "arakawa": {
        "name_ja": "荒川区",
        "name_en": "Arakawa Ward",
        "domain": "https://www.city.arakawa.tokyo.jp",
        "download_index": "https://www.city.arakawa.tokyo.jp/a010/todokede/koseki/tennyu.html",
        "direct_pdfs": [],
        "notes": "Low English support",
    },
    "bunkyo": {
        "name_ja": "文京区",
        "name_en": "Bunkyo Ward",
        "domain": "https://www.city.bunkyo.lg.jp",
        "download_index": "https://www.city.bunkyo.lg.jp/b013/p000260/index.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "chiyoda": {
        "name_ja": "千代田区",
        "name_en": "Chiyoda Ward",
        "domain": "https://www.city.chiyoda.lg.jp",
        "download_index": "https://www.city.chiyoda.lg.jp/koho/kurashi/koseki/jumintoroku/index.html",
        "direct_pdfs": [],
        "notes": "Central Tokyo, well-organized website",
    },
    "chuo": {
        "name_ja": "中央区",
        "name_en": "Chuo Ward",
        "domain": "https://www.city.chuo.lg.jp",
        "download_index": "https://www.city.chuo.lg.jp/kurashi/touroku/sinsei.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "edogawa": {
        "name_ja": "江戸川区",
        "name_en": "Edogawa Ward",
        "domain": "https://www.city.edogawa.tokyo.jp",
        "download_index": "https://www.city.edogawa.tokyo.jp/e038/kuseijoho/denshi/download/kurashi/d_torokushomei/juuminidoutodokede.html",
        "direct_pdfs": [],
        "notes": "Low English support — high value target",
    },
    "itabashi": {
        "name_ja": "板橋区",
        "name_en": "Itabashi Ward",
        "domain": "https://www.city.itabashi.tokyo.jp",
        "download_index": "https://www.city.itabashi.tokyo.jp/tetsuduki/koseki/shomeisho/1001602.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "katsushika": {
        "name_ja": "葛飾区",
        "name_en": "Katsushika Ward",
        "domain": "https://www.city.katsushika.lg.jp",
        "download_index": "https://www.city.katsushika.lg.jp/kurashi/1000046/1001401/1001438.html",
        "direct_pdfs": [],
        "notes": "Low English support — high value target",
    },
    "kita": {
        "name_ja": "北区",
        "name_en": "Kita Ward",
        "domain": "https://www.city.kita.lg.jp",
        "download_index": "https://www.city.kita.lg.jp/living/registration/1001564/1001568.html",
        "direct_pdfs": [
            "https://www.city.kita.lg.jp/_res/projects/default_project/_page_/001/001/568/idoutodokdesyo20260105.pdf",
        ],
        "notes": "Domain changed from city.kita.tokyo.jp to city.kita.lg.jp",
    },
    "koto": {
        "name_ja": "江東区",
        "name_en": "Koto Ward",
        "domain": "https://www.city.koto.lg.jp",
        "download_index": "https://www.city.koto.lg.jp/kurashi/jumin/idotodoke/index.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "meguro": {
        "name_ja": "目黒区",
        "name_en": "Meguro Ward",
        "domain": "https://www.city.meguro.tokyo.jp",
        "download_index": "https://www.city.meguro.tokyo.jp/shinseisho/koseki/index.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "minato": {
        "name_ja": "港区",
        "name_en": "Minato Ward",
        "domain": "https://www.city.minato.tokyo.jp",
        "download_index": "https://www.city.minato.tokyo.jp/shibamadochou/kurashi/todokede/juminhyo.html",
        "direct_pdfs": [],
        "notes": "Has English version of 住民異動届",
    },
    "nakano": {
        "name_ja": "中野区",
        "name_en": "Nakano Ward",
        "domain": "https://www.city.tokyo-nakano.lg.jp",
        "download_index": "https://www.city.tokyo-nakano.lg.jp/kurashi/koseki/yuusou/yuso-tensyutsu.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "nerima": {
        "name_ja": "練馬区",
        "name_en": "Nerima Ward",
        "domain": "https://www.city.nerima.tokyo.jp",
        "download_index": "https://www.city.nerima.tokyo.jp/dl/koseki/index.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "ota": {
        "name_ja": "大田区",
        "name_en": "Ota Ward",
        "domain": "https://www.city.ota.tokyo.jp",
        "download_index": "https://www.city.ota.tokyo.jp/download/koseki/tenshututodoke.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "setagaya": {
        "name_ja": "世田谷区",
        "name_en": "Setagaya Ward",
        "domain": "https://www.city.setagaya.lg.jp",
        "download_index": "https://www.city.setagaya.lg.jp/02233/88.html",
        "direct_pdfs": [],
        "notes": "Largest ward by population",
    },
    "shibuya": {
        "name_ja": "渋谷区",
        "name_en": "Shibuya Ward",
        "domain": "https://www.city.shibuya.tokyo.jp",
        "download_index": "https://www.city.shibuya.tokyo.jp/kurashi/jumin/ido/",
        "direct_pdfs": [],
        "notes": "",
    },
    "shinagawa": {
        "name_ja": "品川区",
        "name_en": "Shinagawa Ward",
        "domain": "https://www.city.shinagawa.tokyo.jp",
        "download_index": "https://www.city.shinagawa.tokyo.jp/PC/procedure/procedure-zyuumin_inkan/procedure-zyuumin_inkan-zyuumin/procedure-zyuumin_inkan-zyuumin-todoke/index.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "shinjuku": {
        "name_ja": "新宿区",
        "name_en": "Shinjuku Ward",
        "domain": "https://www.city.shinjuku.lg.jp",
        "download_index": "https://www.city.shinjuku.lg.jp/todokede/koseki03_000011.html",
        "direct_pdfs": [],
        "notes": "Has Chinese/Korean versions",
    },
    "suginami": {
        "name_ja": "杉並区",
        "name_en": "Suginami Ward",
        "domain": "https://www.city.suginami.tokyo.jp",
        "download_index": "https://www.city.suginami.tokyo.jp/shinseisho/koseki/tenshutsu/1005939.html",
        "direct_pdfs": [
            "https://www.city.suginami.tokyo.jp/documents/8731/todokedesyo1229.pdf",
            "https://www.city.suginami.tokyo.jp/documents/8731/kinyurei1229.pdf",
        ],
        "notes": "Domain migrating to suginami.lg.jp but old domain still serves files",
    },
    "sumida": {
        "name_ja": "墨田区",
        "name_en": "Sumida Ward",
        "domain": "https://www.city.sumida.lg.jp",
        "download_index": "https://www.city.sumida.lg.jp/kurashi/todokede_syoumei/zyuumintouroku/zyuumin_touroku.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "taito": {
        "name_ja": "台東区",
        "name_en": "Taito Ward",
        "domain": "https://www.city.taito.lg.jp",
        "download_index": "https://www.city.taito.lg.jp/kurashi/todokede/jyuminhyo/hikkoshi/jumintoroku.html",
        "direct_pdfs": [],
        "notes": "",
    },
    "toshima": {
        "name_ja": "豊島区",
        "name_en": "Toshima Ward",
        "domain": "https://www.city.toshima.lg.jp",
        "download_index": "https://www.city.toshima.lg.jp/096/tetsuzuki/todokede/kiroku/1809081634.html",
        "direct_pdfs": [],
        "notes": "",
    },
}


# ═══════════════════════════════════════════════════════════════
# SCRAPER
# ═══════════════════════════════════════════════════════════════

def fetch_page(url):
    """Fetch a page and return BeautifulSoup object, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        # Try to detect encoding from content-type or meta tags
        resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"    FAIL: {e}")
        return None


def find_pdf_links(soup, base_url):
    """Extract all PDF links from a page. Returns list of (url, link_text)."""
    pdfs = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            full_url = urljoin(base_url, href)
            # Get link text — include surrounding text for context
            text = a.get_text(strip=True)
            # Also check parent element text for context
            parent_text = ""
            if a.parent:
                parent_text = a.parent.get_text(strip=True)
            pdfs.append({
                "url": full_url,
                "link_text": text,
                "context": parent_text,
            })
    return pdfs


def matches_search_terms(pdf_info):
    """Check if a PDF link matches any of our search terms."""
    combined = pdf_info["link_text"] + " " + pdf_info["context"]
    for term in SEARCH_TERMS:
        if term in combined:
            return True
    return False


# Keywords that suggest a sub-page might contain our target forms
SUBPAGE_KEYWORDS = [
    "住民異動", "転入", "転出", "転居", "引越", "引っ越",
    "届出", "届け出", "ダウンロード", "申請書", "様式",
]


def find_relevant_subpages(soup, base_url, domain):
    """Find links on a page that might lead to form download sub-pages."""
    subpages = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = urljoin(base_url, href)
        # Only follow links within the same domain
        if domain not in full_url:
            continue
        # Skip PDFs, anchors, mailto, javascript
        if any(full_url.lower().endswith(ext) for ext in [".pdf", ".xlsx", ".xls", ".doc", ".docx"]):
            continue
        if full_url.startswith(("mailto:", "javascript:", "tel:")):
            continue
        if full_url in seen:
            continue
        # Check if link text contains relevant keywords
        text = a.get_text(strip=True)
        if any(kw in text for kw in SUBPAGE_KEYWORDS):
            seen.add(full_url)
            subpages.append({"url": full_url, "text": text})
    return subpages


def download_pdf(url, dest_path):
    """Download a PDF to the given path. Returns True on success."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not url.lower().endswith(".pdf"):
            print(f"    WARN: Content-Type is '{content_type}', may not be a PDF")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        size_kb = dest_path.stat().st_size / 1024
        print(f"    OK: {dest_path.name} ({size_kb:.0f} KB)")
        return True
    except requests.RequestException as e:
        print(f"    FAIL download: {e}")
        return False


def scrape_ward(ward_key, ward_cfg):
    """Scrape PDFs for a single ward. Returns list of downloaded file paths."""
    name = f"{ward_cfg['name_en']} ({ward_cfg['name_ja']})"
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    downloaded = []
    ward_dir = DOWNLOADS_DIR / ward_key

    # 1. Direct PDFs first (if configured)
    for pdf_url in ward_cfg.get("direct_pdfs", []):
        print(f"  Direct: {pdf_url}")
        filename = urlparse(pdf_url).path.split("/")[-1]
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        dest = ward_dir / filename
        if dest.exists():
            print(f"    SKIP: already downloaded")
            downloaded.append(dest)
        elif download_pdf(pdf_url, dest):
            downloaded.append(dest)

    # 2. Scrape download_index page
    index_url = ward_cfg.get("download_index")
    if index_url:
        print(f"  Index:  {index_url}")
        soup = fetch_page(index_url)
        if soup:
            pdf_links = find_pdf_links(soup, index_url)
            print(f"    Found {len(pdf_links)} PDF link(s) on page")

            # Filter to matching forms
            matching = [p for p in pdf_links if matches_search_terms(p)]
            if matching:
                print(f"    {len(matching)} match search terms")
                for pdf_info in matching:
                    filename = urlparse(pdf_info["url"]).path.split("/")[-1]
                    dest = ward_dir / filename
                    if dest.exists():
                        print(f"    SKIP: {filename} (already downloaded)")
                        downloaded.append(dest)
                    else:
                        print(f"    Downloading: {pdf_info['link_text'][:50]}")
                        if download_pdf(pdf_info["url"], dest):
                            downloaded.append(dest)
            else:
                # No matching PDFs on index page — crawl sub-pages
                domain = ward_cfg["domain"]
                subpages = find_relevant_subpages(soup, index_url, domain)
                if subpages:
                    print(f"    No PDFs on index. Crawling {len(subpages)} sub-page(s)...")
                    for sp in subpages[:15]:  # Limit to avoid excessive crawling
                        sub_soup = fetch_page(sp["url"])
                        if not sub_soup:
                            continue
                        sub_pdfs = find_pdf_links(sub_soup, sp["url"])
                        sub_matching = [p for p in sub_pdfs if matches_search_terms(p)]
                        if sub_matching:
                            print(f"    Found {len(sub_matching)} PDF(s) on: {sp['text'][:40]}")
                            for pdf_info in sub_matching:
                                filename = urlparse(pdf_info["url"]).path.split("/")[-1]
                                dest = ward_dir / filename
                                if dest.exists():
                                    print(f"    SKIP: {filename} (already downloaded)")
                                    downloaded.append(dest)
                                else:
                                    print(f"    Downloading: {pdf_info['link_text'][:50]}")
                                    if download_pdf(pdf_info["url"], dest):
                                        downloaded.append(dest)
                        if downloaded:
                            break  # Stop after first successful sub-page
                if not downloaded:
                    print(f"    NO MATCHES after crawling. PDF links on index:")
                    for p in pdf_links[:10]:
                        print(f"      - {p['link_text'][:60]}  →  {p['url'][-40:]}")
                    if not pdf_links:
                        print(f"      (none)")
        else:
            print(f"    Could not fetch index page")

    if not downloaded:
        print(f"  RESULT: No PDFs downloaded for {ward_key}")
    else:
        print(f"  RESULT: {len(downloaded)} PDF(s) downloaded")

    return downloaded


# ═══════════════════════════════════════════════════════════════
# MANIFEST
# ═══════════════════════════════════════════════════════════════

def generate_manifest():
    """Generate a manifest of all downloaded PDFs."""
    manifest = {"wards": {}}
    for ward_key in sorted(WARDS.keys()):
        ward_dir = DOWNLOADS_DIR / ward_key
        if not ward_dir.exists():
            continue
        pdfs = sorted(ward_dir.glob("*.pdf"))
        if pdfs:
            manifest["wards"][ward_key] = {
                "name_en": WARDS[ward_key]["name_en"],
                "name_ja": WARDS[ward_key]["name_ja"],
                "pdfs": [
                    {
                        "filename": p.name,
                        "path": str(p.relative_to(BASE_DIR)),
                        "size_kb": round(p.stat().st_size / 1024, 1),
                        "md5": hashlib.md5(p.read_bytes()).hexdigest(),
                    }
                    for p in pdfs
                ],
            }

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    total = sum(len(w["pdfs"]) for w in manifest["wards"].values())
    print(f"\nManifest: {total} PDFs across {len(manifest['wards'])} wards")
    print(f"  Written to: {MANIFEST_PATH.relative_to(BASE_DIR)}")
    return manifest


# ═══════════════════════════════════════════════════════════════
# BATCH GENERATE
# ═══════════════════════════════════════════════════════════════

# Skip PDFs that are non-Japanese (English, Chinese, Korean versions)
NON_JAPANESE_PATTERNS = [
    r'english',
    r'eigo',
    r'英語',
    r'chinese',
    r'中国語',
    r'chuugoku',
    r'korean',
    r'韓国語',
    r'kankoku',
]

# Specific filenames to exclude (non-Japanese without language markers in filename)
NON_JAPANESE_FILES = {
    # Koto - English and Chinese proxy forms
    "20251217171938.pdf",  # English version
    "20251217171956.pdf",  # Chinese version
    # Shinjuku - Korean/Chinese versions
    "000415350.pdf",       # Korean/Chinese
    "000253173.pdf",       # Chinese/Korean
}


def is_japanese_only_pdf(filename):
    """Check if PDF is Japanese-only (not English/Chinese/Korean version)."""
    # Check explicit exclusion list
    if filename in NON_JAPANESE_FILES:
        return False
    # Check patterns in filename
    name_lower = filename.lower()
    for pattern in NON_JAPANESE_PATTERNS:
        if re.search(pattern, name_lower, re.IGNORECASE):
            return False
    return True


def run_generate():
    """Run the translation pipeline on all downloaded PDFs (Japanese only)."""
    try:
        from pipeline import process_pdf
    except ImportError:
        print("ERROR: pipeline.py not found. Cannot run --generate.")
        print("  Place pipeline.py in the scripts/ directory.")
        return

    for ward_key in sorted(WARDS.keys()):
        ward_dir = DOWNLOADS_DIR / ward_key
        if not ward_dir.exists():
            continue
        all_pdfs = sorted(ward_dir.glob("*.pdf"))
        # Filter to Japanese-only PDFs
        pdfs = [p for p in all_pdfs if is_japanese_only_pdf(p.name)]
        skipped = len(all_pdfs) - len(pdfs)

        if not pdfs:
            continue

        name = WARDS[ward_key]["name_en"]
        print(f"\n{'='*60}")
        print(f"  Generating guides for {name}")
        if skipped:
            print(f"  (Skipping {skipped} non-Japanese PDF(s))")
        print(f"{'='*60}")

        # Pipeline will create tokyo/{ward} subdirectory based on input path
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        for pdf_path in pdfs:
            print(f"  Processing: {pdf_path.name}")
            try:
                process_pdf(str(pdf_path), str(OUTPUT_DIR))
            except Exception as e:
                print(f"    ERROR: {e}")


# ═══════════════════════════════════════════════════════════════
# LIST
# ═══════════════════════════════════════════════════════════════

def list_wards():
    """Print a summary of all configured wards."""
    print(f"\n{'Ward':<14} {'Name':<20} {'Japanese':<8} {'Domain':<40} {'Notes'}")
    print("-" * 110)
    for key in sorted(WARDS.keys()):
        w = WARDS[key]
        # Check if any PDFs already downloaded
        ward_dir = DOWNLOADS_DIR / key
        pdf_count = len(list(ward_dir.glob("*.pdf"))) if ward_dir.exists() else 0
        dl = f"[{pdf_count} PDF]" if pdf_count else ""
        notes = w.get("notes", "")
        print(f"  {key:<12} {w['name_en']:<20} {w['name_ja']:<8} {w['domain']:<40} {dl} {notes}")
    print(f"\n  Total: {len(WARDS)} wards configured")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Japanese government form PDFs from Tokyo ward websites"
    )
    parser.add_argument("--list", action="store_true", help="List all configured wards")
    parser.add_argument("--scrape", action="store_true", help="Download PDFs from ward websites")
    parser.add_argument("--ward", type=str, help="Scrape only this ward (use with --scrape)")
    parser.add_argument("--generate", action="store_true", help="Run translation pipeline on downloaded PDFs")
    parser.add_argument("--manifest", action="store_true", help="Generate manifest of downloaded PDFs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be scraped without downloading")
    args = parser.parse_args()

    if not any([args.list, args.scrape, args.generate, args.manifest]):
        parser.print_help()
        return

    if args.list:
        list_wards()
        return

    if args.scrape:
        results = {}
        if args.ward:
            ward_key = args.ward.lower().replace("-ku", "").replace(" ", "")
            if ward_key not in WARDS:
                print(f"Unknown ward: '{args.ward}'")
                print(f"Available: {', '.join(sorted(WARDS.keys()))}")
                return
            results[ward_key] = scrape_ward(ward_key, WARDS[ward_key])
        else:
            for ward_key in sorted(WARDS.keys()):
                results[ward_key] = scrape_ward(ward_key, WARDS[ward_key])

        # Summary
        print(f"\n{'='*60}")
        print("  SCRAPE SUMMARY")
        print(f"{'='*60}")
        success = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"  {success}/{total} wards yielded PDFs\n")
        for ward_key in sorted(results.keys()):
            count = len(results[ward_key])
            status = f"{count} PDF(s)" if count else "NONE"
            print(f"  {ward_key:<14} {status}")

    if args.manifest:
        generate_manifest()

    if args.generate:
        run_generate()


if __name__ == "__main__":
    main()
