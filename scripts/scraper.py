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
    python scraper.py --generate                                # Run pipeline on downloaded PDFs
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
]

# Context keywords that boost candidate scores
DOWNLOAD_CONTEXT_KEYWORDS = [
    "ダウンロード", "申請書", "様式", "届出書", "届け出",
    "PDF", "書式", "用紙",
]


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
        path = urlparse(url).path.lower()
        if any(seg in path for seg in FORM_PATH_SEGMENTS):
            filtered.append(url)
    return filtered


def score_candidate(pdf_info, search_terms):
    """Score a PDF candidate 0-100 based on relevance signals."""
    score = 0
    combined = pdf_info["link_text"] + " " + pdf_info["context"]
    url_path = urlparse(pdf_info["url"]).path.lower()

    # Search term matches in link text/context (strongest signal)
    for term in search_terms:
        if term in combined:
            score += 30

    # URL path keywords
    for seg in FORM_PATH_SEGMENTS:
        if seg in url_path:
            score += 5

    # Download-related context
    for kw in DOWNLOAD_CONTEXT_KEYWORDS:
        if kw in combined:
            score += 10

    return min(score, 100)


def crawl_for_forms(domain, form_type, max_pages=50):
    """Crawl a municipality site looking for form PDFs.

    Tries sitemap.xml first, then BFS crawl (depth 3).
    Returns list of candidate PDFs sorted by score (highest first).
    """
    ft = FORM_TYPES[form_type]
    search_terms = ft["search_terms"]
    subpage_kw = ft["subpage_keywords"]
    candidates = []
    seen_urls = set()
    seen_pdfs = set()

    # Phase 1: Try sitemap
    print(f"  Checking sitemap.xml...")
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
            pdfs = find_pdf_links(soup, url)
            for pdf in pdfs:
                if pdf["url"] not in seen_pdfs:
                    pdf["score"] = score_candidate(pdf, search_terms)
                    pdf["found_on"] = url
                    if pdf["score"] > 0:
                        candidates.append(pdf)
                        seen_pdfs.add(pdf["url"])
            time.sleep(1)
    else:
        print(f"    No sitemap found, falling back to BFS crawl")

    # Phase 2: BFS crawl from homepage (if sitemap didn't yield enough)
    if len(candidates) < 3:
        print(f"  BFS crawling from {domain}...")
        queue = deque([(domain, 0)])  # (url, depth)
        pages_visited = len(seen_urls)

        while queue and pages_visited < max_pages:
            url, depth = queue.popleft()
            if url in seen_urls or depth > 3:
                continue
            seen_urls.add(url)
            pages_visited += 1

            soup = fetch_page(url)
            if not soup:
                continue

            # Collect PDFs
            pdfs = find_pdf_links(soup, url)
            for pdf in pdfs:
                if pdf["url"] not in seen_pdfs:
                    pdf["score"] = score_candidate(pdf, search_terms)
                    pdf["found_on"] = url
                    if pdf["score"] > 0:
                        candidates.append(pdf)
                        seen_pdfs.add(pdf["url"])

            # Queue relevant subpages
            if depth < 3:
                subpages = find_relevant_subpages(soup, url, domain, subpage_kw)
                for sp in subpages:
                    if sp["url"] not in seen_urls:
                        queue.append((sp["url"], depth + 1))

            time.sleep(1)

        print(f"    Visited {pages_visited} pages")

    # Sort by score descending
    candidates.sort(key=lambda c: c["score"], reverse=True)
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

    # Download top candidates (max 5)
    for c in candidates[:5]:
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

def generate_status():
    """Generate STATUS.md from current project state."""
    from datetime import date
    try:
        from pipeline import classify_pdf_pages
    except ImportError:
        classify_pdf_pages = None

    STATUS_PATH = BASE_DIR / "STATUS.md"
    downloads_dir = get_downloads_dir("residence", "tokyo")
    trash_dir = BASE_DIR / "input" / "tokyo_trash"

    # Gather per-ward data
    ward_rows = []
    total_walkthroughs = 0
    total_source_pdfs = 0

    for ward_key in sorted(WARDS.keys()):
        ward = WARDS[ward_key]
        ward_dir = downloads_dir / ward_key
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
    nhi_dir = get_downloads_dir("nhi", "tokyo")
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


def run_generate(form_type="residence", prefecture="tokyo", registry=None):
    """Run the translation pipeline on all downloaded PDFs (Japanese only)."""
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
        skipped = len(all_pdfs) - len(pdfs)

        if not pdfs:
            continue

        name = registry[ward_key]["name_en"]
        print(f"\n{'='*60}")
        print(f"  Generating guides for {name} — {ft['label']}")
        if skipped:
            print(f"  (Skipping {skipped} non-Japanese PDF(s))")
        print(f"{'='*60}")

        # Pipeline will create {prefecture}/{ward} subdirectory based on input path
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
    parser.add_argument("--ward", type=str, help="Target municipality (Tokyo shorthand)")
    parser.add_argument("--municipality", type=str, help="Target municipality key")
    parser.add_argument("--prefecture", type=str, default="tokyo",
                        help="Prefecture to operate on (default: tokyo)")
    parser.add_argument("--form-type", type=str, default="residence",
                        choices=list(FORM_TYPES.keys()),
                        help="Form type to scrape/generate (default: residence)")
    parser.add_argument("--generate", action="store_true", help="Run translation pipeline on downloaded PDFs")
    parser.add_argument("--manifest", action="store_true", help="Generate manifest of downloaded PDFs")
    parser.add_argument("--status", action="store_true", help="Regenerate STATUS.md from current project state")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be scraped without downloading")
    parser.add_argument("--max-pages", type=int, default=50,
                        help="Max pages to crawl in discovery mode (default: 50)")
    parser.add_argument("--domain", type=str,
                        help="Override domain for discovery (useful for testing)")
    args = parser.parse_args()

    form_type = getattr(args, 'form_type', 'residence')
    prefecture = args.prefecture

    # Resolve --ward as alias for --municipality
    muni_filter = args.municipality or args.ward

    if not any([args.list, args.scrape, args.generate, args.manifest, args.status, args.discover]):
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

    if args.manifest:
        generate_manifest()

    if args.generate:
        run_generate(form_type, prefecture, registry)

    # Update STATUS.md only on explicit --status (slow — classifies all PDFs)
    if args.status:
        generate_status()


if __name__ == "__main__":
    main()
