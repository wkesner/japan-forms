# 🇯🇵 Japan Forms — Open Bilingual Translation Project

**Community-driven English translations of Japanese municipal paperwork.**

Every year, hundreds of thousands of foreign residents navigate Japan's municipal offices with forms they can't read. This project provides structured, bilingual translations of every common government form — starting with Tokyo's 23 special wards and scaling to all 1,741 municipalities in Japan.

## How It Works

```
data/fields/        ← Universal field dictionary (kanji → English)
data/forms/         ← Form templates (one per form type)
data/municipalities/ ← Ward/city-specific variants & metadata
scripts/            ← Generators: data → PDF + Markdown guides
output/             ← Generated guides (don't edit directly)
```

The **data** is the source of truth. The **output** is generated from it. If you want to fix a translation, edit the data — not the output files.

### The Three-Layer Model

1. **Field Dictionary** — Every field that appears on Japanese government forms, translated once and reused everywhere. `氏名 → Full Name` doesn't change between Minato and Shibuya.

2. **Form Templates** — Each form type (residence registration, seal registration, etc.) defines which fields appear, in what order, with what context. These are national-level — the law mandates what a 転入届 must contain.

3. **Municipality Variants** — What makes Minato's form different from Sapporo's: optional fields, office locations, English availability, downloadable PDF links, local tips.

## Quick Start

```bash
# Generate all outputs from data
python scripts/generate.py

# Generate for a specific municipality
python scripts/generate.py --municipality tokyo/minato-ku

# Generate only PDF or only Markdown
python scripts/generate.py --format pdf
python scripts/generate.py --format markdown
```

## Contributing

We welcome contributions! Here's how you can help:

### 🔤 Fix a translation
Edit the relevant file in `data/` and submit a PR. Every field has a `confidence` rating — if you're a native speaker or have used the form yourself, bump it to `verified`.

### 🏙️ Add a municipality
Copy `data/municipalities/_template.json`, fill in the details for your city/ward, and submit a PR. Even partial information is useful.

### 🐛 Flag an issue
Use the [Issue Templates](.github/ISSUE_TEMPLATE/) to report:
- **Translation error** — wrong or misleading translation
- **Missing form** — a form type we don't cover yet
- **Municipality data** — corrections to office hours, locations, etc.

### 📝 Add a form type
Create a new form template in `data/forms/` following the schema in `data/forms/_schema.json`.

## Coverage

### Tokyo Special Wards (23区)

| Ward | Status | Forms Covered |
|------|--------|---------------|
| 港区 Minato | 🟢 Started | Residence Registration |
| 渋谷区 Shibuya | ⬜ Not started | — |
| 新宿区 Shinjuku | ⬜ Not started | — |
| ... | ... | ... |

### Form Types

| Form | Japanese | Status |
|------|----------|--------|
| Residence Registration (Moving In) | 転入届 | 🟢 Complete |
| Residence Registration (Moving Out) | 転出届 | ⬜ Not started |
| Certificate of Residence Request | 住民票交付申請書 | ⬜ Not started |
| Seal Registration | 印鑑登録申請書 | ⬜ Not started |
| Bank Account Opening (JP Post) | 口座開設申込書 | ⬜ Not started |
| Pension Lump-Sum Withdrawal | 脱退一時金裁定請求書 | ⬜ Not started |
| National Health Insurance Enrollment | 国民健康保険加入届 | ⬜ Not started |
| My Number Card Application | マイナンバーカード交付申請書 | ⬜ Not started |

## License

Content is licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Code is MIT.

This is a community project and not affiliated with any Japanese government entity. Translations are provided as-is for reference — always confirm with your local municipal office.

## Acknowledgments

Built by [Deep Japan](https://deepjapan.io). Translations cross-referenced with official ward office materials, MailMate.jp, JobsInJapan.com, and community contributions.
