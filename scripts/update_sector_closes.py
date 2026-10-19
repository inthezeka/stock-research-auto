#!/usr/bin/env python3
"""Daily close snapshot for every listed Fundamental sector candidate.

Quotes are accepted only when their daily OHLC bar is dated to the same
trading session as stock-universe.json. Missing or conflicting providers are
recorded explicitly. Discovery snapshots must never impersonate valid closes.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import update_data as stock

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "fundamental_themes.json"
UNIVERSE = ROOT / "stock-universe.json"
OUT = ROOT / "data" / "sector-closes.json"


def normalize_close(item, rows, provider, asof):
    if not rows:
        raise ValueError("empty OHLC")
    last = rows[-1]
    date = str(last.get("date") or "")
    if date != asof:
        raise ValueError(f"stale daily candle {date}, expected {asof}")
    close = stock.n(last.get("close"))
    high = stock.n(last.get("high"))
    low = stock.n(last.get("low"))
    if close is None or close <= 0 or high is None or low is None or not low <= close <= high:
        raise ValueError(f"invalid OHLC for {item['code']}: {low}, {close}, {high}")
    price = int(round(close))
    listing_price = stock.n(item.get("close"))
    listing_agrees = listing_price is not None and int(round(listing_price)) == price
    return {
        "date": asof,
        "close": price,
        "source": provider,
        "status": "DAILY_CLOSE",
        "reference_agrees": listing_agrees,
        "listing_reference_close": int(round(listing_price)) if listing_price is not None else None,
    }


def get_close(item, asof):
    failures = []
    try:
        rows, _ = stock.yahoo_chart(item["ticker"], range_="3mo")
        return normalize_close(item, rows, "Yahoo Finance daily unadjusted close", asof), None
    except Exception as exc:
        failures.append("Yahoo: " + str(exc)[:180])
    try:
        rows, _ = stock.naver_stock_chart(item["code"], 90)
        return normalize_close(item, rows, "Naver Stock daily candle (single source)", asof), None
    except Exception as exc:
        failures.append("Naver: " + str(exc)[:180])
    return None, "; ".join(failures)


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    by_code = {i["code"]: i for i in universe.get("items", [])}
    asof = str(universe.get("as_of") or "")
    codes = sorted(set(str(code).zfill(6) for sector in config["sectors"] for code in (sector.get("codes") or [])))
    if not asof or len(codes) < 100:
        raise SystemExit("sector universe/date missing; refuse incomplete quotes")

    quotes = {}
    failures = {}
    to_fetch = []
    for code in codes:
        item = by_code.get(code)
        if not item:
            failures[code] = "no longer listed in KOSPI/KOSDAQ universe"
            continue
        if item.get("close_status") == "VERIFIED" and item.get("close_date") == asof and stock.n(item.get("close")):
            quotes[code] = {
                "date": asof,
                "close": int(round(float(item["close"]))),
                "status": "VERIFIED",
                "source": item.get("close_source", "verified daily close"),
                "reference_agrees": True,
            }
        else:
            to_fetch.append(item)

    concurrency = max(4, min(20, int(os.environ.get("SECTOR_CLOSE_WORKERS", "16"))))
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        pending = {ex.submit(get_close, item, asof): item["code"] for item in to_fetch}
        for fut in as_completed(pending):
            code = pending[fut]
            try:
                quote, error = fut.result()
            except Exception as exc:
                quote, error = None, str(exc)
            if quote is not None:
                quotes[code] = quote
            else:
                failures[code] = error or "daily closing price unavailable"

    payload = {
        "as_of": asof,
        "generated_at": stock.NOW.isoformat(),
        "method": "verified watchlist + dated Yahoo daily close; Naver candle fallback",
        "requested": len(codes),
        "listed": len(codes) - sum("no longer listed" in str(x) for x in failures.values()),
        "confirmed": len(quotes),
        "unavailable": len(failures),
        "reference_disagreements": sum(x.get("reference_agrees") is False for x in quotes.values()),
        "items": {k: quotes[k] for k in sorted(quotes)},
        "errors": {k: failures[k] for k in sorted(failures)},
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(OUT)
    print(json.dumps({k: payload[k] for k in ("as_of", "requested", "confirmed", "unavailable", "reference_disagreements")}, ensure_ascii=False))
    # Do not publish if too many sector names are unavailable. The remainder
    # must stay visibly unverified, not falsely labelled as fresh.
    if len(quotes) / max(len(codes), 1) < 0.95:
        raise SystemExit("sector close coverage under 95%: do not deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
