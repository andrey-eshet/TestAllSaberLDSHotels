#!/usr/bin/env python
"""Local entry-point: run the full hotel static-data check."""

import sys
from pathlib import Path

# Ensure project root is on sys.path so `src.*` imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.runner import run  # noqa: E402

if __name__ == "__main__":
    run()
