https://andrey-eshet.github.io/TestAllSaberLDSHotels/

# SabreLDS Hotel Static Data Checker

Automated verification of hotel static data arrival from SabreLDS via hgwcore AbroadStaticData/Hotel endpoint.

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

## Input data

Place the following files in `data/`:

| File | Required columns |
|------|-----------------|
| `simaFullHotelList.xlsx` | `HOTELID` |
| `missing_sabre_codes_after_Meital_filtering.xlsx` | `MissingSabreCode`, `Destination_EN` |

## Configuration

Copy `.env.example` to `.env` and adjust if needed:

```bash
cp .env.example .env
```

Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `RETRY_COUNT` | 2 | Number of fetch attempts per hotel |
| `TIMEOUT_SECONDS` | 30 | HTTP request timeout |
| `USE_PLAYWRIGHT_FOR_TOKEN` | false | Use Playwright for token page chain (if JS required) |

## Run

```bash
python scripts/run_local.py
```

## Output

Reports are generated in `reports/`:

- `report.md` — human-readable Markdown table
- `report.json` — structured JSON (full data including token URLs)
- `report.csv` — CSV for Excel import

Debug HTML saved in `artifacts/html/` and `artifacts/tokens/`.

## Report structure

**Section 1** — Hotels with complete static data (Name, Stars with *, at least 1 image with Url)

**Section 2** — Hotels missing data or not found, with:
- Missing field details
- Destination code (from token chain) for NOT_FOUND hotels
- Destination name from missing codes file

## Optional: Playwright

If token pages require JavaScript rendering:

```bash
pip install playwright
playwright install chromium
```

Set `USE_PLAYWRIGHT_FOR_TOKEN=true` in `.env`.
