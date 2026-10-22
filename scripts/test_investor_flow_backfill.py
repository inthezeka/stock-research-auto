#!/usr/bin/env python3
"""Offline regression tests for signed, source-traceable 5D/20D/60D flows."""
from __future__ import annotations

import csv
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

from kis_investor_flow import (
    KisInvestorClient, collect_history, parse_output2, safe_int, safe_date,
)
from investor_flow_csv import read_backfill_csv
from update_investor_flow import merge_verified_source, HISTORY_DAYS

assert HISTORY_DAYS >= 65
assert safe_int("-1,234") == -1234
assert safe_int("+9,876") == 9876
assert safe_int("") is None
assert safe_int("—") is None
assert safe_date("20261021") == "2026-10-21"
assert safe_date("20261340") is None
assert parse_output2({"output2": [
    {"stck_bsop_date": "20261021", "frgn_ntby_qty": "-50",
     "orgn_ntby_qty": "+10", "prsn_ntby_qty": "40"},
    {"stck_bsop_date": "20261022", "frgn_ntby_qty": "50",
     "orgn_ntby_qty": "20", "prsn_ntby_qty": "-70"},
    {"stck_bsop_date": "20261020", "frgn_ntby_qty": "0",
     "orgn_ntby_qty": "", "prsn_ntby_qty": "0"},
], "rt_cd": "0"}, "2026-10-21") == [
    {"date": "2026-10-21", "foreign": -50, "institution": 10, "individual": 40},
]

# Simulate a 30-business-day per-page KIS response with overlapping windows.
weekdays = []
cursor = date(2026, 10, 21)
while len(weekdays) < 80:
    if cursor.weekday() < 5:
        weekdays.append(cursor)
    cursor -= timedelta(days=1)
weekdays.sort()
calls = []
def fake_get_page(code, end_yyyymmdd):
    assert code == "005930"
    calls.append(end_yyyymmdd)
    allowed = [x for x in weekdays if x.strftime("%Y%m%d") <= end_yyyymmdd]
    last = list(reversed(allowed[-30:]))
    return {"rt_cd": "0", "output2": [
        {"stck_bsop_date": d.strftime("%Y%m%d"), "frgn_ntby_qty": str(i+1),
         "orgn_ntby_qty": str(-(i+1)), "prsn_ntby_qty": "0"}
        for i, d in enumerate(last)
    ]}
rows = collect_history("005930", "2026-10-21", fake_get_page, target_rows=65)
assert len(rows) >= 65, (len(rows), calls)
assert len(calls) >= 3, calls
assert rows[-1]["date"] == "2026-10-21"
assert rows == sorted(rows, key=lambda x: x["date"])
assert len({r["date"] for r in rows}) == len(rows)
assert all(set(r) == {"date", "foreign", "institution", "individual"} for r in rows)

def duplicate_page(code, cursor):
    return {"rt_cd": "0", "output2": [
        {"stck_bsop_date": d.strftime("%Y%m%d"), "frgn_ntby_qty": "1",
         "orgn_ntby_qty": "2", "prsn_ntby_qty": "-3"}
        for d in reversed(weekdays[-30:])
    ]}
limited = collect_history("005930", "2026-10-21", duplicate_page, target_rows=65)
assert len(limited) == 30  # no duplicate older page presented as fresh history
assert collect_history("005930", "2026-10-21",
                       lambda c, d: {"rt_cd": "1"}, target_rows=65) == []
assert not KisInvestorClient(key="", secret="").authenticate()

# Authoritative KRX dates only: never add Naver rows to KIS rows when merging.
previous = {
    "source": "KIS investor-trade-by-stock-daily · KRX",
    "unit": "shares", "rows": rows, "last_date": rows[-1]["date"]
}
naver = {
    "source": "Naver integration investor trend", "unit": "shares",
    "rows": [{"date": "2026-10-21", "foreign": 999999, "institution": 0,
              "individual": 0}],
    "last_date": "2026-10-21"
}
official = merge_verified_source(naver, previous, [], previous["source"], "2026-10-21")
assert len(official["rows"]) >= 65
assert official["rows"][-1]["foreign"] != 999999
assert official["market_scope"] == "KRX"

with tempfile.TemporaryDirectory() as work:
    path = Path(work) / "krx.csv"
    with path.open("w", encoding="utf-8", newline="") as fp:
        wr = csv.DictWriter(fp, fieldnames=[
            "stock_code", "date", "foreign", "institution", "individual", "market"])
        wr.writeheader()
        for day in weekdays[-65:]:
            wr.writerow({"stock_code": "005930", "date": day.isoformat(),
                         "foreign": "1", "institution": "-2",
                         "individual": "1", "market": "KRX"})
    parsed = read_backfill_csv(path, "2026-10-21")
    assert len(parsed["005930"]) == 65
    assert parsed["005930"][-1]["date"] == "2026-10-21"
    with path.open("a", encoding="utf-8") as fp:
        fp.write("005930,2026-10-21,999,-2,1,KRX\n")
    try:
        read_backfill_csv(path, "2026-10-21")
        raise AssertionError("conflicting same-day KRX CSV did not fail")
    except ValueError:
        pass

print("KIS/CSV 5D 20D 60D investor-flow backfill regression PASS")
