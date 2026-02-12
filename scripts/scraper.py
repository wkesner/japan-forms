#!/usr/bin/env python3
"""
Ward PDF scraper and batch pipeline runner for japan-forms.

Downloads Japanese government form PDFs from all 23 Tokyo special ward websites,
then optionally runs the translation pipeline on each.

Usage:
    python scraper.py --list                          # Show all configured wards
    python scraper.py --scrape                        # Download residence PDFs from all wards
    python scraper.py --scrape --form-type nhi        # Download NHI PDFs from all wards
    python scraper.py --scrape --ward adachi          # Single ward
    python scraper.py --generate                      # Run pipeline on all downloaded PDFs
    python scraper.py --scrape --generate --manifest  # Full pipeline + manifest
    python scraper.py --status                        # Regenerate STATUS.md
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


def matches_search_terms(pdf_info, search_terms=None):
    """Check if a PDF link matches any of the given search terms."""
    if search_terms is None:
        search_terms = SEARCH_TERMS
    combined = pdf_info["link_text"] + " " + pdf_info["context"]
    for term in search_terms:
        if term in combined:
            return True
    return False


# Keywords that suggest a sub-page might contain our target forms
SUBPAGE_KEYWORDS = [
    "住民異動", "転入", "転出", "転居", "引越", "引っ越",
    "届出", "届け出", "ダウンロード", "申請書", "様式",
]


# ═══════════════════════════════════════════════════════════════
# FORM TYPE CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════
# Each form type defines search terms for matching PDFs, subpage keywords
# for crawling, and the downloads subdirectory under input/.

FORM_TYPES = {
    "residence": {
        "label": "Residence Registration (住民異動届)",
        "search_terms": SEARCH_TERMS,
        "subpage_keywords": SUBPAGE_KEYWORDS,
        "downloads_subdir": "tokyo",
        "form_id": "residence_registration",
    },
    "nhi": {
        "label": "National Health Insurance (国民健康保険)",
        "search_terms": [
            "国民健康保険",       # National Health Insurance
            "国保",               # NHI abbreviation
            "異動届出書",         # Change notification form
            "資格取得届",         # Enrollment form
            "資格喪失届",         # Disenrollment form
            "被保険者",           # Insured person
        ],
        "subpage_keywords": [
            "国民健康保険", "国保", "健康保険",
            "加入", "資格", "届出", "届け出",
            "ダウンロード", "申請書", "様式",
        ],
        "downloads_subdir": "tokyo_nhi",
        "form_id": "national_health_insurance",
    },
}

# Per-ward NHI index pages (analogous to download_index for residence)
NHI_WARD_INDEX = {
    "adachi": "https://www.city.adachi.tokyo.jp/on-line/shinsesho/kokuho.html",
    "arakawa": "https://www.city.arakawa.tokyo.jp/a031/kenkouhoken/kokuho/todokede.html",
    "bunkyo": "https://www.city.bunkyo.lg.jp/b021/p000478/index.html",
    "chiyoda": "https://www.city.chiyoda.lg.jp/koho/kurashi/hoken/kenkohoken/kokuho.html",
    "chuo": "https://www.city.chuo.lg.jp/a0024/kurashi/hokennenkin/kokuho/kokuhonitsuite/tetsuzuki/kanyu.html",
    "edogawa": "https://www.city.edogawa.tokyo.jp/kurashi/iryohoken/kokuho/shikaku/index.html",
    "itabashi": "https://www.city.itabashi.tokyo.jp/kenko/kokuho/kokuho/1003171.html",
    "katsushika": "https://www.city.katsushika.lg.jp/kurashi/1000049/1001690/1001699.html",
    "kita": "https://www.city.kita.lg.jp/living/insurance-pension/1001703/1001712/1001721.html",
    "koto": "https://www.city.koto.lg.jp/fukushi/kokumin/todokede/index.html",
    "meguro": "https://www.city.meguro.tokyo.jp/shinseisho/kokuho/index.html",
    "minato": "https://www.city.minato.tokyo.jp/shikaku/kurashi/hoken/kenkohoken/todokede.html",
    "nakano": "https://www.city.tokyo-nakano.lg.jp/kurashi/hoken/tetsuduki/index.html",
    "nerima": "https://www.city.nerima.tokyo.jp/dl/nenkinhoken/index.html",
    "ota": "https://www.city.ota.tokyo.jp/seikatsu/kokunen/kokuho/kanyuu.html",
    "setagaya": "https://www.city.setagaya.lg.jp/02060/18761.html",
    "shibuya": "https://www.city.shibuya.tokyo.jp/kurashi/kokuho/kokuho/yuso_tetsuzuki.html",
    "shinagawa": "https://www.city.shinagawa.tokyo.jp/PC/procedure/procedure-kenkouhoken/procedure-kenkouhoken-todokede/20200325095605.html",
    "shinjuku": "https://www.city.shinjuku.lg.jp/hoken/hoken01_002033.html",
    "suginami": "https://www.city.suginami.tokyo.jp/s035/shinseisho/8716.html",
    "sumida": "https://www.city.sumida.lg.jp/online_service/sinsei/kokuho/index.html",
    "taito": "https://www.city.taito.lg.jp/kurashi/zeikin/kokuminkenkohoken/kanyuudattai.html",
    "toshima": "https://www.city.toshima.lg.jp/109/tetsuzuki/nenkin/kenkohoken/todokede/004920.html",
}

# Wards that already have official English NHI forms — skip translation for these
NHI_ENGLISH_WARDS = {
    "bunkyo",     # Form has English subtitle "APPLICATION FOR THE NATIONAL HEALTH INSURANCE"
    "chiyoda",    # Multilingual guidebook (EN/CN/KR) + bilingual form labels
    "shibuya",    # Enrollment & disenrollment forms have full English labels
    "shinjuku",   # Forms have English labels throughout, example uses foreign name
}


def find_relevant_subpages(soup, base_url, domain, subpage_keywords=None):
    """Find links on a page that might lead to form download sub-pages."""
    if subpage_keywords is None:
        subpage_keywords = SUBPAGE_KEYWORDS
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
        if any(kw in text for kw in subpage_keywords):
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


def scrape_ward(ward_key, ward_cfg, form_type="residence"):
    """Scrape PDFs for a single ward. Returns list of downloaded file paths."""
    ft = FORM_TYPES[form_type]
    name = f"{ward_cfg['name_en']} ({ward_cfg['name_ja']})"
    print(f"\n{'='*60}")
    print(f"  {name} — {ft['label']}")
    print(f"{'='*60}")

    downloaded = []
    downloads_dir = BASE_DIR / "input" / ft["downloads_subdir"]
    ward_dir = downloads_dir / ward_key
    search_terms = ft["search_terms"]
    subpage_kw = ft["subpage_keywords"]

    # 1. Direct PDFs first (only for residence — NHI has no pre-known direct URLs)
    if form_type == "residence":
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

    # 2. Get index URL based on form type
    if form_type == "residence":
        index_url = ward_cfg.get("download_index")
    elif form_type == "nhi":
        index_url = NHI_WARD_INDEX.get(ward_key)
    else:
        index_url = None

    if index_url:
        print(f"  Index:  {index_url}")
        soup = fetch_page(index_url)
        if soup:
            pdf_links = find_pdf_links(soup, index_url)
            print(f"    Found {len(pdf_links)} PDF link(s) on page")

            # Filter to matching forms
            matching = [p for p in pdf_links if matches_search_terms(p, search_terms)]
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
                subpages = find_relevant_subpages(soup, index_url, domain, subpage_kw)
                if subpages:
                    print(f"    No PDFs on index. Crawling {len(subpages)} sub-page(s)...")
                    for sp in subpages[:15]:  # Limit to avoid excessive crawling
                        sub_soup = fetch_page(sp["url"])
                        if not sub_soup:
                            continue
                        sub_pdfs = find_pdf_links(sub_soup, sp["url"])
                        sub_matching = [p for p in sub_pdfs if matches_search_terms(p, search_terms)]
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
    else:
        print(f"  No index URL configured for {form_type}")

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
# STATUS GENERATION
# ═══════════════════════════════════════════════════════════════

def generate_status():
    """Generate STATUS.md from current project state."""
    from datetime import date
    try:
        from pipeline import classify_pdf_pages
    except ImportError:
        classify_pdf_pages = None

    STATUS_PATH = BASE_DIR / "STATUS.md"
    trash_dir = BASE_DIR / "input" / "tokyo_trash"

    # Gather per-ward data
    ward_rows = []
    total_walkthroughs = 0
    total_source_pdfs = 0

    for ward_key in sorted(WARDS.keys()):
        ward = WARDS[ward_key]
        ward_dir = DOWNLOADS_DIR / ward_key
        out_dir = OUTPUT_DIR / "tokyo" / ward_key

        # Count source PDFs
        source_pdfs = sorted(ward_dir.glob("*.pdf")) if ward_dir.exists() else []
        num_source = len(source_pdfs)
        total_source_pdfs += num_source

        # Count walkthroughs
        walkthroughs = sorted(out_dir.glob("*_walkthrough.pdf")) if out_dir.exists() else []
        num_walks = len(walkthroughs)
        total_walkthroughs += num_walks

        # Detect source type (text vs OCR)
        source_type = "—"
        if source_pdfs and classify_pdf_pages:
            try:
                pages = classify_pdf_pages(str(source_pdfs[0]))
                image_pages = sum(1 for p in pages if p["type"] == "image")
                text_pages = sum(1 for p in pages if p["type"] == "text")
                if image_pages > text_pages:
                    source_type = "OCR"
                else:
                    source_type = "Text"
            except Exception:
                source_type = "?"

        # Quality heuristic
        if num_walks == 0:
            quality = "—"
        elif num_walks >= 3:
            quality = "Good"
        elif num_walks >= 2:
            quality = "Good"
        else:
            quality = "OK"

        ward_rows.append({
            "key": ward_key,
            "name_ja": ward["name_ja"],
            "name_en": ward["name_en"].replace(" Ward", ""),
            "source_pdfs": num_source,
            "walkthroughs": num_walks,
            "source_type": source_type,
            "quality": quality,
        })

    # Trash guide coverage
    trash_text = []
    trash_scanned = []
    trash_missing = []
    for ward_key in sorted(WARDS.keys()):
        trash_ward_dir = trash_dir / ward_key
        if trash_ward_dir.exists() and list(trash_ward_dir.glob("*.pdf")):
            # Quick check if text or scanned
            pdfs = list(trash_ward_dir.glob("*.pdf"))
            if classify_pdf_pages:
                try:
                    pages = classify_pdf_pages(str(pdfs[0]))
                    if any(p["type"] == "text" for p in pages):
                        trash_text.append(WARDS[ward_key]["name_en"].replace(" Ward", ""))
                    else:
                        trash_scanned.append(WARDS[ward_key]["name_en"].replace(" Ward", ""))
                except Exception:
                    trash_text.append(WARDS[ward_key]["name_en"].replace(" Ward", ""))
            else:
                trash_text.append(WARDS[ward_key]["name_en"].replace(" Ward", ""))
        else:
            trash_missing.append(WARDS[ward_key]["name_en"].replace(" Ward", ""))

    # NHI coverage
    nhi_dir = BASE_DIR / "input" / "tokyo_nhi"
    nhi_sourced = []
    nhi_not_sourced = []
    for ward_key in sorted(WARDS.keys()):
        nhi_ward_dir = nhi_dir / ward_key
        if nhi_ward_dir.exists() and list(nhi_ward_dir.glob("*.pdf")):
            nhi_sourced.append(WARDS[ward_key]["name_en"].replace(" Ward", ""))
        else:
            nhi_not_sourced.append(WARDS[ward_key]["name_en"].replace(" Ward", ""))
    nhi_configured = sum(1 for k in WARDS if k in NHI_WARD_INDEX)

    # Load dictionary count
    dict_count = 0
    dict_path = BASE_DIR / "data" / "fields" / "dictionary.json"
    if dict_path.exists():
        try:
            import json as _json
            data = _json.load(open(dict_path, "r", encoding="utf-8"))
            dict_count = len(data.get("fields", data))
        except Exception:
            pass

    # Build STATUS.md
    lines = []
    lines.append("# Project Status")
    lines.append("")
    lines.append(f"*Last updated: {date.today().isoformat()}*")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Walkthroughs generated | {total_walkthroughs} |")
    lines.append(f"| Wards covered | {sum(1 for w in ward_rows if w['walkthroughs'] > 0)} (Tokyo) |")
    lines.append(f"| Dictionary entries | {dict_count} |")
    lines.append("")
    lines.append("## Document Types")
    lines.append("")
    lines.append("| Document Type | Status | Notes |")
    lines.append("|---------------|--------|-------|")
    lines.append("| Residence Registration (住民異動届) | **Active** | All 23 Tokyo wards, primary focus |")
    lines.append(f"| Trash Disposal Guides (ゴミの出し方) | **Sourced** | {len(trash_text) + len(trash_scanned)} wards downloaded, pipeline adaptation needed |")
    nhi_status = "Sourced" if nhi_sourced else "Configured" if nhi_configured else "Planned"
    nhi_note = f"{len(nhi_sourced)}/23 wards downloaded" if nhi_sourced else f"{nhi_configured}/23 wards configured, not yet scraped"
    lines.append(f"| National Health Insurance (国民健康保険) | **{nhi_status}** | {nhi_note} |")
    lines.append("| Bank Account (口座開設) | **Done** | JP Post personal + corporate |")
    lines.append("| Pension Withdrawal (脱退一時金) | **Done** | National form, single walkthrough |")
    lines.append("| Seal Registration (印鑑登録) | **Planned** | Not yet sourced |")
    lines.append("")
    lines.append("## Tokyo Special Wards (23区) — Residence Registration")
    lines.append("")
    lines.append("All 23 wards have at least one walkthrough. Quality varies by source PDF type.")
    lines.append("")
    lines.append("| Ward | Walkthroughs | Source Type | Quality | Notes |")
    lines.append("|------|:-----------:|:-----------:|:-------:|-------|")
    for w in ward_rows:
        notes = ""
        if w["source_type"] == "OCR":
            notes = "Scanned form, easyOCR positioning"
        lines.append(f"| {w['name_ja']} {w['name_en']} | {w['walkthroughs']} | {w['source_type']} | {w['quality']} | {notes} |")

    lines.append("")
    lines.append("## Trash Disposal Guides (ゴミの出し方)")
    lines.append("")
    lines.append(f"Downloaded for {len(trash_text) + len(trash_scanned)}/23 Tokyo wards. Pipeline adaptation needed before translation.")
    lines.append("")
    lines.append("| Status | Wards |")
    lines.append("|--------|-------|")
    lines.append(f"| **Text-based ({len(trash_text)})** | {', '.join(trash_text)} |")
    if trash_scanned:
        lines.append(f"| **Scanned ({len(trash_scanned)})** | {', '.join(trash_scanned)} |")
    lines.append(f"| **Not sourced ({len(trash_missing)})** | {', '.join(trash_missing)} |")
    lines.append("| **Translated** | *None yet* |")
    lines.append("")
    lines.append("## National Health Insurance (国民健康保険)")
    lines.append("")
    lines.append(f"Scraping targets configured for {nhi_configured}/23 Tokyo wards. Applications are ward-specific, similar to residence registration.")
    lines.append("")
    if nhi_sourced:
        lines.append("| Status | Wards |")
        lines.append("|--------|-------|")
        lines.append(f"| **Downloaded ({len(nhi_sourced)})** | {', '.join(nhi_sourced)} |")
        lines.append(f"| **Not yet scraped ({len(nhi_not_sourced)})** | {', '.join(nhi_not_sourced)} |")
        lines.append("| **Translated** | *None yet* |")
    else:
        lines.append(f"Use `python scraper.py --scrape --form-type nhi` to download NHI forms.")
    lines.append("")
    lines.append("## Regional Coverage Roadmap")
    lines.append("")
    lines.append("### Phase 1: Tokyo (東京都) — In Progress")
    lines.append("")

    wards_with_coverage = sum(1 for w in ward_rows if w["walkthroughs"] > 0)
    pct_residence = round(wards_with_coverage / 23 * 100)
    pct_total_tokyo = round(wards_with_coverage / 53 * 100)

    nhi_count = len(nhi_sourced)
    lines.append("| Area | Municipalities | Residence | Trash | NHI | Coverage |")
    lines.append("|------|:-:|:-:|:-:|:-:|:-:|")
    lines.append(f"| **23 Special Wards** | 23 | {wards_with_coverage}/23 | 0/23 | {nhi_count}/23 | **{pct_residence}%** |")
    lines.append("| Tama Region (多摩地域) | 30 | 0 | 0 | 0 | 0% |")
    lines.append(f"| **Tokyo Total** | 53 | {wards_with_coverage}/53 | 0/53 | {nhi_count}/53 | **{pct_total_tokyo}%** |")
    lines.append("")
    lines.append("### Phase 2: Kanto Region (関東) — Planned")
    lines.append("")
    lines.append("| Prefecture | Capital | Municipalities | Status |")
    lines.append("|-----------|---------|:-:|--------|")
    lines.append("| 東京都 Tokyo | — | 53 | **Phase 1** |")
    lines.append("| 神奈川県 Kanagawa | Yokohama | 33 | Not started |")
    lines.append("| 埼玉県 Saitama | Saitama | 63 | Not started |")
    lines.append("| 千葉県 Chiba | Chiba | 54 | Not started |")
    lines.append("| 茨城県 Ibaraki | Mito | 44 | Not started |")
    lines.append("| 栃木県 Tochigi | Utsunomiya | 25 | Not started |")
    lines.append("| 群馬県 Gunma | Maebashi | 35 | Not started |")
    kanto_total = 307
    kanto_pct = round(wards_with_coverage / kanto_total * 100)
    lines.append(f"| **Kanto Total** | — | **{kanto_total}** | **{kanto_pct}%** |")
    lines.append("")
    lines.append("### Phase 3: Kansai Region (関西) — Planned")
    lines.append("")
    lines.append("| Prefecture | Capital | Municipalities | Status |")
    lines.append("|-----------|---------|:-:|--------|")
    lines.append("| 大阪府 Osaka | Osaka | 43 | Not started |")
    lines.append("| 京都府 Kyoto | Kyoto | 26 | Not started |")
    lines.append("| 兵庫県 Hyogo | Kobe | 41 | Not started |")
    lines.append("| 奈良県 Nara | Nara | 39 | Not started |")
    lines.append("| 滋賀県 Shiga | Otsu | 19 | Not started |")
    lines.append("| 和歌山県 Wakayama | Wakayama | 30 | Not started |")
    lines.append("| **Kansai Total** | — | **198** | **0%** |")
    lines.append("")
    lines.append("### Phase 4: Remaining Regions — Future")
    lines.append("")
    lines.append("| Region | Prefectures | Municipalities (approx.) | Status |")
    lines.append("|--------|:-:|:-:|--------|")
    lines.append("| Chubu (中部) | 9 | ~270 | Not started |")
    lines.append("| Kyushu (九州) | 8 | ~230 | Not started |")
    lines.append("| Tohoku (東北) | 6 | ~220 | Not started |")
    lines.append("| Chugoku (中国) | 5 | ~110 | Not started |")
    lines.append("| Shikoku (四国) | 4 | ~95 | Not started |")
    lines.append("| Hokkaido (北海道) | 1 | ~180 | Not started |")
    lines.append("| Okinawa (沖縄) | 1 | ~41 | Not started |")
    lines.append("")
    lines.append("### National Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append("| Total municipalities in Japan | ~1,741 |")
    lines.append(f"| Municipalities with any coverage | {wards_with_coverage} |")
    nat_pct = round(wards_with_coverage / 1741 * 100, 1)
    lines.append(f"| **National coverage** | **{nat_pct}%** |")
    lines.append("")
    lines.append("## Technical Notes")
    lines.append("")
    lines.append("- **OCR pipeline**: easyOCR for positioning + dictionary pre-filter + Claude Vision for classification")
    lines.append("- **Auto page classification**: Pages with <50 extractable characters route to OCR automatically")
    lines.append("- **Translation hierarchy**: Dictionary (free) → Fragment match (free) → Claude Sonnet (API)")
    lines.append("")

    content = "\n".join(lines)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\nSTATUS.md updated ({total_walkthroughs} walkthroughs, {wards_with_coverage} wards)")
    print(f"  Written to: {STATUS_PATH.relative_to(BASE_DIR)}")


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


def run_generate(form_type="residence"):
    """Run the translation pipeline on all downloaded PDFs (Japanese only)."""
    try:
        from pipeline import process_pdf
    except ImportError:
        print("ERROR: pipeline.py not found. Cannot run --generate.")
        print("  Place pipeline.py in the scripts/ directory.")
        return

    ft = FORM_TYPES[form_type]
    downloads_dir = BASE_DIR / "input" / ft["downloads_subdir"]

    for ward_key in sorted(WARDS.keys()):
        # Skip wards with official English forms for NHI
        if form_type == "nhi" and ward_key in NHI_ENGLISH_WARDS:
            print(f"\n  SKIP: {WARDS[ward_key]['name_en']} — has official English NHI forms")
            continue

        ward_dir = downloads_dir / ward_key
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
        print(f"  Generating guides for {name} — {ft['label']}")
        if skipped:
            print(f"  (Skipping {skipped} non-Japanese PDF(s))")
        print(f"{'='*60}")

        # Pipeline will create tokyo/{ward} subdirectory based on input path
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        for pdf_path in pdfs:
            print(f"  Processing: {pdf_path.name}")
            try:
                process_pdf(str(pdf_path), str(OUTPUT_DIR), form_id=ft["form_id"])
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
    parser.add_argument("--form-type", type=str, default="residence",
                        choices=list(FORM_TYPES.keys()),
                        help="Form type to scrape/generate (default: residence)")
    parser.add_argument("--generate", action="store_true", help="Run translation pipeline on downloaded PDFs")
    parser.add_argument("--manifest", action="store_true", help="Generate manifest of downloaded PDFs")
    parser.add_argument("--status", action="store_true", help="Regenerate STATUS.md from current project state")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be scraped without downloading")
    args = parser.parse_args()

    form_type = getattr(args, 'form_type', 'residence')

    if not any([args.list, args.scrape, args.generate, args.manifest, args.status]):
        parser.print_help()
        return

    if args.list:
        list_wards()
        return

    if args.scrape:
        ft_label = FORM_TYPES[form_type]["label"]
        print(f"\nForm type: {ft_label}")
        results = {}
        if args.ward:
            ward_key = args.ward.lower().replace("-ku", "").replace(" ", "")
            if ward_key not in WARDS:
                print(f"Unknown ward: '{args.ward}'")
                print(f"Available: {', '.join(sorted(WARDS.keys()))}")
                return
            results[ward_key] = scrape_ward(ward_key, WARDS[ward_key], form_type)
        else:
            for ward_key in sorted(WARDS.keys()):
                results[ward_key] = scrape_ward(ward_key, WARDS[ward_key], form_type)

        # Summary
        print(f"\n{'='*60}")
        print(f"  SCRAPE SUMMARY — {ft_label}")
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
        run_generate(form_type)

    # Update STATUS.md only on explicit --status (slow — classifies all PDFs)
    if args.status:
        generate_status()


if __name__ == "__main__":
    main()
