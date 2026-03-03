"""Configuration loaded from environment variables / .env file."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
ARTIFACTS_HTML_DIR = ARTIFACTS_DIR / "html"
ARTIFACTS_TOKENS_DIR = ARTIFACTS_DIR / "tokens"
REPORTS_DIR = PROJECT_ROOT / "reports"

# Excel files
DATA_HOTEL_LIST_XLSX = PROJECT_ROOT / os.getenv(
    "DATA_HOTEL_LIST_XLSX", "data/simaFullHotelList.xlsx"
)
DATA_MISSING_CODES_XLSX = PROJECT_ROOT / os.getenv(
    "DATA_MISSING_CODES_XLSX", "data/missing_sabre_codes_after_Meital_filtering.xlsx"
)

# API
BASE_URL_HOTEL = os.getenv(
    "BASE_URL_HOTEL", "https://hgwcore.azurewebsites.net/AbroadStaticData/Hotel"
)
VENDOR = os.getenv("VENDOR", "SabreLDS")

# Base origin for resolving relative URLs (e.g. /Trace?token=...)
BASE_ORIGIN = os.getenv("BASE_ORIGIN", "https://hgwcore.azurewebsites.net")
HOTEL_ID_TYPE = os.getenv("HOTEL_ID_TYPE", "Sabre")

# Retry / timeout
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "2"))
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "30"))

# Token parsing strategy
USE_PLAYWRIGHT_FOR_TOKEN = os.getenv("USE_PLAYWRIGHT_FOR_TOKEN", "false").lower() == "true"


def hotel_url(hotel_id: str) -> str:
    """Build the full URL for a hotel static-data check."""
    return (
        f"{BASE_URL_HOTEL}"
        f"?hotelId={hotel_id}"
        f"&hotelIdType={HOTEL_ID_TYPE}"
        f"&vendor={VENDOR}"
    )


def ensure_dirs() -> None:
    """Create output directories if they don't exist."""
    for d in (ARTIFACTS_HTML_DIR, ARTIFACTS_TOKENS_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
