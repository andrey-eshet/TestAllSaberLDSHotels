"""Follow the Token link chain to extract destination codes.

Path: Token page → HotelsGW → Umbraco → Request
The Request page contains a URL like:
  http://10.1.0.5/umbraco/api/Destinations/GetDestinationsEntireDataByCodes
    ?codes=IRF&codeType=SabreCode&...

We extract the `codes` parameter from that URL.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import httpx

from src.config import ARTIFACTS_HTML_DIR, ARTIFACTS_TOKENS_DIR, BASE_ORIGIN, USE_PLAYWRIGHT_FOR_TOKEN
from src.utils import get_logger, save_html

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_destination_code(
    token_url: str,
    hotel_id: str,
    client: httpx.Client,
) -> Optional[str]:
    """Follow the token chain and return the *codes* value, or None."""
    if not token_url:
        log.warning("HOTELID=%s  no token URL available", hotel_id)
        return None

    # Resolve relative token URLs (e.g. /Trace?token=...) to absolute
    if token_url.startswith("/"):
        token_url = BASE_ORIGIN + token_url

    if USE_PLAYWRIGHT_FOR_TOKEN:
        return _extract_via_playwright(token_url, hotel_id)

    return _extract_via_http(token_url, hotel_id, client)


# ---------------------------------------------------------------------------
# HTTP-based extraction (default)
# ---------------------------------------------------------------------------

def _extract_via_http(
    token_url: str,
    hotel_id: str,
    client: httpx.Client,
) -> Optional[str]:
    """Walk the link chain using plain HTTP requests."""
    try:
        # Step 1: Fetch the Token page
        log.info("HOTELID=%s  fetching token page: %s", hotel_id, token_url)
        resp = client.get(token_url)
        resp.raise_for_status()
        token_html = resp.text
        save_html(ARTIFACTS_TOKENS_DIR, f"{hotel_id}_token.html", token_html)

        # Step 2: Find the "HotelsGW" link
        hotelsgw_url = _find_link_by_text(token_html, "HotelsGW")
        if not hotelsgw_url:
            # Try to find the codes directly in the token page
            codes = _find_codes_in_html(token_html)
            if codes:
                return codes
            log.warning("HOTELID=%s  'HotelsGW' link not found on token page", hotel_id)
            return None

        hotelsgw_url = _resolve_url(token_url, hotelsgw_url)
        log.info("HOTELID=%s  fetching HotelsGW: %s", hotel_id, hotelsgw_url)
        resp = client.get(hotelsgw_url)
        resp.raise_for_status()
        gw_html = resp.text
        save_html(ARTIFACTS_TOKENS_DIR, f"{hotel_id}_hotelsgw.html", gw_html)

        # Step 3: Find the "Umbraco" link
        umbraco_url = _find_link_by_text(gw_html, "Umbraco")
        if not umbraco_url:
            codes = _find_codes_in_html(gw_html)
            if codes:
                return codes
            log.warning("HOTELID=%s  'Umbraco' link not found on HotelsGW page", hotel_id)
            return None

        umbraco_url = _resolve_url(hotelsgw_url, umbraco_url)
        log.info("HOTELID=%s  fetching Umbraco: %s", hotel_id, umbraco_url)
        resp = client.get(umbraco_url)
        resp.raise_for_status()
        umbraco_html = resp.text
        save_html(ARTIFACTS_TOKENS_DIR, f"{hotel_id}_umbraco.html", umbraco_html)

        # Step 4: Find the "Request" link
        request_url = _find_link_by_text(umbraco_html, "Request")
        if not request_url:
            codes = _find_codes_in_html(umbraco_html)
            if codes:
                return codes
            log.warning("HOTELID=%s  'Request' link not found on Umbraco page", hotel_id)
            return None

        request_url = _resolve_url(umbraco_url, request_url)
        log.info("HOTELID=%s  fetching Request: %s", hotel_id, request_url)
        resp = client.get(request_url)
        resp.raise_for_status()
        request_html = resp.text
        save_html(ARTIFACTS_TOKENS_DIR, f"{hotel_id}_request.html", request_html)

        # Step 5: Extract codes= from the Request page
        codes = _find_codes_in_html(request_html)
        if codes:
            log.info("HOTELID=%s  destination code extracted: %s", hotel_id, codes)
            return codes

        log.warning("HOTELID=%s  could not extract 'codes' from Request page", hotel_id)
        return None

    except httpx.HTTPError as exc:
        log.error("HOTELID=%s  HTTP error during token chain: %s", hotel_id, exc)
        return None


# ---------------------------------------------------------------------------
# Playwright fallback
# ---------------------------------------------------------------------------

def _extract_via_playwright(token_url: str, hotel_id: str) -> Optional[str]:
    """Use Playwright to navigate the token chain (JS-heavy pages)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Token page
            log.info("HOTELID=%s  [Playwright] opening token page", hotel_id)
            page.goto(token_url, wait_until="networkidle")
            save_html(ARTIFACTS_TOKENS_DIR, f"{hotel_id}_token_pw.html", page.content())

            # Click HotelsGW
            hotelsgw_link = page.locator("a", has_text="HotelsGW").first
            if hotelsgw_link.count() == 0:
                log.warning("HOTELID=%s  [Playwright] HotelsGW link not found", hotel_id)
                browser.close()
                return None
            hotelsgw_link.click()
            page.wait_for_load_state("networkidle")
            save_html(ARTIFACTS_TOKENS_DIR, f"{hotel_id}_hotelsgw_pw.html", page.content())

            # Click Umbraco
            umbraco_link = page.locator("a", has_text="Umbraco").first
            if umbraco_link.count() == 0:
                log.warning("HOTELID=%s  [Playwright] Umbraco link not found", hotel_id)
                browser.close()
                return None
            umbraco_link.click()
            page.wait_for_load_state("networkidle")
            save_html(ARTIFACTS_TOKENS_DIR, f"{hotel_id}_umbraco_pw.html", page.content())

            # Click Request
            request_link = page.locator("a", has_text="Request").first
            if request_link.count() == 0:
                log.warning("HOTELID=%s  [Playwright] Request link not found", hotel_id)
                browser.close()
                return None
            request_link.click()
            page.wait_for_load_state("networkidle")
            request_html = page.content()
            save_html(ARTIFACTS_TOKENS_DIR, f"{hotel_id}_request_pw.html", request_html)

            browser.close()

            codes = _find_codes_in_html(request_html)
            if codes:
                log.info("HOTELID=%s  [Playwright] destination code: %s", hotel_id, codes)
            return codes

    except Exception as exc:
        log.error("HOTELID=%s  [Playwright] error: %s", hotel_id, exc)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_link_by_text(html: str, link_text: str) -> Optional[str]:
    """Find the href of an <a> whose visible text contains *link_text*."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a"):
        if a.get_text(strip=True) and link_text.lower() in a.get_text(strip=True).lower():
            href = a.get("href")
            if href:
                return href
    return None


def _find_codes_in_html(html: str) -> Optional[str]:
    """Search for 'codes=XXX' in any URL found in the HTML text."""
    # Try regex on the raw HTML for any URL with codes=
    match = re.search(r'[?&]codes=([^&"\s<>\']+)', html, re.I)
    if match:
        return match.group(1)

    # Try parsing all hrefs
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        parsed = urlparse(a["href"])
        qs = parse_qs(parsed.query)
        if "codes" in qs:
            return qs["codes"][0]

    return None


def _resolve_url(base_url: str, relative_url: str) -> str:
    """Resolve a potentially relative URL against a base."""
    if relative_url.startswith("http://") or relative_url.startswith("https://"):
        return relative_url
    from urllib.parse import urljoin
    return urljoin(base_url, relative_url)
