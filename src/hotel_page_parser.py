"""Fetch and parse the AbroadStaticData/Hotel page for a given HOTELID."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from bs4 import BeautifulSoup, Tag
import httpx

from src.config import (
    ARTIFACTS_HTML_DIR,
    RETRY_COUNT,
    hotel_url,
)
from src.utils import get_logger, safe_get, save_html

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ImageInfo:
    url: str = ""
    name: str = ""
    alt: str = ""
    is_main: str = ""


@dataclass
class HotelParseResult:
    hotel_id: str = ""
    found: bool = False
    name: str = ""
    stars: str = ""
    zone: str = ""
    destination_name: str = ""
    country_name: str = ""
    images: List[ImageInfo] = field(default_factory=list)
    token_url: str = ""
    raw_html: str = ""
    missing_fields: List[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_and_parse(hotel_id: str, client: httpx.Client) -> HotelParseResult:
    """Try up to RETRY_COUNT attempts; return parsed result."""
    url = hotel_url(hotel_id)
    result = HotelParseResult(hotel_id=hotel_id)
    last_html: str = ""

    for attempt in range(1, RETRY_COUNT + 1):
        log.info("HOTELID=%s  attempt %d/%d  GET %s", hotel_id, attempt, RETRY_COUNT, url)
        try:
            resp = safe_get(client, url)
            resp.raise_for_status()
            last_html = resp.text
        except httpx.HTTPError as exc:
            log.warning("HOTELID=%s  attempt %d  HTTP error: %s", hotel_id, attempt, exc)
            if attempt < RETRY_COUNT:
                time.sleep(2)
            continue

        if _page_has_data(last_html, hotel_id):
            result.found = True
            result.raw_html = last_html
            _parse_fields(last_html, result)
            log.info("HOTELID=%s  data found on attempt %d", hotel_id, attempt)
            break

        # Data not present yet — retry
        if attempt < RETRY_COUNT:
            time.sleep(3)

    # If not found after all retries, persist HTML for debug
    if not result.found:
        result.raw_html = last_html
        log.warning("HOTELID=%s  NOT FOUND after %d attempts", hotel_id, RETRY_COUNT)
        if last_html:
            result.token_url = _extract_token_url(last_html)
        save_html(ARTIFACTS_HTML_DIR, f"{hotel_id}.html", last_html)

    # Compute missing fields
    _compute_missing(result)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _page_has_data(html: str, hotel_id: str) -> bool:
    """Check if the page contains 'Hotel Code' together with the HOTELID value."""
    return "Hotel Code" in html and hotel_id in html


def _parse_fields(html: str, result: HotelParseResult) -> None:
    """Extract Name, Stars, Zone, Destination Name, Country Name, Images."""
    soup = BeautifulSoup(html, "html.parser")

    result.name = _find_value_by_label(soup, "Name") or ""
    result.stars = _find_value_by_label(soup, "Stars") or ""
    result.zone = _find_th_td_value(soup, "Zone")
    result.destination_name = _find_th_td_value(soup, "Destination Name")
    result.country_name = _find_th_td_value(soup, "Country Name")
    result.token_url = _extract_token_url(html)

    # --- Images table ---
    result.images = _parse_images_table(soup)


def _find_th_td_value(soup: BeautifulSoup, label: str) -> str:
    """Find exact <th>Label</th><td>Value</td> pair and return value (may be empty)."""
    th = soup.find("th", string=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I))
    if th:
        td = th.find_next_sibling("td")
        if td:
            return td.get_text(strip=True)
    return ""


def _find_value_by_label(soup: BeautifulSoup, label: str) -> Optional[str]:
    """Find a text node matching *label* and return the associated value.

    Supports several common HTML patterns:
    - <dt>Label</dt><dd>Value</dd>
    - <th>Label</th><td>Value</td>
    - <b>Label</b> or <strong>Label</strong> followed by text
    - Plain text "Label: Value"
    """
    # Pattern 1: dt/dd or th/td
    for tag_name in ("dt", "th", "b", "strong", "label"):
        tags = soup.find_all(tag_name, string=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I))
        for tag in tags:
            sibling = tag.find_next_sibling()
            if sibling:
                text = sibling.get_text(strip=True)
                if text:
                    return text

    # Pattern 2: text contains "Label:" or "Label :"
    text_nodes = soup.find_all(string=re.compile(rf"{re.escape(label)}\s*:", re.I))
    for node in text_nodes:
        match = re.search(rf"{re.escape(label)}\s*:\s*(.+)", node.strip(), re.I)
        if match:
            return match.group(1).strip()

    # Pattern 3: look inside table rows
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        for i, cell in enumerate(cells):
            if cell.get_text(strip=True).lower() == label.lower() and i + 1 < len(cells):
                return cells[i + 1].get_text(strip=True)

    return None


def _parse_images_table(soup: BeautifulSoup) -> List[ImageInfo]:
    """Find the Images section / table and extract rows."""
    images: List[ImageInfo] = []

    # Try to find a heading/label for "Images" then the nearest table
    images_header = soup.find(string=re.compile(r"^\s*Images\s*$", re.I))
    table: Optional[Tag] = None
    if images_header:
        parent = images_header.parent if images_header else None
        if parent:
            table = parent.find_next("table")
    if table is None:
        # Fallback: find any table whose header row contains "Url"
        for t in soup.find_all("table"):
            first_row = t.find("tr")
            if first_row and "Url" in first_row.get_text():
                table = t
                break

    if table is None:
        return images

    rows = table.find_all("tr")
    if len(rows) < 2:
        return images

    # Determine column indices from header row
    header_cells = rows[0].find_all(["th", "td"])
    col_map: dict[str, int] = {}
    for idx, cell in enumerate(header_cells):
        text = cell.get_text(strip=True)
        lower = text.lower()
        if "url" in lower:
            col_map["url"] = idx
        elif "is main" in lower or "ismain" in lower:
            col_map["is_main"] = idx
        elif "alt" in lower:
            col_map["alt"] = idx
        elif "name" in lower:
            col_map["name"] = idx

    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        img = ImageInfo()
        if "url" in col_map and col_map["url"] < len(cells):
            url_cell = cells[col_map["url"]]
            a_tag = url_cell.find("a")
            img_tag = url_cell.find("img")
            if a_tag and a_tag.get("href"):
                img.url = a_tag["href"]
            elif img_tag and img_tag.get("src"):
                img.url = img_tag["src"]
            else:
                img.url = url_cell.get_text(strip=True)
        if "name" in col_map and col_map["name"] < len(cells):
            img.name = cells[col_map["name"]].get_text(strip=True)
        if "alt" in col_map and col_map["alt"] < len(cells):
            img.alt = cells[col_map["alt"]].get_text(strip=True)
        if "is_main" in col_map and col_map["is_main"] < len(cells):
            img.is_main = cells[col_map["is_main"]].get_text(strip=True)
        images.append(img)

    return images


def _extract_token_url(html: str) -> str:
    """Pull the Token link from the page (Token: <a href='...'>)."""
    soup = BeautifulSoup(html, "html.parser")
    token_label = soup.find(string=re.compile(r"Token", re.I))
    if token_label:
        parent = token_label.parent if token_label else None
        if parent:
            a_tag = parent.find_next("a")
            if a_tag and a_tag.get("href"):
                return a_tag["href"]
    # Regex fallback
    match = re.search(r'Token\s*:\s*<a[^>]+href=["\']([^"\']+)["\']', html, re.I)
    if match:
        return match.group(1)
    return ""


# ---------------------------------------------------------------------------
# Missing-field logic
# ---------------------------------------------------------------------------

def _compute_missing(result: HotelParseResult) -> None:
    """Populate result.missing_fields based on completeness rules."""
    missing: List[str] = []

    if not result.found:
        missing.append("ALL (hotel not found)")
        result.missing_fields = missing
        return

    if not result.name:
        missing.append("Name")
    if not result.stars or "*" not in result.stars:
        missing.append("Stars")
    if not result.images:
        missing.append("Images (no images section or empty)")
    else:
        has_url = False
        for idx, img in enumerate(result.images):
            if img.url:
                has_url = True
            else:
                missing.append(f"Image[{idx}].Url")
            if not img.name:
                missing.append(f"Image[{idx}].Name")
            if not img.alt:
                missing.append(f"Image[{idx}].Alt")
        if not has_url:
            missing.append("Images (no image with Url)")

    result.missing_fields = missing
