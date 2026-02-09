# LLM Translation TODO — Tokyo 23 Wards

> Generated 2026-02-09. Feed this file back into Claude Code to resume work.

## Current State

- **23/23 Tokyo wards** have walkthrough PDFs in `output/walkthroughs/tokyo/`
- **43 total walkthrough PDFs** (some wards have multiple forms: blank + example + English)
- **Translation cache**: 1000 entries in `translations_cache.json`
  - 524 LLM-translated (Claude API)
  - 392 fragment-matched (dictionary partial match)
  - 24 dictionary exact-match
  - 60 vision explanation groups (image-based field explanations, covering 4 zones)
- **All walkthroughs were generated with `use_llm=True`** via the batch scraper run, so the cache includes Claude translations for text the pipeline extracted as fields
- **However**: many Japanese text lines on each form were not extracted as zone fields, so they remain uncached. These are structural/instructional lines outside the 4 default zones (Header & Dates, Who Is Filing, Addresses, Person Table).

## Problem

The pipeline uses `DEFAULT_ZONES` to extract fields from known rectangular regions of the form. Text outside these zones (instructions, footnotes, checkboxes, identity document lists, mailing instructions) is **not extracted, not translated, and not explained** in the walkthrough. Toshima and most other wards show the same pattern.

## Per-Ward Uncached Text Count

Lines of Japanese text that exist in the source PDFs but are NOT in the translation cache:

| Ward | Uncached Lines | PDFs | Priority |
|------|---------------|------|----------|
| shinagawa | 208 | 1 | HIGH — mostly childcare/保育園 content, not a residency form |
| itabashi | 203 | 4 | MEDIUM — 4 forms, largest ward set |
| nakano | 135 | 2 | MEDIUM |
| koto | 117 | 4 | MEDIUM |
| toshima | 112 | 1 | HIGH — user specifically mentioned |
| taito | 104 | 1 | MEDIUM |
| shinjuku | 105 | 3 | LOW — already has 26 LLM entries |
| shibuya | 86 | 1 | LOW — My Number card application guide, not a residency form |
| katsushika | 82 | 1 | MEDIUM |
| minato | 81 | 2 | LOW — already bilingual (English on form) |
| nerima | 76 | 3 | MEDIUM |
| ota | 72 | 2 | MEDIUM |
| bunkyo | 69 | 2 | MEDIUM |
| chiyoda | 65 | 1 | MEDIUM |
| suginami | 63 | 2 | MEDIUM |
| edogawa | 50 | 2 | LOW — our original test ward, has most vision entries |
| adachi | 52 | 1 | MEDIUM |
| meguro | 46 | 2 | MEDIUM |
| chuo | 45 | 1 | LOW — already partially bilingual |
| kita | 37 | 1 | MEDIUM |
| sumida | 33 | 3 | LOW |
| arakawa | 31 | 2 | LOW |
| setagaya | 1 | 1 | DONE |

## Recommended Actions

### 1. Re-run Toshima with fresh LLM pass (user priority)

```bash
cd C:\Users\Owner\GithubProjects\japan-forms
python scripts/pipeline.py downloads/tokyo/toshima/hpyoutodokedesho.pdf
```

This will re-extract fields, hit the cache for known translations, and call Claude for any new fields. The walkthrough at `output/walkthroughs/tokyo/toshima/` will be regenerated.

### 2. Expand zone coverage in pipeline.py

The 4 default zones (`DEFAULT_ZONES` around line 90 in `pipeline.py`) only cover a portion of each form. Many wards' forms have additional sections for:
- Identity document checklists (本人確認書類)
- Mailing instructions (郵送の場合)
- My Number / マイナンバー sections
- Checkbox/category sections (異動区分)

**Task**: Add more zones to `DEFAULT_ZONES` or implement full-page text extraction as fallback. This would dramatically increase coverage across all wards without per-ward customization.

### 3. Batch re-run all wards after zone expansion

```bash
cd C:\Users\Owner\GithubProjects\japan-forms\scripts
python scraper.py --generate
```

The cache will prevent redundant API calls — only newly-extracted fields will hit Claude.

### 4. Review non-residency forms

Some wards' PDFs aren't actually residency move-out forms:
- **shibuya**: `mynumber_sinsei` — My Number card application guide (86 uncached lines, not a 転出届)
- **shinagawa**: `20250206184722_1` — appears to be a childcare/保育園 enrollment checklist (208 uncached lines)

These may need different zone definitions or should be excluded from the residency walkthrough set.

### 5. Dictionary expansion candidates

Common uncached terms appearing across multiple wards that should be added to `data/fields/dictionary.json`:

- `届出人` — Person filing the notification (appears in ~15 wards)
- `委任状` — Power of attorney / letter of delegation
- `本人確認書類` — Identity verification documents
- `異動区分` — Type of address change
- `世帯主` — Head of household
- `方書` — Building/apartment name line (already in dict but not matching compound forms)
- `郵送届出用` — For postal submission
- `転出証明書` — Certificate of moving out
- `マイナンバーカード` — My Number card
- `在留カード` — Residence card

Adding these to the dictionary would improve fragment matching and reduce LLM API calls.

## File References

| File | Purpose |
|------|---------|
| `scripts/pipeline.py` | Main pipeline — `DEFAULT_ZONES` (~line 90), `translate_field()` (~line 373), `resolve_explanations()` (~line 843), `process_pdf()` (~line 2705) |
| `scripts/scraper.py` | Batch runner — `run_generate()` (~line 493), `OUTPUT_DIR` (line 36) |
| `data/fields/dictionary.json` | Universal field dictionary (kanji/reading/romaji/english/tip) |
| `translations_cache.json` | MD5-keyed translation cache (1000 entries) |
| `data/forms/residence_registration.json` | Template for residency forms |
| `downloads/tokyo/<ward>/*.pdf` | Source PDFs (43 files across 23 wards) |
| `output/walkthroughs/tokyo/<ward>/` | Generated walkthrough PDFs |

## Next Major Milestone

Expand beyond Tokyo to other Kanto cities: Yokohama, Kawasaki, Sagamihara, Saitama, Kawaguchi, Chiba, Funabashi, etc.
