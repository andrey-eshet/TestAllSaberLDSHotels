"""Main orchestrator: reads input, checks each hotel, builds the report.

Designed for long runs (16000+ hotels, 2-3 hours):
- Saves progress to disk every CHECKPOINT_EVERY hotels
- Catches per-hotel exceptions so one bad hotel doesn't kill the run
- Recreates HTTP client periodically to avoid stale connections
- Shows ETA and progress percentage
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from src.config import (
    DATA_HOTEL_LIST_XLSX,
    DATA_MISSING_CODES_XLSX,
    REPORTS_DIR,
    ensure_dirs,
)
from src.excel_reader import read_hotel_ids, read_missing_codes
from src.hotel_page_parser import HotelParseResult, fetch_and_parse
from src.token_parser import extract_destination_code
from src.report_writer import (
    CompleteHotel,
    IncompleteHotel,
    Summary,
    write_reports,
)
from src.utils import get_logger, make_client

log = get_logger(__name__)

# Save intermediate results every N hotels
CHECKPOINT_EVERY = 200

# Recreate HTTP client every N hotels to avoid stale connections
CLIENT_REFRESH_EVERY = 500


def run() -> None:
    """Entry point for the full check pipeline."""
    ensure_dirs()

    # 1. Read inputs
    hotel_ids = read_hotel_ids(DATA_HOTEL_LIST_XLSX)
    missing_codes_map: Dict[str, str] = read_missing_codes(DATA_MISSING_CODES_XLSX)

    total = len(hotel_ids)
    log.info("Starting check for %d hotels", total)

    # 2. Try to resume from checkpoint
    complete, incomplete, not_found_count, start_idx = _load_checkpoint()
    processed_ids = {h.hotel_id for h in complete} | {h.hotel_id for h in incomplete}

    if start_idx > 0:
        log.info("Resuming from checkpoint: %d already processed, starting at index %d", len(processed_ids), start_idx)

    client = make_client()
    run_start = time.time()

    for idx, hotel_id in enumerate(hotel_ids, start=1):
        # Skip already processed hotels (resume support)
        if hotel_id in processed_ids:
            continue

        # Progress + ETA
        done = len(processed_ids)
        elapsed = time.time() - run_start
        if done > start_idx:
            newly_done = done - start_idx
            avg_per_hotel = elapsed / newly_done if newly_done else 0
            remaining = total - done
            eta_seconds = avg_per_hotel * remaining
            eta_min = eta_seconds / 60
            log.info(
                "--- [%d/%d] (%.1f%%) HOTELID=%s  ETA: %.0f min ---",
                done + 1, total, (done / total) * 100, hotel_id, eta_min,
            )
        else:
            log.info("--- [%d/%d] HOTELID=%s ---", idx, total, hotel_id)

        # Refresh HTTP client periodically
        if done > 0 and done % CLIENT_REFRESH_EVERY == 0:
            log.info("Refreshing HTTP client (every %d hotels)", CLIENT_REFRESH_EVERY)
            try:
                client.close()
            except Exception:
                pass
            client = make_client()

        # Process single hotel with exception guard
        try:
            _process_one_hotel(hotel_id, client, missing_codes_map, complete, incomplete)
            if not complete or complete[-1].hotel_id != hotel_id:
                # It went to incomplete — check if NOT_FOUND
                if incomplete and incomplete[-1].hotel_id == hotel_id and incomplete[-1].status == "NOT_FOUND":
                    not_found_count += 1
        except Exception as exc:
            log.error("HOTELID=%s  UNEXPECTED ERROR: %s", hotel_id, exc, exc_info=True)
            incomplete.append(
                IncompleteHotel(
                    hotel_id=hotel_id,
                    status="NOT_FOUND",
                    missing_fields=["ALL (unexpected error)"],
                    name="שם לא זמין",
                    explanation=f"שגיאה לא צפויה: {exc}",
                )
            )
            not_found_count += 1

        processed_ids.add(hotel_id)

        # Checkpoint
        if len(processed_ids) % CHECKPOINT_EVERY == 0:
            _save_checkpoint(complete, incomplete, not_found_count, len(processed_ids))
            log.info("Checkpoint saved: %d hotels processed", len(processed_ids))

    try:
        client.close()
    except Exception:
        pass

    # 3. Build summary
    incomplete_only = len(incomplete) - not_found_count
    summary = Summary(
        total=total,
        complete=len(complete),
        incomplete=incomplete_only,
        not_found=not_found_count,
    )

    # 4. Write final reports
    write_reports(complete, incomplete, summary)

    # 5. Clean up checkpoint file
    _remove_checkpoint()

    log.info("=== DONE ===")
    log.info(
        "Total=%d  Complete=%d  Incomplete=%d  NotFound=%d",
        summary.total,
        summary.complete,
        summary.incomplete,
        summary.not_found,
    )
    elapsed_total = time.time() - run_start
    log.info("Total time: %.1f minutes", elapsed_total / 60)


# ---------------------------------------------------------------------------
# Single hotel processing
# ---------------------------------------------------------------------------

def _process_one_hotel(
    hotel_id: str,
    client: httpx.Client,
    missing_codes_map: Dict[str, str],
    complete: List[CompleteHotel],
    incomplete: List[IncompleteHotel],
) -> None:
    """Process a single hotel and append to complete or incomplete list."""
    import httpx as _httpx  # local import to keep type checker happy

    result: HotelParseResult = fetch_and_parse(hotel_id, client)

    if result.found and not result.missing_fields:
        complete.append(
            CompleteHotel(
                hotel_id=result.hotel_id,
                name=result.name,
                stars=result.stars,
                images_count=len(result.images),
            )
        )
        log.info("HOTELID=%s  COMPLETE", hotel_id)

    elif result.found and result.missing_fields:
        incomplete.append(
            IncompleteHotel(
                hotel_id=result.hotel_id,
                status="INCOMPLETE",
                missing_fields=result.missing_fields,
                name=result.name or "שם לא זמין",
            )
        )
        log.info(
            "HOTELID=%s  INCOMPLETE  missing: %s",
            hotel_id,
            ", ".join(result.missing_fields),
        )

    else:
        # Not found — follow token chain
        codes = ""
        destination_en = ""
        name = result.name or "שם לא זמין"

        if result.token_url:
            try:
                codes = extract_destination_code(
                    result.token_url, hotel_id, client
                ) or ""
            except Exception as exc:
                log.warning("HOTELID=%s  token extraction failed: %s", hotel_id, exc)

        if codes:
            destination_en = missing_codes_map.get(codes, "לא נמצא בקובץ")
        else:
            destination_en = "לא ניתן לחלץ קוד יעד"

        explanation = (
            "המלון לא מגיע ב AbrodStaticData עבור SabreLDS "
            "לאחר 2 ניסיונות, קוד יעד חסר"
        )

        incomplete.append(
            IncompleteHotel(
                hotel_id=hotel_id,
                status="NOT_FOUND",
                missing_fields=result.missing_fields,
                name=name,
                codes=codes,
                destination_en=destination_en,
                token_url=result.token_url,
                explanation=explanation,
            )
        )
        log.info(
            "HOTELID=%s  NOT_FOUND  codes=%s  destination=%s",
            hotel_id,
            codes,
            destination_en,
        )


# ---------------------------------------------------------------------------
# Checkpoint (save / load / remove)
# ---------------------------------------------------------------------------

_CHECKPOINT_PATH = REPORTS_DIR / "_checkpoint.json"


def _save_checkpoint(
    complete: List[CompleteHotel],
    incomplete: List[IncompleteHotel],
    not_found_count: int,
    processed_count: int,
) -> None:
    """Persist current progress to a JSON checkpoint file."""
    data = {
        "processed_count": processed_count,
        "not_found_count": not_found_count,
        "complete": [h.to_dict() for h in complete],
        "incomplete": [h.to_dict() for h in incomplete],
    }
    _CHECKPOINT_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load_checkpoint() -> tuple:
    """Load checkpoint if it exists. Returns (complete, incomplete, not_found_count, start_idx)."""
    if not _CHECKPOINT_PATH.exists():
        return [], [], 0, 0

    try:
        data = json.loads(_CHECKPOINT_PATH.read_text(encoding="utf-8"))
        complete = [
            CompleteHotel(**h) for h in data.get("complete", [])
        ]
        incomplete = [
            IncompleteHotel(**h) for h in data.get("incomplete", [])
        ]
        not_found_count = data.get("not_found_count", 0)
        processed_count = data.get("processed_count", 0)
        log.info("Checkpoint loaded: %d complete, %d incomplete", len(complete), len(incomplete))
        return complete, incomplete, not_found_count, processed_count
    except Exception as exc:
        log.warning("Failed to load checkpoint, starting fresh: %s", exc)
        return [], [], 0, 0


def _remove_checkpoint() -> None:
    """Delete checkpoint file after successful completion."""
    if _CHECKPOINT_PATH.exists():
        _CHECKPOINT_PATH.unlink()
        log.info("Checkpoint file removed")
