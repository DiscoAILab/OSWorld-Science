"""File readers shared by evaluators (ported from the legacy checks.py)."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def read_json(p: Path):
    try:
        return json.loads(p.read_text()), None
    except FileNotFoundError:
        return None, f"missing file: {p.name}"
    except json.JSONDecodeError as e:
        return None, f"{p.name} is not valid JSON: {e}"


def read_csv(p: Path):
    try:
        with p.open(newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(8192)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(fh, dialect=dialect))
        return rows, None
    except FileNotFoundError:
        return None, f"missing file: {p.name}"
    except Exception as e:  # noqa: BLE001
        return None, f"{p.name} could not be parsed as CSV: {e}"
