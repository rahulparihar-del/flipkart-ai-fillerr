# 🦕 Flipkart AI Filler

Upload your Flipkart bulk listing template (.xls) → AI fills every empty field →
download the completed sheet and upload it to Flipkart Seller Hub.

- Keeps the **exact original .xls structure** (all sheets, validations) — cells are
  edited in place with LibreOffice, never rebuilt.
- AI (free NVIDIA Nemotron via OpenRouter) reads what is already in each row
  (SKU, keywords, features, colors) and generates descriptions, fabric, colors,
  and all dropdown values from the sheet's own allowed-values lists.
- Fixed fields (ACTIVE, SELLER, GST_APPAREL, HSN, dimensions…) are filled instantly.
- Optionally fills the **Parent Variant Products** sheet with the main size per product.

## Deploy on Railway (easiest, ~5 min)

1. Create a free account at https://railway.app (sign in with GitHub)
2. Push this folder to a GitHub repository
3. In Railway: **New Project → Deploy from GitHub repo** → select the repo
   (Railway auto-detects the Dockerfile)
4. In the service → **Variables**, add:
   - `OPENROUTER_API_KEY` = your OpenRouter key (`sk-or-v1-…`)
5. **Settings → Networking → Generate Domain** — that URL is your website

Render.com works the same way (New → Web Service → Docker).

## Run locally (Mac)

```bash
# 1. Install LibreOffice once: https://www.libreoffice.org/download/  (or: brew install --cask libreoffice)
# 2. In this folder:
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-v1-...
# make sure 'soffice' is on PATH (add if needed):
export PATH="$PATH:/Applications/LibreOffice.app/Contents/MacOS"
uvicorn app:app --port 8000
```

Open http://localhost:8000

## How it works

| Step | What happens |
|---|---|
| Upload | LibreOffice converts a copy to .xlsx; pandas finds the category sheet, products, empty columns, and the allowed dropdown values from the Index sheet |
| Fill | Fixed values applied instantly; one AI call per product generates the content fields, choosing dropdown values only from the sheet's own allowed lists |
| Apply | A LibreOffice Basic macro writes each cell into the **original** .xls in place |
| Download | Same file, same sheets, values filled |

## Environment variables

| Variable | Meaning | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | Your OpenRouter key | (required) |
| `OPENROUTER_MODEL` | Model to use | `nvidia/nemotron-3-super-120b-a12b:free` |
| `JOBS_DIR` | Where uploads are stored | `/tmp/fk_jobs` |
