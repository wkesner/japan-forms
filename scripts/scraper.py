#!/usr/bin/env python3
"""
Municipality PDF scraper and batch pipeline runner for japan-forms.

Downloads Japanese government form PDFs from municipality websites,
then optionally runs the translation pipeline on each.

Usage:
    python scraper.py --list                                    # Show configured municipalities
    python scraper.py --list --prefecture kanagawa              # Show Kanagawa municipalities
    python scraper.py --scrape                                  # Download residence PDFs (Tokyo)
    python scraper.py --scrape --form-type nhi                  # Download NHI PDFs (Tokyo)
    python scraper.py --scrape --ward adachi                    # Single ward
    python scraper.py --scrape --prefecture kanagawa            # All Kanagawa municipalities
    python scraper.py --scrape --prefecture kanagawa --municipality yokohama-naka
    python scraper.py --discover --prefecture kanagawa --form-type residence
    python scraper.py --validate --prefecture chiba --form-type residence
    python scraper.py --generate                                # Run pipeline on downloaded PDFs
    python scraper.py --generate --dry-run                      # Preview without generating
    python scraper.py --qa --prefecture tokyo --form-type residence
    python scraper.py --scrape --generate --manifest            # Full pipeline + manifest
    python scraper.py --status                                  # Regenerate STATUS.md
"""

import argparse
import json
import os
import re
import sys
import time
import hashlib
import xml.etree.ElementTree as ET
import heapq
from collections import deque
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
# WARD CONFIGURATIONS (Tokyo fallback — used when JSON registry is empty)
# ═══════════════════════════════════════════════════════════════
# Each ward has:
#   name_ja/name_en: Display names
#   domain: Base URL for resolving relative links
#   download_index: Page listing downloadable forms (or the specific form page)
#   direct_pdfs: Known direct PDF URLs (skip scraping, just download these)
#   notes: Special handling notes
#
# URLs change frequently — test with --scrape and fix as needed.
# These are kept as fallback; canonical data lives in data/municipalities/tokyo/*.json

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
# JSON REGISTRY
# ═══════════════════════════════════════════════════════════════

def load_registry(prefecture):
    """Load municipality configs from JSON files for a given prefecture.

    Returns dict of {muni_key: config} compatible with scrape_ward().
    Falls back to hardcoded WARDS dict for Tokyo if no JSON scraping data found.
    """
    registry = {}
    muni_dir = BASE_DIR / "data" / "municipalities" / prefecture
    if not muni_dir.exists():
        return registry

    for json_path in sorted(muni_dir.glob("*.json")):
        if json_path.name.startswith("_"):
            continue
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, KeyError, OSError):
            continue

        scraping = data.get("scraping")
        if not scraping or not scraping.get("domain"):
            continue

        # Derive key from filename — strip -ku for special wards (backward compat)
        key = json_path.stem
        if data.get("type") == "special_ward" and key.endswith("-ku"):
            key = key[:-3]

        residence = scraping.get("residence", {})
        nhi = scraping.get("nhi", {})

        registry[key] = {
            "name_ja": data["names"]["ja"],
            "name_en": data["names"]["en"],
            "domain": scraping["domain"],
            # Backward-compatible keys for scrape_ward()
            "download_index": residence.get("index_url"),
            "direct_pdfs": residence.get("direct_pdfs", []),
            # Per-form-type scraping config
            "scraping": {
                "residence": residence,
                "nhi": nhi,
            },
            "notes": data.get("population_note", ""),
        }

    return registry


def get_active_registry(prefecture):
    """Get the active municipality registry for a prefecture.

    Tries JSON files first, falls back to hardcoded WARDS for Tokyo.
    """
    registry = load_registry(prefecture)
    if registry:
        return registry
    if prefecture == "tokyo":
        return WARDS
    return {}


def get_downloads_dir(form_type, prefecture):
    """Get the input downloads directory for a form type and prefecture."""
    if form_type == "nhi":
        return BASE_DIR / "input" / f"{prefecture}_nhi"
    return BASE_DIR / "input" / prefecture


# ═══════════════════════════════════════════════════════════════
# SCRAPER
# ═══════════════════════════════════════════════════════════════

_page_cache = {}

def fetch_page(url, use_cache=True):
    """Fetch a page and return BeautifulSoup object, or None on failure.

    Uses a simple in-memory cache to avoid re-fetching the same URL
    across multiple discovery phases.
    """
    if use_cache and url in _page_cache:
        return _page_cache[url]
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        # Try to detect encoding from content-type or meta tags
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        if use_cache:
            _page_cache[url] = soup
        return soup
    except requests.RequestException as e:
        print(f"    FAIL: {e}")
        if use_cache:
            _page_cache[url] = None
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
    "手続き", "各種届出", "住民票", "戸籍", "暮らし", "申請",
    "届出書", "届出様式", "届出用紙",
]


# ═══════════════════════════════════════════════════════════════
# FORM TYPE CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════
# Each form type defines search terms for matching PDFs, subpage keywords
# for crawling. Downloads subdirectory is now computed dynamically via
# get_downloads_dir(form_type, prefecture).

FORM_TYPES = {
    "residence": {
        "label": "Residence Registration (住民異動届)",
        "search_terms": SEARCH_TERMS,
        "subpage_keywords": SUBPAGE_KEYWORDS,
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
        "form_id": "national_health_insurance",
    },
}

# Per-ward NHI index pages — Tokyo fallback (canonical data in JSON files)
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
        text_match = any(kw in text for kw in subpage_keywords)
        # Also check URL path for form-related segments — but only the
        # relative portion (new segments not in the parent page URL) to avoid
        # false matches when the domain/base path contains a keyword
        # (e.g. Kawaguchi's /kurashi_tetsuzuki/ contains "tetsuzuki")
        base_path = urlparse(base_url).path.lower()
        link_path = urlparse(full_url).path.lower()
        relative_path = link_path[len(base_path):] if link_path.startswith(base_path) else link_path
        path_match = any(seg in relative_path for seg in FORM_PATH_SEGMENTS)
        if text_match or path_match:
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


def scrape_ward(ward_key, ward_cfg, form_type="residence", downloads_dir=None):
    """Scrape PDFs for a single municipality. Returns list of downloaded file paths."""
    ft = FORM_TYPES[form_type]
    name = f"{ward_cfg['name_en']} ({ward_cfg['name_ja']})"
    print(f"\n{'='*60}")
    print(f"  {name} — {ft['label']}")
    print(f"{'='*60}")

    downloaded = []
    if downloads_dir is None:
        downloads_dir = get_downloads_dir(form_type, "tokyo")
    ward_dir = downloads_dir / ward_key
    search_terms = ft["search_terms"]
    subpage_kw = ft["subpage_keywords"]

    # Get form-type-specific scraping config (new JSON style)
    ft_cfg = ward_cfg.get("scraping", {}).get(form_type, {})

    # 1. Direct PDFs — check form-type-specific config first, then legacy
    direct_pdfs = ft_cfg.get("direct_pdfs", [])
    if not direct_pdfs and form_type == "residence":
        direct_pdfs = ward_cfg.get("direct_pdfs", [])

    for pdf_url in direct_pdfs:
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

    # 2. Get index URL — check form-type-specific config first, then legacy
    index_url = ft_cfg.get("index_url")
    if not index_url:
        if form_type == "residence":
            index_url = ward_cfg.get("download_index")
        elif form_type == "nhi":
            index_url = NHI_WARD_INDEX.get(ward_key)

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
# DISCOVERY MODE
# ═══════════════════════════════════════════════════════════════

# Path segments that suggest form-related pages
FORM_PATH_SEGMENTS = [
    "todokede", "shinseisho", "download", "kurashi", "tetsuzuki",
    "hoken", "kokuho", "koseki", "jumin", "hikkoshi", "tensyutsu",
    "tennyu", "shinsei", "yoshiki", "dl",
    "kurashi_tetsuzuki",  # Kawaguchi-style underscore variant
    "g_info",             # Yokosuka general info pages
    "madoguchi",          # Window/counter services
    "juminhyo",           # Resident certificate
    "hikkoshi-portal",    # Moving portal pages
    "shinseisho_menu",    # Sagamihara-style form menu
]

# Context keywords that boost candidate scores
DOWNLOAD_CONTEXT_KEYWORDS = [
    "ダウンロード", "申請書", "様式", "届出書", "届け出",
    "PDF", "書式", "用紙",
]

# Common URL path patterns on Japanese municipal websites, per form type.
# Used in Phase 2 (Seed URL Patterns) to probe likely pages before BFS.
MUNICIPAL_SEED_PATTERNS = {
    "residence": [
        # Semantic path variants (most common)
        "/kurashi/todokede/",
        "/kurashi/koseki/jumin/",
        "/kurashi/koseki/",
        "/kurashi/todokede/hikkoshi/",
        "/kurashi/jumin/hikkoshi/",
        "/kurashi/jumin/",
        "/kurashi/jumin/todokede/",
        "/kurashi/todokede/koseki/",
        "/tetsuzuki/todokede/",
        "/tetsuzuki/koseki/",
        "/tetsuzuki/jumin/",
        "/shinseisho/",
        "/yoshiki/",
        "/dl/kurashi/",
        "/dl/koseki/",
        "/download/kurashi/",
        "/download/koseki/",
        "/kurashi/shinseisho/",
        "/smph/kurashi/todokede/",
        "/life/todokede/",
        "/life/koseki/",
        "/soshiki/shimin/",
        # Underscore-joined paths (Kawaguchi-style)
        "/kurashi_tetsuzuki/",
        # Moving portal pages (Yokosuka-style)
        "/hikkoshi-portal/",
        # Numbered subcategory paths (Funabashi-style: 002=住民登録)
        "/kurashi/koseki/002/",
        "/kurashi/koseki/001/",
        # Application form menu pages (Sagamihara-style)
        "/shinseisho_menu/",
        "/shinseisho_menu/koseki/",
    ],
    "nhi": [
        "/kurashi/hoken/kokuho/",
        "/kurashi/kokuho/",
        "/tetsuzuki/hoken/",
        "/tetsuzuki/kokuho/",
        "/kenko/kokuho/",
        "/hoken/kokuho/",
        "/shinseisho/kokuho/",
        "/dl/hoken/",
        "/kurashi_tetsuzuki/kokuho/",
    ],
}

# Japanese keywords indicating irrelevant content — penalize candidates
NEGATIVE_KEYWORDS = [
    "消防", "防災", "ごみ", "環境", "動物", "水道", "道路",
    "桜", "花見", "イベント", "観光", "祭り", "スポーツ",
    "図書館", "公園", "選挙", "議会", "入札", "契約",
    "農業", "林業", "漁業", "商工", "都市計画",
    "委任状",       # Power of attorney
    "封筒",         # Envelope
    "戸籍",         # Family register (not residence registration)
    "改葬",         # Reburial permit
    "土地",         # Land/property
    "占用",         # Road occupancy permit
    "排水",         # Drainage permit
    "産業廃棄物",   # Industrial waste
    "臨時運行",     # Temporary vehicle permit
    "枝葉",         # Yard waste
]

# URL path segments indicating irrelevant sections — penalize candidates
NEGATIVE_PATH_SEGMENTS = [
    "shobo", "bousai", "gomi", "kankyo", "doubutsu", "suidou",
    "douro", "sakura", "kanko", "matsuri", "sports", "toshokan",
    "koen", "senkyo", "gikai", "nyusatsu", "keiyaku",
    "nogyo", "ringyo", "shoko", "toshikeikaku",
    "fire", "disaster", "garbage", "tourism", "library",
    "koseki", "inkantodoke", "nochi", "kaiso", "haiki", "rinjiunko",
]

# Google search terms per form type for site:-scoped queries
GOOGLE_SEARCH_TERMS = {
    "residence": "住民異動届 PDF",
    "nhi": "国民健康保険 届出書 PDF",
}


def generate_seed_urls(domain, form_type):
    """Generate candidate URLs by appending common municipal path patterns to the domain.

    Returns list of URLs to probe. Patterns are form-type-specific.
    """
    patterns = MUNICIPAL_SEED_PATTERNS.get(form_type, [])
    base = domain.rstrip("/")
    urls = []
    for pattern in patterns:
        urls.append(base + pattern)
    return urls


def google_site_search(domain, form_type, max_results=10):
    """Search Google with site: scoping to find form pages.

    Mimics what a human would do: search for the form name on the specific site.
    Returns list of URLs from search results. Falls back gracefully if Google blocks.
    """
    search_term = GOOGLE_SEARCH_TERMS.get(form_type, "届出書 PDF")
    parsed = urlparse(domain)
    site_host = parsed.netloc or parsed.path.strip("/")
    query = f"site:{site_host} {search_term}"

    try:
        from urllib.parse import quote_plus
        search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={max_results}&hl=ja"
        resp = requests.get(search_url, headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept-Language": "ja,en;q=0.9",
        }, timeout=TIMEOUT)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        urls = []
        # Google result links are in <a> tags; filter to ones pointing at the target site
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Google wraps results in /url?q=... redirects
            if href.startswith("/url?"):
                from urllib.parse import parse_qs
                params = parse_qs(urlparse(href).query)
                actual = params.get("q", [None])[0]
                if actual and site_host in actual:
                    urls.append(actual)
            elif site_host in href and href.startswith("http"):
                urls.append(href)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique[:max_results]
    except Exception as e:
        print(f"    Google search failed (non-fatal): {e}")
        return []


def parse_sitemap(domain):
    """Fetch and parse sitemap.xml, return list of URLs filtered to form-related paths."""
    urls = []
    sitemap_url = f"{domain.rstrip('/')}/sitemap.xml"
    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        # Handle both urlset and sitemapindex
        root = ET.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        # Check if this is a sitemap index
        sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)
        if sitemaps:
            # It's an index — fetch child sitemaps
            for sitemap_loc in sitemaps[:5]:  # Limit to avoid fetching too many
                try:
                    sub_resp = requests.get(sitemap_loc.text, headers=HEADERS, timeout=TIMEOUT)
                    sub_resp.raise_for_status()
                    sub_root = ET.fromstring(sub_resp.content)
                    for url_elem in sub_root.findall(".//sm:url/sm:loc", ns):
                        urls.append(url_elem.text)
                    time.sleep(0.5)
                except Exception:
                    continue
        else:
            # Direct urlset
            for url_elem in root.findall(".//sm:url/sm:loc", ns):
                urls.append(url_elem.text)
    except Exception:
        return []

    # Filter to form-related paths
    filtered = []
    for url in urls:
        path = urlparse(url if isinstance(url, str) else url.decode()).path.lower()
        if any(seg in path for seg in FORM_PATH_SEGMENTS):
            filtered.append(url)
    return filtered


def score_candidate(pdf_info, search_terms, form_type=None):
    """Score a PDF candidate 0-100 based on relevance signals.

    Positive signals: search term matches, form-related path segments, download context.
    Negative signals: irrelevant keywords (消防, 動物...) and path segments (shobo, gomi...).
    Cross-form penalties: when scoring for one form type, penalize terms from other types.
    """
    score = 0
    combined = pdf_info["link_text"] + " " + pdf_info["context"]
    url_path = urlparse(pdf_info["url"]).path.lower()

    # Search term matches in link text/context (strongest signal)
    for term in search_terms:
        if term in combined:
            score += 30

    # URL path keywords (positive)
    for seg in FORM_PATH_SEGMENTS:
        if seg in url_path:
            score += 5

    # Download-related context
    for kw in DOWNLOAD_CONTEXT_KEYWORDS:
        if kw in combined:
            score += 10

    # Negative: irrelevant Japanese keywords in text
    for kw in NEGATIVE_KEYWORDS:
        if kw in combined:
            score -= 20

    # Negative: irrelevant path segments in URL
    for seg in NEGATIVE_PATH_SEGMENTS:
        if seg in url_path:
            score -= 10

    # Cross-form-type penalties: penalize terms from the wrong form type
    if form_type == "residence":
        for term in ["国民健康保険", "国保", "健康保険料"]:
            if term in combined:
                score -= 20
    elif form_type == "nhi":
        for term in ["住民異動届", "転入届", "転出届", "転居届"]:
            if term in combined:
                score -= 20

    return max(0, min(score, 100))


def _has_strong_candidates(candidates, threshold=50):
    """Check if any candidates score at or above the threshold.

    Default threshold of 50 requires at least one search term match (+30)
    plus some supporting context, avoiding false positives from generic
    form keywords alone.
    """
    return any(c["score"] >= threshold for c in candidates)


def _has_search_term_match(pdf_info, search_terms):
    """Check if at least one search term appears in the PDF's link text or context."""
    combined = pdf_info["link_text"] + " " + pdf_info["context"]
    return any(term in combined for term in search_terms)


def _collect_pdfs_from_page(url, soup, search_terms, candidates, seen_pdfs, form_type=None):
    """Extract and score PDFs from a page, appending to candidates list."""
    pdfs = find_pdf_links(soup, url)
    added = 0
    for pdf in pdfs:
        if pdf["url"] not in seen_pdfs:
            pdf["score"] = score_candidate(pdf, search_terms, form_type=form_type)
            pdf["found_on"] = url
            if pdf["score"] > 0:
                candidates.append(pdf)
                seen_pdfs.add(pdf["url"])
                added += 1
    return added


def crawl_for_forms(domain, form_type, max_pages=50):
    """Crawl a municipality site looking for form PDFs.

    Uses a 4-phase cascade — each phase only runs if the previous one
    didn't find candidates scoring >= 30:

      Phase 1: Sitemap (parse sitemap.xml, filter by FORM_PATH_SEGMENTS)
      Phase 2: Seed URL Patterns (probe common municipal paths)
      Phase 3: Google Site Search (site:-scoped query)
      Phase 4: BFS crawl (depth 5, last resort)

    Returns list of candidate PDFs sorted by score (highest first).
    """
    ft = FORM_TYPES[form_type]
    search_terms = ft["search_terms"]
    subpage_kw = ft["subpage_keywords"]
    candidates = []
    seen_urls = set()
    seen_pdfs = set()

    # Clear page cache between municipality crawls
    _page_cache.clear()

    # ── Phase 1: Sitemap ──
    print(f"  Phase 1: Checking sitemap.xml...")
    sitemap_urls = parse_sitemap(domain)
    if sitemap_urls:
        print(f"    Found {len(sitemap_urls)} form-related URLs in sitemap")
        for url in sitemap_urls[:max_pages]:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            soup = fetch_page(url)
            if not soup:
                continue
            _collect_pdfs_from_page(url, soup, search_terms, candidates, seen_pdfs, form_type=form_type)
            time.sleep(1)
        if _has_strong_candidates(candidates):
            best = max(c["score"] for c in candidates)
            print(f"    Found strong candidates (best score: {best}), skipping later phases")
    else:
        print(f"    No sitemap found")

    # ── Phase 2: Seed URL Patterns ──
    # Probe common municipal paths, then do a focused mini-BFS (depth 4)
    # from each responding seed page. This is much more effective than
    # homepage BFS because it starts in the right site section.
    if not _has_strong_candidates(candidates):
        seed_urls = generate_seed_urls(domain, form_type)
        print(f"  Phase 2: Probing {len(seed_urls)} common URL patterns...")
        responding_seeds = []
        for url in seed_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            soup = fetch_page(url)
            if not soup:
                continue
            responding_seeds.append((url, soup))
            _collect_pdfs_from_page(url, soup, search_terms, candidates, seen_pdfs, form_type=form_type)
            time.sleep(0.5)

        if responding_seeds:
            print(f"    {len(responding_seeds)} seed pages responded, running focused crawl...")
            # Mini-BFS from each responding seed (depth 4, budget 30 pages per seed)
            for seed_url, seed_soup in responding_seeds:
                seed_queue = deque()
                # Seed the queue with subpage links from this seed page
                for sp in find_relevant_subpages(seed_soup, seed_url, domain, subpage_kw):
                    if sp["url"] not in seen_urls:
                        seed_queue.append((sp["url"], 1))
                seed_pages = 0
                while seed_queue and seed_pages < 30:
                    url, depth = seed_queue.popleft()
                    if url in seen_urls or depth > 4:
                        continue
                    seen_urls.add(url)
                    seed_pages += 1
                    soup = fetch_page(url)
                    if not soup:
                        continue
                    _collect_pdfs_from_page(url, soup, search_terms, candidates, seen_pdfs, form_type=form_type)
                    if depth < 4:
                        for sp in find_relevant_subpages(soup, url, domain, subpage_kw):
                            if sp["url"] not in seen_urls:
                                seed_queue.append((sp["url"], depth + 1))
                    time.sleep(0.5)
                print(f"    Seed {seed_url.replace(domain, '')}: crawled {seed_pages} pages")
                if _has_strong_candidates(candidates):
                    break
            print(f"    {len(candidates)} candidates after seed crawl")
        else:
            print(f"    0 seed pages responded")
        if _has_strong_candidates(candidates):
            best = max(c["score"] for c in candidates)
            print(f"    Found strong candidates (best score: {best}), skipping later phases")

    # ── Phase 3: Google Site Search ──
    if not _has_strong_candidates(candidates):
        print(f"  Phase 3: Google site search...")
        time.sleep(2)  # Politeness delay before Google query
        google_urls = google_site_search(domain, form_type)
        if google_urls:
            print(f"    Got {len(google_urls)} result(s) from Google")
            for url in google_urls:
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                soup = fetch_page(url)
                if not soup:
                    continue
                _collect_pdfs_from_page(url, soup, search_terms, candidates, seen_pdfs, form_type=form_type)
                # Also follow subpage links from Google result pages
                subpages = find_relevant_subpages(soup, url, domain, subpage_kw)
                for sp in subpages[:5]:
                    if sp["url"] in seen_urls:
                        continue
                    seen_urls.add(sp["url"])
                    sub_soup = fetch_page(sp["url"])
                    if sub_soup:
                        _collect_pdfs_from_page(sp["url"], sub_soup, search_terms, candidates, seen_pdfs, form_type=form_type)
                    time.sleep(0.5)
                time.sleep(1)
            if _has_strong_candidates(candidates):
                best = max(c["score"] for c in candidates)
                print(f"    Found strong candidates (best score: {best}), skipping BFS")
        else:
            print(f"    No Google results (may be blocked)")

    # ── Phase 4: Best-first crawl (last resort) ──
    # Uses a priority queue instead of plain BFS so the most relevant-looking
    # links are explored first. This prevents budget exhaustion on breadth
    # when forms are buried 4+ levels deep.
    if not _has_strong_candidates(candidates):
        print(f"  Phase 4: Best-first crawl from {domain} (depth 5)...")
        # Priority queue: (negative_priority, depth, url)
        # Lower priority value = explored first; negate so higher relevance = lower value
        pq = [(-100, 0, domain)]  # Homepage gets highest priority
        bfs_visited = 0

        while pq and bfs_visited < max_pages:
            neg_pri, depth, url = heapq.heappop(pq)
            if url in seen_urls or depth > 5:
                continue
            seen_urls.add(url)
            bfs_visited += 1

            soup = fetch_page(url)
            if not soup:
                continue

            _collect_pdfs_from_page(url, soup, search_terms, candidates, seen_pdfs, form_type=form_type)

            # Queue relevant subpages with priority scoring
            if depth < 5:
                subpages = find_relevant_subpages(soup, url, domain, subpage_kw)
                for sp in subpages:
                    if sp["url"] not in seen_urls:
                        # Score link relevance for priority ordering
                        link_pri = 0
                        sp_text = sp.get("text", "")
                        sp_path = urlparse(sp["url"]).path.lower()
                        for kw in subpage_kw:
                            if kw in sp_text:
                                link_pri += 10
                        for seg in FORM_PATH_SEGMENTS:
                            if seg in sp_path:
                                link_pri += 5
                        for kw in NEGATIVE_KEYWORDS:
                            if kw in sp_text:
                                link_pri -= 20
                        for seg in NEGATIVE_PATH_SEGMENTS:
                            if seg in sp_path:
                                link_pri -= 10
                        heapq.heappush(pq, (-link_pri, depth + 1, sp["url"]))

            time.sleep(1)

            if _has_strong_candidates(candidates):
                best = max(c["score"] for c in candidates)
                print(f"    Found strong candidate (score: {best}), stopping early")
                break

        print(f"    Crawled {bfs_visited} pages")

    # Sort by score descending
    candidates.sort(key=lambda c: c["score"], reverse=True)
    print(f"  Cascade complete: {len(candidates)} candidates found")
    return candidates


def discover_and_scrape(muni_key, muni_cfg, form_type, prefecture, max_pages=50):
    """Full discovery pipeline: crawl -> download -> validate.

    Returns dict with results per file:
      {"ok": [(path, score)], "flagged": [(path, reason)]}
    """
    ft = FORM_TYPES[form_type]
    name = f"{muni_cfg['name_en']} ({muni_cfg['name_ja']})"
    print(f"\n{'='*60}")
    print(f"  DISCOVER: {name} — {ft['label']}")
    print(f"{'='*60}")

    domain = muni_cfg["domain"]
    downloads_dir = get_downloads_dir(form_type, prefecture)
    muni_dir = downloads_dir / muni_key
    results = {"ok": [], "flagged": []}

    # Try to import validation from pipeline
    validate_fn = None
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from pipeline import validate_pdf_for_form, load_form_template
        form_template = load_form_template(ft["form_id"])
        if form_template:
            validate_fn = lambda pdf_path: validate_pdf_for_form(pdf_path, form_template)
    except ImportError:
        print("  WARN: pipeline.py not available, skipping validation")

    # Crawl for candidates
    candidates = crawl_for_forms(domain, form_type, max_pages)

    if not candidates:
        print(f"  No candidates found for {muni_key}")
        results["flagged"].append((None, "no candidates found"))
        return results

    print(f"\n  Top candidates ({len(candidates)} total):")
    for i, c in enumerate(candidates[:10]):
        filename = urlparse(c["url"]).path.split("/")[-1]
        print(f"    {i+1}. [{c['score']}] {filename}")
        print(f"       Text: {c['link_text'][:60]}")
        print(f"       URL:  {c['url']}")

    # Download top candidates (max 5, score >= 30, must have search term match)
    search_terms = ft["search_terms"]
    download_candidates = [
        c for c in candidates
        if c["score"] >= 30 and _has_search_term_match(c, search_terms)
    ][:5]
    if not download_candidates and candidates:
        print(f"  No candidates scored >= 30 with search term match (best: {candidates[0]['score']})")
        results["flagged"].append((None, f"best score too low ({candidates[0]['score']})"))
        return results

    for c in download_candidates:
        filename = urlparse(c["url"]).path.split("/")[-1]
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        dest = muni_dir / filename

        if dest.exists():
            print(f"\n  SKIP: {filename} (already downloaded)")
            # Still validate existing files
        else:
            print(f"\n  Downloading [{c['score']}]: {filename}")
            if not download_pdf(c["url"], dest):
                results["flagged"].append((filename, "download failed"))
                continue

        # Validate
        if validate_fn:
            try:
                passed, msg = validate_fn(str(dest))
                if passed:
                    print(f"    VALID: {msg}")
                    results["ok"].append((str(dest), c["score"]))
                else:
                    print(f"    FAIL validation: {msg}")
                    results["flagged"].append((str(dest), f"validation failed: {msg}"))
            except Exception as e:
                print(f"    WARN: validation error: {e}")
                results["flagged"].append((str(dest), f"validation error: {e}"))
        else:
            # No validation available — accept based on score
            if c["score"] >= 30:
                results["ok"].append((str(dest), c["score"]))
            else:
                results["flagged"].append((str(dest), f"low score ({c['score']})"))

    # Summary
    print(f"\n  DISCOVERY SUMMARY for {muni_key}:")
    print(f"    OK: {len(results['ok'])} downloaded and validated")
    for path, score in results["ok"]:
        print(f"      + {Path(path).name} (score={score})")
    if results["flagged"]:
        print(f"    FLAGGED: {len(results['flagged'])} need review")
        for path, reason in results["flagged"]:
            fname = Path(path).name if path else "(none)"
            print(f"      ! {fname}: {reason}")

    return results


# ═══════════════════════════════════════════════════════════════
# MANIFEST
# ═══════════════════════════════════════════════════════════════

def generate_manifest():
    """Generate a manifest of all downloaded PDFs."""
    downloads_dir = get_downloads_dir("residence", "tokyo")
    manifest = {"wards": {}}
    for ward_key in sorted(WARDS.keys()):
        ward_dir = downloads_dir / ward_key
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

KNOWN_PREFECTURES = ["tokyo", "kanagawa", "chiba", "saitama", "ibaraki", "tochigi", "gunma"]


def generate_status(prefecture=None, form_type=None):
    """Print coverage status to terminal.

    Args:
        prefecture: Single prefecture to report, or None for all known prefectures.
        form_type: "residence" or "nhi" (default: "residence").
    """
    if form_type is None:
        form_type = "residence"

    # Match both new-style (*_Residence_*.PDF) and old-style (*_walkthrough.pdf) naming
    if form_type == "residence":
        walkthrough_globs = ["*_Residence_*.PDF", "*_walkthrough.pdf"]
    else:
        walkthrough_globs = ["*_NHIapp_*.PDF"]
    ft_label = FORM_TYPES[form_type]["label"]

    prefectures = [prefecture] if prefecture else KNOWN_PREFECTURES

    print(f"\n{'='*60}")
    print(f"  STATUS: {ft_label}")
    print(f"{'='*60}")

    grand_total_inputs = 0
    grand_total_walks = 0
    grand_munis_with_inputs = 0
    grand_munis_with_walks = 0

    for pref in prefectures:
        registry = get_active_registry(pref)
        if not registry:
            continue

        downloads_dir = get_downloads_dir(form_type, pref)
        pref_inputs = 0
        pref_walks = 0
        munis_with_inputs = 0
        munis_with_walks = 0
        gaps = []

        for muni_key in sorted(registry.keys()):
            # Count input PDFs (Japanese-only)
            muni_input_dir = downloads_dir / muni_key
            if muni_input_dir.exists():
                input_pdfs = [
                    f for f in muni_input_dir.glob("*.pdf")
                    if is_japanese_only_pdf(f.name)
                ]
                num_inputs = len(input_pdfs)
            else:
                num_inputs = 0

            # Count walkthroughs (union of all glob patterns, deduplicated)
            muni_output_dir = OUTPUT_DIR / pref / muni_key
            if muni_output_dir.exists():
                walk_set = set()
                for g in walkthrough_globs:
                    walk_set.update(muni_output_dir.glob(g))
                num_walks = len(walk_set)
            else:
                num_walks = 0

            if num_inputs > 0:
                munis_with_inputs += 1
                pref_inputs += num_inputs
            if num_walks > 0:
                munis_with_walks += 1
                pref_walks += num_walks
            if num_inputs > 0 and num_walks == 0:
                gaps.append(muni_key)

        if munis_with_inputs == 0 and munis_with_walks == 0:
            continue

        coverage_pct = round(munis_with_walks / munis_with_inputs * 100) if munis_with_inputs else 0

        print(f"\n  {pref.upper()}")
        print(f"    Municipalities with inputs: {munis_with_inputs}")
        print(f"    Input PDFs: {pref_inputs}")
        print(f"    Walkthroughs: {pref_walks}")
        print(f"    Coverage: {munis_with_walks}/{munis_with_inputs} ({coverage_pct}%)")
        if gaps:
            print(f"    Gaps ({len(gaps)}): {', '.join(gaps)}")

        grand_total_inputs += pref_inputs
        grand_total_walks += pref_walks
        grand_munis_with_inputs += munis_with_inputs
        grand_munis_with_walks += munis_with_walks

    grand_pct = round(grand_munis_with_walks / grand_munis_with_inputs * 100) if grand_munis_with_inputs else 0
    print(f"\n{'='*60}")
    print(f"  TOTAL: {grand_total_walks} walkthroughs, "
          f"{grand_munis_with_walks}/{grand_munis_with_inputs} municipalities ({grand_pct}%)")
    print(f"{'='*60}")


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


def _find_existing_walkthrough(ward_key, pdf_stem, prefecture):
    """Check if a walkthrough already exists for this ward/PDF combination."""
    ward_output = OUTPUT_DIR / prefecture / ward_key
    if not ward_output.exists():
        return None
    # Walkthroughs follow pattern: {Ward}_{FormLabel}_{pdf_stem}_v{N}.PDF
    matches = list(ward_output.glob(f"*_{pdf_stem}_v*.PDF")) + \
              list(ward_output.glob(f"*_{pdf_stem}_v*.pdf"))
    if matches:
        return matches[-1]  # Latest version
    return None


def _save_generation_report(prefecture, form_type, report_data):
    """Persist generation results to data/reports/{prefecture}_{form_type}_generate.json."""
    from datetime import datetime

    reports_dir = BASE_DIR / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_data["timestamp"] = datetime.now().isoformat(timespec="seconds")
    out_path = reports_dir / f"{prefecture}_{form_type}_generate.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    print(f"\n  Generation report saved to {out_path.relative_to(BASE_DIR)}")
    return out_path


def run_generate(form_type="residence", prefecture="tokyo", registry=None,
                 dry_run=False):
    """Run the translation pipeline on all downloaded PDFs (Japanese only).

    When dry_run=True, previews what would be generated without calling process_pdf().
    Tracks results per-ward and persists a generation report.
    """
    if not dry_run:
        try:
            from pipeline import process_pdf
        except ImportError:
            print("ERROR: pipeline.py not found. Cannot run --generate.")
            print("  Place pipeline.py in the scripts/ directory.")
            return

    if registry is None:
        registry = get_active_registry(prefecture)

    ft = FORM_TYPES[form_type]
    downloads_dir = get_downloads_dir(form_type, prefecture)

    # Track results across all wards
    all_ward_results = {}
    totals = {"generated": 0, "failed": 0, "skipped": 0}

    if dry_run:
        print(f"\n  DRY RUN: {ft['label']} — {prefecture}")
        print(f"  (No walkthroughs will be generated)\n")

    for ward_key in sorted(registry.keys()):
        # Skip wards with official English forms for NHI
        if form_type == "nhi" and ward_key in NHI_ENGLISH_WARDS:
            print(f"\n  SKIP: {registry[ward_key]['name_en']} — has official English NHI forms")
            continue

        ward_dir = downloads_dir / ward_key
        if not ward_dir.exists():
            continue
        all_pdfs = sorted(ward_dir.glob("*.pdf"))
        # Filter to Japanese-only PDFs
        pdfs = [p for p in all_pdfs if is_japanese_only_pdf(p.name)]
        non_jp_count = len(all_pdfs) - len(pdfs)

        if not pdfs:
            continue

        name = registry[ward_key]["name_en"]
        ward_results = {"generated": [], "failed": [], "skipped": []}

        if dry_run:
            print(f"  {name}:")
            for pdf_path in pdfs:
                existing = _find_existing_walkthrough(ward_key, pdf_path.stem, prefecture)
                if existing:
                    print(f"    ALREADY EXISTS: {pdf_path.name} → {existing.name}")
                    ward_results["skipped"].append(pdf_path.name)
                    totals["skipped"] += 1
                else:
                    print(f"    WOULD GENERATE: {pdf_path.name}")
                    ward_results["generated"].append(pdf_path.name)
                    totals["generated"] += 1
            if non_jp_count:
                print(f"    (skipped {non_jp_count} non-Japanese PDF(s))")
        else:
            print(f"\n{'='*60}")
            print(f"  Generating guides for {name} — {ft['label']}")
            if non_jp_count:
                print(f"  (Skipping {non_jp_count} non-Japanese PDF(s))")
            print(f"{'='*60}")

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            for pdf_path in pdfs:
                print(f"  Processing: {pdf_path.name}")
                try:
                    result = process_pdf(str(pdf_path), str(OUTPUT_DIR), form_id=ft["form_id"])
                    if result:
                        output_name = Path(result).name
                        ward_results["generated"].append(output_name)
                        totals["generated"] += 1
                        print(f"    OK: {output_name}")
                    else:
                        ward_results["skipped"].append(pdf_path.name)
                        totals["skipped"] += 1
                except Exception as e:
                    print(f"    ERROR: {e}")
                    ward_results["failed"].append(pdf_path.name)
                    totals["failed"] += 1

            # Per-ward summary
            g, f, s = len(ward_results["generated"]), len(ward_results["failed"]), len(ward_results["skipped"])
            print(f"  → {name}: {g} generated, {f} failed, {s} skipped")

        all_ward_results[ward_key] = ward_results

    # Final summary
    mode_label = "DRY RUN" if dry_run else "GENERATION"
    print(f"\n{'='*60}")
    print(f"  {mode_label} SUMMARY — {ft['label']} ({prefecture})")
    print(f"{'='*60}")
    if dry_run:
        print(f"    Would generate: {totals['generated']}")
        print(f"    Already exist:  {totals['skipped']}")
    else:
        print(f"    Generated: {totals['generated']}")
        print(f"    Failed:    {totals['failed']}")
        print(f"    Skipped:   {totals['skipped']}")

        # Persist generation report (not for dry-run)
        report = {
            "prefecture": prefecture,
            "form_type": form_type,
            "summary": totals,
            "wards": all_ward_results,
        }
        _save_generation_report(prefecture, form_type, report)


# ═══════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════

def _save_validation_results(prefecture, form_type, results_by_ward):
    """Persist validation results to data/validation/{prefecture}_{form_type}.json."""
    from datetime import datetime

    validation_dir = BASE_DIR / "data" / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)

    summary = {"ok": 0, "failed": 0, "skipped": 0}
    for ward_data in results_by_ward.values():
        for f in ward_data.get("files", []):
            status = f["status"]
            if status in summary:
                summary[status] += 1

    output = {
        "prefecture": prefecture,
        "form_type": form_type,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "results": results_by_ward,
    }

    out_path = validation_dir / f"{prefecture}_{form_type}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n  Validation results saved to {out_path.relative_to(BASE_DIR)}")
    return out_path


def run_validate(form_type="residence", prefecture="tokyo", registry=None):
    """Validate all downloaded PDFs for a form type without generating walkthroughs.

    Iterates PDFs in the downloads directory, applies Japanese-only and
    NHI English ward filters, then runs validate_pdf_for_form() from pipeline.py.
    Prints per-file results and persists to data/validation/.
    """
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from pipeline import validate_pdf_for_form, load_form_template
    except ImportError:
        print("ERROR: pipeline.py not found. Cannot run --validate.")
        return

    if registry is None:
        registry = get_active_registry(prefecture)

    ft = FORM_TYPES[form_type]
    form_template = load_form_template(ft["form_id"])
    if not form_template:
        print(f"ERROR: No form template found for '{ft['form_id']}'")
        return

    downloads_dir = get_downloads_dir(form_type, prefecture)
    ft_label = ft["label"]
    print(f"\n  Validating: {ft_label} — {prefecture}")
    print(f"  Input dir: {downloads_dir.relative_to(BASE_DIR)}")

    results_by_ward = {}
    totals = {"ok": 0, "failed": 0, "skipped": 0}

    for ward_key in sorted(registry.keys()):
        # Skip wards with official English forms for NHI
        if form_type == "nhi" and ward_key in NHI_ENGLISH_WARDS:
            print(f"\n  SKIP: {registry[ward_key]['name_en']} — has official English NHI forms")
            continue

        ward_dir = downloads_dir / ward_key
        if not ward_dir.exists():
            continue

        all_pdfs = sorted(ward_dir.glob("*.pdf"))
        # Filter to Japanese-only PDFs
        pdfs = [p for p in all_pdfs if is_japanese_only_pdf(p.name)]
        non_jp = len(all_pdfs) - len(pdfs)

        if not pdfs:
            continue

        name_en = registry[ward_key]["name_en"]
        print(f"\n  {name_en}:")
        ward_files = []

        for pdf_path in pdfs:
            passed, detail = validate_pdf_for_form(str(pdf_path), form_template)

            # Determine status category
            if "image-based" in detail:
                status = "skipped"
                label = "SKIP"
            elif passed:
                status = "ok"
                label = "OK"
            else:
                status = "failed"
                label = "FAIL"

            totals[status] += 1
            ward_files.append({
                "filename": pdf_path.name,
                "status": status,
                "detail": detail,
            })
            print(f"    {label:>4}: {pdf_path.name} — {detail}")

        if non_jp:
            print(f"    (skipped {non_jp} non-Japanese PDF(s))")

        results_by_ward[ward_key] = {
            "name_en": name_en,
            "files": ward_files,
        }

    # Summary
    print(f"\n{'='*60}")
    print(f"  VALIDATION SUMMARY — {ft_label} ({prefecture})")
    print(f"{'='*60}")
    print(f"    OK:      {totals['ok']}")
    print(f"    FAILED:  {totals['failed']}")
    print(f"    SKIPPED: {totals['skipped']}")

    # Persist results
    _save_validation_results(prefecture, form_type, results_by_ward)


# ═══════════════════════════════════════════════════════════════
# QA MODE
# ═══════════════════════════════════════════════════════════════

def run_qa(form_type="residence", prefecture="tokyo", registry=None):
    """Run quality checks on generated walkthrough PDFs.

    Finds walkthroughs in output/{prefecture}/, runs quality_check.py checks,
    aggregates issues by severity, and prints a summary.
    """
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from quality_check import run_checks, load_dictionary
    except ImportError:
        print("ERROR: quality_check.py not found. Cannot run --qa.")
        return

    if registry is None:
        registry = get_active_registry(prefecture)

    ft = FORM_TYPES[form_type]
    # Determine naming pattern to filter by form type
    # pipeline.py uses these labels: residence → "Residence", nhi → "NHIapp"
    FORM_LABEL_MAP = {"residence": "_Residence_", "nhi": "_NHIapp_"}
    name_pattern = FORM_LABEL_MAP.get(form_type)

    dictionary = load_dictionary()
    pref_output = OUTPUT_DIR / prefecture
    if not pref_output.exists():
        print(f"\n  No walkthroughs found in {pref_output.relative_to(BASE_DIR)}")
        return

    print(f"\n  QA: {ft['label']} — {prefecture}")
    print(f"  Output dir: {pref_output.relative_to(BASE_DIR)}")

    total_files = 0
    total_errors = 0
    total_warnings = 0
    total_info = 0
    files_with_issues = 0

    for ward_key in sorted(registry.keys()):
        ward_dir = pref_output / ward_key
        if not ward_dir.exists():
            continue

        walkthroughs = sorted(ward_dir.glob("*.PDF")) + sorted(ward_dir.glob("*.pdf"))
        # Filter by form type naming pattern
        if name_pattern:
            walkthroughs = [w for w in walkthroughs if name_pattern in w.name]
        if not walkthroughs:
            continue

        name_en = registry[ward_key]["name_en"]
        print(f"\n  {name_en}:")

        for wt in walkthroughs:
            total_files += 1
            try:
                issues = run_checks(str(wt), dictionary)
            except Exception as e:
                print(f"    ERROR checking {wt.name}: {e}")
                total_errors += 1
                files_with_issues += 1
                continue

            errors = sum(1 for i in issues if i.severity == "ERROR")
            warnings = sum(1 for i in issues if i.severity == "WARNING")
            infos = sum(1 for i in issues if i.severity == "INFO")
            total_errors += errors
            total_warnings += warnings
            total_info += infos

            if errors or warnings:
                files_with_issues += 1
                print(f"    {wt.name}: {errors}E {warnings}W {infos}I")
                for issue in issues:
                    if issue.severity in ("ERROR", "WARNING"):
                        print(f"      [{issue.severity}] {issue.category}: {issue.message}")
            else:
                print(f"    {wt.name}: OK ({infos} info)")

    # Summary
    print(f"\n{'='*60}")
    print(f"  QA SUMMARY — {ft['label']} ({prefecture})")
    print(f"{'='*60}")
    print(f"    Files checked:    {total_files}")
    print(f"    Files with issues: {files_with_issues}")
    print(f"    Errors:   {total_errors}")
    print(f"    Warnings: {total_warnings}")
    print(f"    Info:     {total_info}")


# ═══════════════════════════════════════════════════════════════
# LIST
# ═══════════════════════════════════════════════════════════════

def list_wards(prefecture="tokyo", registry=None):
    """Print a summary of all configured municipalities for a prefecture."""
    if registry is None:
        registry = get_active_registry(prefecture)

    if not registry:
        print(f"\n  No municipalities configured for prefecture: {prefecture}")
        print(f"  Add JSON files to data/municipalities/{prefecture}/")
        return

    downloads_dir = get_downloads_dir("residence", prefecture)
    print(f"\n  Prefecture: {prefecture}")
    print(f"\n{'Ward':<22} {'Name':<24} {'Japanese':<10} {'Domain':<40} {'Notes'}")
    print("-" * 130)
    for key in sorted(registry.keys()):
        w = registry[key]
        # Check if any PDFs already downloaded
        ward_dir = downloads_dir / key
        pdf_count = len(list(ward_dir.glob("*.pdf"))) if ward_dir.exists() else 0
        dl = f"[{pdf_count} PDF]" if pdf_count else ""
        notes = w.get("notes", "")
        domain = w.get("domain", "")
        print(f"  {key:<20} {w['name_en']:<24} {w['name_ja']:<10} {domain:<40} {dl} {notes}")
    print(f"\n  Total: {len(registry)} municipalities configured")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Japanese government form PDFs from municipality websites"
    )
    parser.add_argument("--list", action="store_true", help="List all configured municipalities")
    parser.add_argument("--scrape", action="store_true", help="Download PDFs from municipality websites")
    parser.add_argument("--discover", action="store_true",
                        help="Crawl municipality sites to find form PDFs automatically")
    parser.add_argument("--validate", action="store_true",
                        help="Validate downloaded PDFs against form template (no generation)")
    parser.add_argument("--qa", action="store_true",
                        help="Run quality checks on generated walkthrough PDFs")
    parser.add_argument("--ward", type=str, help="Target municipality (Tokyo shorthand)")
    parser.add_argument("--municipality", type=str, help="Target municipality key")
    parser.add_argument("--prefecture", type=str, default=None,
                        help="Prefecture to operate on (default: tokyo for most modes, all for --status)")
    parser.add_argument("--form-type", type=str, default="residence",
                        choices=list(FORM_TYPES.keys()),
                        help="Form type to scrape/generate (default: residence)")
    parser.add_argument("--generate", action="store_true", help="Run translation pipeline on downloaded PDFs")
    parser.add_argument("--manifest", action="store_true", help="Generate manifest of downloaded PDFs")
    parser.add_argument("--status", action="store_true", help="Regenerate STATUS.md from current project state")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what --generate would do without running pipeline")
    parser.add_argument("--max-pages", type=int, default=50,
                        help="Max pages to crawl in discovery mode (default: 50)")
    parser.add_argument("--domain", type=str,
                        help="Override domain for discovery (useful for testing)")
    args = parser.parse_args()

    form_type = getattr(args, 'form_type', 'residence')
    prefecture = args.prefecture or "tokyo"

    # Resolve --ward as alias for --municipality
    muni_filter = args.municipality or args.ward

    if not any([args.list, args.scrape, args.generate, args.manifest,
                args.status, args.discover, args.validate, args.qa]):
        parser.print_help()
        return

    # Load registry for the active prefecture
    registry = get_active_registry(prefecture)

    if args.list:
        list_wards(prefecture, registry)
        return

    if args.discover:
        if not registry:
            print(f"No municipalities configured for {prefecture}")
            print(f"  Add JSON files to data/municipalities/{prefecture}/")
            return

        ft_label = FORM_TYPES[form_type]["label"]
        print(f"\nDiscovery mode: {ft_label} — {prefecture}")
        all_results = {}

        if muni_filter:
            muni_key = muni_filter.lower().replace("-ku", "").replace(" ", "")
            if muni_key not in registry:
                print(f"Unknown municipality: '{muni_filter}'")
                print(f"Available: {', '.join(sorted(registry.keys()))}")
                return
            cfg = dict(registry[muni_key])
            if args.domain:
                cfg["domain"] = args.domain
            all_results[muni_key] = discover_and_scrape(
                muni_key, cfg, form_type, prefecture, args.max_pages)
        else:
            for muni_key in sorted(registry.keys()):
                cfg = registry[muni_key]
                if not cfg.get("domain"):
                    continue
                all_results[muni_key] = discover_and_scrape(
                    muni_key, cfg, form_type, prefecture, args.max_pages)

        # Final summary
        total_ok = sum(len(r["ok"]) for r in all_results.values())
        total_flagged = sum(len(r["flagged"]) for r in all_results.values())
        print(f"\n{'='*60}")
        print(f"  DISCOVERY COMPLETE — {prefecture} {ft_label}")
        print(f"{'='*60}")
        print(f"  OK: {total_ok} PDFs downloaded and validated")
        print(f"  FLAGGED: {total_flagged} need review")

        # Persist discovery results in validation format
        validation_by_ward = {}
        for muni_key, result in all_results.items():
            name_en = registry.get(muni_key, {}).get("name_en", muni_key)
            files = []
            for path, score in result.get("ok", []):
                fname = Path(path).name if path else "(none)"
                files.append({"filename": fname, "status": "ok",
                              "detail": f"score={score}"})
            for path, reason in result.get("flagged", []):
                fname = Path(path).name if path else "(none)"
                files.append({"filename": fname, "status": "failed",
                              "detail": reason})
            if files:
                validation_by_ward[muni_key] = {"name_en": name_en, "files": files}
        if validation_by_ward:
            _save_validation_results(prefecture, form_type, validation_by_ward)

        return

    if args.scrape:
        ft_label = FORM_TYPES[form_type]["label"]
        downloads_dir = get_downloads_dir(form_type, prefecture)
        print(f"\nForm type: {ft_label}")
        print(f"Prefecture: {prefecture}")
        results = {}
        if muni_filter:
            ward_key = muni_filter.lower().replace("-ku", "").replace(" ", "")
            if ward_key not in registry:
                print(f"Unknown municipality: '{muni_filter}'")
                print(f"Available: {', '.join(sorted(registry.keys()))}")
                return
            results[ward_key] = scrape_ward(ward_key, registry[ward_key], form_type, downloads_dir)
        else:
            for ward_key in sorted(registry.keys()):
                results[ward_key] = scrape_ward(ward_key, registry[ward_key], form_type, downloads_dir)

        # Summary
        print(f"\n{'='*60}")
        print(f"  SCRAPE SUMMARY — {ft_label} ({prefecture})")
        print(f"{'='*60}")
        success = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"  {success}/{total} municipalities yielded PDFs\n")
        for ward_key in sorted(results.keys()):
            count = len(results[ward_key])
            status = f"{count} PDF(s)" if count else "NONE"
            print(f"  {ward_key:<22} {status}")

    if args.validate:
        run_validate(form_type, prefecture, registry)

    if args.qa:
        run_qa(form_type, prefecture, registry)

    if args.manifest:
        generate_manifest()

    if args.generate:
        run_generate(form_type, prefecture, registry, dry_run=args.dry_run)

    if args.status:
        generate_status(prefecture=args.prefecture, form_type=form_type)


if __name__ == "__main__":
    main()
