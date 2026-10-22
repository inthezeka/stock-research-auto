#!/usr/bin/env python3
"""Optional, user-authorized KRX CSV historical flow import.

Expected normalized UTF-8 CSV:
stock_code,date,foreign,institution,individual,market
005930,2026-10-21,2055241,313027,-2324529,KRX

This is an input adapter, not a downloader; the user must obtain data legally.
Absent or invalid files never introduce fabricated values into the dashboard.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path


def number(x):
    value = str(x if x is not None else "").strip().replace(",", "")
    if not re.fullmatch(r"[+-]?\\d+", value):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def date_string(value):
    value = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def read_backfill_csv(path: Path, cutoff: str, maximum_rows=90):
    if not path.exists():
        return {}
    group = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"stock_code", "date", "foreign", "institution", "individual", "market"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("KRX CSV must have stock_code,date,foreign,institution,individual,market")
        for i, item in enumerate(reader, start=2):
            code = str(item.get("stock_code") or "").strip()
            day = date_string(item.get("date"))
            market = str(item.get("market") or "").strip().upper()
            if not re.fullmatch(r"[0-9]{6}", code) or not day or market != "KRX":
                raise ValueError("Invalid KRX CSV code/date/market at row " + str(i))
            if day > cutoff:
                continue
            vals = {k: number(item.get(k)) for k in ("foreign", "institution", "individual")}
            if any(x is None for x in vals.values()):
                raise ValueError("Missing or non-integer KRX CSV quantities at row " + str(i))
            dest = group.setdefault(code, {})
            row = {"date": day, **vals}
            if day in dest and dest[day] != row:
                raise ValueError("Conflicting KRX CSV values for " + code + " on " + day)
            dest[day] = row
    return {code: sorted(records.values(), key=lambda r: r["date"])[-maximum_rows:]
            for code, records in group.items() if records}
