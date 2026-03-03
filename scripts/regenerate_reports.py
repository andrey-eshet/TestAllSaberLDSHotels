#!/usr/bin/env python
"""Regenerate report files from existing report.json without re-running the check."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
from src.config import REPORTS_DIR
from src.report_writer import (
    CompleteHotel,
    IncompleteHotel,
    Summary,
    write_reports,
)

def main() -> None:
    json_path = REPORTS_DIR / "report.json"
    if not json_path.exists():
        print(f"ERROR: {json_path} not found. Run the full check first.")
        sys.exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))

    complete = [CompleteHotel(**h) for h in data["complete_hotels"]]
    incomplete = [IncompleteHotel(**h) for h in data["incomplete_hotels"]]
    s = data["summary"]
    summary = Summary(
        total=s["total_checked"],
        complete=s["complete"],
        incomplete=s["incomplete"],
        not_found=s["not_found"],
    )

    write_reports(complete, incomplete, summary)
    print(f"Done. Reports regenerated in {REPORTS_DIR}")


if __name__ == "__main__":
    main()
