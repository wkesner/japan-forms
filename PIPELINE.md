# Japan Forms Translation Pipeline

## Quick Start

```bash
cd japan-forms
source venv/bin/activate  # or: python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-key-here"
```

## Pipeline Scripts (Run in Order)

### 1. Download Source PDFs
```bash
python scripts/scraper.py --scrape --manifest
```
Downloads Japanese government form PDFs from all 23 Tokyo ward websites.
- Output: `input/tokyo/{ward}/*.pdf`
- Manifest: `input/manifest.json`

**Note:** Only process Japanese-language PDFs. Skip Chinese (中国語), Korean (韓国語),
and English versions to avoid redundant translations.

### 2. Generate Walkthroughs
```bash
python scripts/scraper.py --generate
# Or run pipeline directly on specific PDFs:
python scripts/pipeline.py input/tokyo/bunkyo/*.pdf
```
Generates bilingual walkthrough PDFs with translations from dictionary + LLM fallback.
- Output: `output/walkthroughs/tokyo/{ward}/*_walkthrough.pdf`

### 3. Find & Translate Gaps
```bash
python scripts/find_gaps.py                    # Scan for untranslated items
python scripts/find_gaps.py --translate        # Auto-translate using Claude API
python scripts/find_gaps.py --translate --limit 100  # Limit API calls
```
Scans walkthrough PDFs for `[Japanese text]` patterns, translates via Claude API,
and caches results in `data/fields/dictionary.json`.

### 4. Quality Check
```bash
python scripts/quality_check.py              # Check all walkthroughs
python scripts/quality_check.py --verbose    # Include INFO level issues
python scripts/quality_check.py --pdf path   # Check specific file
```
Validates translation quality, formatting, and dictionary consistency.

### 5. Regenerate with New Translations
After adding dictionary entries, regenerate walkthroughs:
```bash
python scripts/scraper.py --generate
```

## Full Pipeline (All Steps)
```bash
# 1. Download fresh PDFs
python scripts/scraper.py --scrape --manifest

# 2. Generate walkthroughs (first pass)
python scripts/scraper.py --generate

# 3. Find and fix translation gaps
python scripts/find_gaps.py --translate --limit 500

# 4. Regenerate with complete translations
python scripts/scraper.py --generate

# 5. Quality check
python scripts/quality_check.py

# 6. Commit and push
git add .
git commit -m "Update walkthroughs with new translations"
git push
```

## Translation Hierarchy

The pipeline uses a 3-tier translation system:

1. **Dictionary** (fastest, free) - Exact match in `data/fields/dictionary.json`
2. **Fragment** (fast, free) - Partial match combining known dictionary terms
3. **LLM** (slow, costs API) - Claude Sonnet fallback for unknown text

Goal: Build dictionary comprehensive enough that LLM is rarely needed.

## Directory Structure

```
japan-forms/
├── data/
│   ├── fields/
│   │   └── dictionary.json    # Universal field translations (583+ entries)
│   ├── forms/                 # Form templates (JSON)
│   └── municipalities/        # Ward metadata
├── input/
│   ├── tokyo/{ward}/*.pdf     # Source PDFs (not committed)
│   └── manifest.json          # Download metadata
├── output/
│   └── walkthroughs/tokyo/    # Generated bilingual guides
├── scripts/
│   ├── scraper.py             # Download + batch processing
│   ├── pipeline.py            # Core translation + PDF generation
│   ├── find_gaps.py           # Gap detection + auto-translation
│   └── quality_check.py       # Validation
└── venv/                      # Python virtual environment
```

## Requirements

```bash
pip install pdfplumber anthropic requests beautifulsoup4 pdf2image reportlab Pillow
```

For image rendering in walkthroughs (optional):
- **macOS:** `brew install poppler`
- **Linux:** `apt install poppler-utils`
- **Windows:** Download from https://github.com/oschwartz10612/poppler-windows/releases

## Tips

- **API costs:** Each LLM translation costs ~$0.003. Run `find_gaps.py` without
  `--translate` first to see how many gaps exist.

- **Dictionary growth:** After translating gaps, the dictionary grows. Subsequent
  runs will be faster and cheaper.

- **Skip non-Japanese:** Filter out Chinese/Korean/English versions when scraping
  to avoid redundant work.

- **Quality issues:** If quality_check.py shows errors, review the specific
  translations in dictionary.json and fix manually if needed.
