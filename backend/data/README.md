# Sprntly demo data

Source documents and processed corpora that the demo backend consumes.

## Layout

```
backend/data/
├── sprntly_prd_template.docx     ← David's PRD template (original)
├── sprntly_prd_template.md       ← markdown the LLM is fed
└── asurion/
    ├── raw/                      ← immutable originals from the user
    │   ├── asurion_analytics.xlsx
    │   ├── asurion_business_context.docx
    │   ├── asurion_qualitative_signals.docx
    │   └── asurion_expected_output.docx     (the answer key)
    ├── asurion_analytics.md
    ├── asurion_business_context.md
    ├── asurion_qualitative_signals.md
    └── _reference/
        └── asurion_expected_output.md       (NEVER fed to the LLM)
```

## What each file is

| File | Role |
|---|---|
| `raw/asurion_business_context.docx` | Asurion company profile — channels, claim economics |
| `raw/asurion_analytics.xlsx` | Quantitative cuts: funnels, churn by channel, upload failures |
| `raw/asurion_qualitative_signals.docx` | Verbatim Zendesk tickets, App Store reviews, Reddit, Gong |
| `raw/asurion_expected_output.docx` | **Answer key** — what an LLM *should* produce when fed the corpus. Used to grade output quality. |
| `*.md` (sibling of `raw/`) | Markdown derived from the originals; this is what `app/corpus.py` loads and passes to Claude |
| `_reference/asurion_expected_output.md` | Markdown of the answer key, deliberately isolated so the corpus loader skips it |

## How the LLM consumes it

`app/corpus.py:load_corpus("asurion")` walks `backend/data/asurion/*.md` (NOT recursive into `_reference/` or `raw/`), concatenates them, and feeds them as the corpus alongside the prompt. So:

- `*.md` at the dataset root → corpus
- `raw/` → ignored (binary originals, source-of-truth only)
- `_reference/` → ignored (answer key, anti-cheat)

## Adding a new dataset

**Preferred (live API):** use the onboarding wizard at `/demo/onboard` or POST to `/v1/datasets` + `/v1/datasets/{slug}/files`. The backend converts uploads to markdown in-process and seeds the `datasets` table automatically.

**Offline (developer):**

1. Drop original `.docx` / `.xlsx` / `.pdf` / `.txt` files into `backend/data/<dataset>/raw/`
2. Run the converter:
   ```bash
   pip install python-docx openpyxl pypdf
   python scripts/convert_dataset.py --in backend/data/<dataset>/raw --out backend/data/<dataset>
   ```
3. Files matching `expected_output*` automatically land in `_reference/` and are kept out of the LLM context.
4. Restart the backend — `seed_filesystem_datasets()` registers the new folder in the `datasets` table on boot.

## Re-converting an existing dataset

If you change a `.docx` or `.xlsx` in `raw/`, re-run the converter to refresh the `.md` siblings, then commit both. The `.md` is the version-controlled format; the `.docx` is the source-of-truth that won't drift silently.
