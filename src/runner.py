"""Main orchestrator: reads input, checks each hotel, builds the report.

Designed for long runs (16000+ hotels):
- Uses ThreadPoolExecutor for parallel processing (WORKERS threads)
- Each thread has its own HTTP client
- Saves progress to disk every CHECKPOINT_EVERY hotels
- Catches per-hotel exceptions so one bad hotel doesn't kill the run
- Shows ETA and progress percentage
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from src.config import (
    DATA_HOTEL_LIST_XLSX,
    DATA_MISSING_CODES_XLSX,
    REPORTS_DIR,
    WORKERS,
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


# ---------------------------------------------------------------------------
# Thread-local HTTP clients
# ---------------------------------------------------------------------------

_thread_local = threading.local()


def _get_client() -> "httpx.Client":
    """Return a per-thread HTTP client, creating one if needed."""
    import httpx
    if not hasattr(_thread_local, "client"):
        _thread_local.client = make_client()
    return _thread_local.client


def _refresh_client() -> "httpx.Client":
    """Close and recreate the per-thread HTTP client."""
    import httpx
    if hasattr(_thread_local, "client"):
        try:
            _thread_local.client.close()
        except Exception:
            pass
    _thread_local.client = make_client()
    return _thread_local.client


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Entry point for the full check pipeline."""
    ensure_dirs()

    # 1. Read inputs
    hotel_ids = read_hotel_ids(DATA_HOTEL_LIST_XLSX)
    missing_codes_map: Dict[str, str] = read_missing_codes(DATA_MISSING_CODES_XLSX)

    total = len(hotel_ids)
    workers = min(WORKERS, total)
    log.info("Starting check for %d hotels with %d workers", total, workers)

    # 2. Try to resume from checkpoint
    complete, incomplete, not_found_count, start_idx = _load_checkpoint()
    processed_ids = {h.hotel_id for h in complete} | {h.hotel_id for h in incomplete}

    if start_idx > 0:
        log.info(
            "Resuming from checkpoint: %d already processed",
            len(processed_ids),
        )

    # Filter out already-processed hotels
    remaining_ids = [h for h in hotel_ids if h not in processed_ids]
    log.info("Hotels remaining: %d", len(remaining_ids))

    # Thread-safe structures
    lock = threading.Lock()
    done_count = len(processed_ids)
    run_start = time.time()

    def _worker(hotel_id: str) -> Tuple[str, Union[CompleteHotel, IncompleteHotel, None], bool]:
        """Process a single hotel. Returns (hotel_id, result_obj, is_not_found)."""
        client = _get_client()
        try:
            return _process_one_hotel(hotel_id, client, missing_codes_map)
        except Exception as exc:
            log.error("HOTELID=%s  UNEXPECTED ERROR: %s", hotel_id, exc, exc_info=True)
            # Recreate client after errors
            if "timeout" in str(exc).lower():
                _refresh_client()
            return (
                hotel_id,
                IncompleteHotel(
                    hotel_id=hotel_id,
                    status="NOT_FOUND",
                    missing_fields=["ALL (unexpected error)"],
                    name="\u05e9\u05dd \u05dc\u05d0 \u05d6\u05de\u05d9\u05df",
                    explanation=f"\u05e9\u05d2\u05d9\u05d0\u05d4 \u05dc\u05d0 \u05e6\u05e4\u05d5\u05d9\u05d4: {exc}",
                ),
                True,
            )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_worker, hid): hid for hid in remaining_ids
        }

        for future in as_completed(futures):
            hotel_id = futures[future]
            try:
                hid, result_obj, is_not_found = future.result()
            except Exception as exc:
                log.error("HOTELID=%s  future error: %s", hotel_id, exc)
                result_obj = IncompleteHotel(
                    hotel_id=hotel_id,
                    status="NOT_FOUND",
                    missing_fields=["ALL (future error)"],
                    name="\u05e9\u05dd \u05dc\u05d0 \u05d6\u05de\u05d9\u05df",
                    explanation=f"\u05e9\u05d2\u05d9\u05d0\u05d4: {exc}",
                )
                is_not_found = True

            with lock:
                if isinstance(result_obj, CompleteHotel):
                    complete.append(result_obj)
                elif isinstance(result_obj, IncompleteHotel):
                    incomplete.append(result_obj)
                    if is_not_found:
                        not_found_count += 1

                done_count += 1
                processed_ids.add(hotel_id)

                # Progress + ETA
                elapsed = time.time() - run_start
                newly_done = done_count - start_idx
                if newly_done > 0:
                    avg = elapsed / newly_done
                    remaining = total - done_count
                    eta_min = (avg * remaining) / 60
                    log.info(
                        "[%d/%d] (%.1f%%) HOTELID=%s  ETA: %.0f min",
                        done_count, total,
                        (done_count / total) * 100,
                        hotel_id, eta_min,
                    )

                # Checkpoint
                if done_count % CHECKPOINT_EVERY == 0:
                    _save_checkpoint(complete, incomplete, not_found_count, done_count)
                    log.info("Checkpoint saved: %d hotels processed", done_count)

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
# Single hotel processing (returns result, no shared mutation)
# ---------------------------------------------------------------------------

def _process_one_hotel(
    hotel_id: str,
    client: "httpx.Client",
    missing_codes_map: Dict[str, str],
) -> Tuple[str, Union[CompleteHotel, IncompleteHotel], bool]:
    """Process a single hotel.

    Returns (hotel_id, result_object, is_not_found).
    """
    result: HotelParseResult = fetch_and_parse(hotel_id, client)

    if result.found and not result.missing_fields:
        obj = CompleteHotel(
            hotel_id=result.hotel_id,
            name=result.name,
            stars=result.stars,
            images_count=len(result.images),
            zone=result.zone,
            destination_name=result.destination_name,
            country_name=result.country_name,
        )
        log.info("HOTELID=%s  COMPLETE", hotel_id)
        return (hotel_id, obj, False)

    elif result.found and result.missing_fields:
        obj = IncompleteHotel(
            hotel_id=result.hotel_id,
            status="INCOMPLETE",
            missing_fields=result.missing_fields,
            name=result.name or "\u05e9\u05dd \u05dc\u05d0 \u05d6\u05de\u05d9\u05df",
            zone=result.zone,
            destination_name=result.destination_name,
            country_name=result.country_name,
        )
        log.info(
            "HOTELID=%s  INCOMPLETE  missing: %s",
            hotel_id,
            ", ".join(result.missing_fields),
        )
        return (hotel_id, obj, False)

    else:
        # Not found — follow token chain
        codes = ""
        destination_en = ""
        name = result.name or "\u05e9\u05dd \u05dc\u05d0 \u05d6\u05de\u05d9\u05df"

        if result.token_url:
            try:
                codes = extract_destination_code(
                    result.token_url, hotel_id, client
                ) or ""
            except Exception as exc:
                log.warning("HOTELID=%s  token extraction failed: %s", hotel_id, exc)

        if codes:
            destination_en = missing_codes_map.get(codes, "\u05dc\u05d0 \u05e0\u05de\u05e6\u05d0 \u05d1\u05e7\u05d5\u05d1\u05e5")
        else:
            destination_en = "\u05dc\u05d0 \u05e0\u05d9\u05ea\u05df \u05dc\u05d7\u05dc\u05e5 \u05e7\u05d5\u05d3 \u05d9\u05e2\u05d3"

        explanation = (
            "\u05d4\u05de\u05dc\u05d5\u05df \u05dc\u05d0 \u05de\u05d2\u05d9\u05e2 \u05d1 AbrodStaticData \u05e2\u05d1\u05d5\u05e8 SabreLDS "
            "\u05dc\u05d0\u05d7\u05e8 2 \u05e0\u05d9\u05e1\u05d9\u05d5\u05e0\u05d5\u05ea, \u05e7\u05d5\u05d3 \u05d9\u05e2\u05d3 \u05d7\u05e1\u05e8"
        )

        obj = IncompleteHotel(
            hotel_id=hotel_id,
            status="NOT_FOUND",
            missing_fields=result.missing_fields,
            name=name,
            codes=codes,
            destination_en=destination_en,
            token_url=result.token_url,
            explanation=explanation,
            zone=result.zone,
            destination_name=result.destination_name,
            country_name=result.country_name,
        )
        log.info(
            "HOTELID=%s  NOT_FOUND  codes=%s  destination=%s",
            hotel_id,
            codes,
            destination_en,
        )
        return (hotel_id, obj, True)


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
