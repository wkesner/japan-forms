# Contributing to japan-forms

Thank you for helping make Japanese government paperwork accessible to everyone! Here's how to contribute.

## Ways to Contribute

### 1. Fix a Translation (Easiest)

If you spot an error or have a better translation:

1. Find the field in `data/fields/dictionary.json` or the form in `data/forms/`
2. Edit the relevant `english`, `tip_en`, or other English text
3. Update the `confidence` level if appropriate
4. Submit a PR

**Confidence levels:**
- `draft` — Machine-translated or best guess
- `reviewed` — Reviewed by someone with Japanese ability
- `verified` — Confirmed against official English materials or by native speaker
- `official` — Taken directly from an official government English translation

### 2. Add a Municipality

1. Copy `data/municipalities/_template.json`
2. Save as `data/municipalities/{prefecture}/{municipality-name}.json`
   - Use romaji with hyphens: `tokyo/shibuya-ku.json`, `osaka/osaka-shi.json`
3. Fill in what you know — partial data is fine!
4. Submit a PR

**Even just confirming "this ward has/doesn't have English forms" is valuable.**

### 3. Add a Form Type

1. Create a new file in `data/forms/{form_id}.json`
2. Follow the structure of `residence_registration.json`
3. Reference existing fields from `data/fields/dictionary.json` where possible
4. Add any new fields to the dictionary
5. Submit a PR

### 4. Report an Issue

Use our [issue templates](https://github.com/deep-japan/japan-forms/issues/new/choose):
- **Translation error** — wrong or misleading translation
- **Add municipality** — share info about your ward/city
- **Missing form** — request a new form type

## File Structure Rules

### Field IDs
- Use lowercase romaji with underscores: `shimei`, `seinengappi`, `zairyu_card_bango`
- Must be unique across the entire dictionary
- If a field appears on multiple forms, define it ONCE in the dictionary

### Municipality IDs
- Format: `{prefecture}/{municipality}.json`
- Use romaji with hyphens: `tokyo/minato-ku`, `osaka/osaka-shi`, `hokkaido/sapporo-shi`
- Tokyo wards use `-ku` suffix, cities use `-shi`, towns use `-cho` or `-machi`

### Form IDs
- Use lowercase with underscores: `residence_registration`, `seal_registration`
- Must match the filename (without `.json`)

## Running the Generator

After making changes, regenerate outputs to verify:

```bash
# Install dependencies
pip install reportlab

# Generate all outputs
python scripts/generate.py

# Generate for a specific municipality
python scripts/generate.py --municipality tokyo/minato-ku

# Markdown only (faster, no font dependencies)
python scripts/generate.py --format markdown
```

## Pull Request Checklist

- [ ] Data files are valid JSON (run `python -m json.tool data/path/to/file.json`)
- [ ] Field IDs are consistent (no typos in references)
- [ ] English text is clear and concise
- [ ] Confidence level is set appropriately
- [ ] Generator runs without errors: `python scripts/generate.py`

## Code of Conduct

- Be respectful in issues and PRs
- Don't submit AI-generated translations without verification
- When in doubt, mark confidence as `draft` and note your uncertainty
- Credit sources when translating from official materials

## Questions?

Open an issue with the `question` label or reach out to the maintainers.
