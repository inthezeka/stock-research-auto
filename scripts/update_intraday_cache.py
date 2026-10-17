#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT / "stock-universe.json"
OUT_PATH = ROOT / "data" / "intraday-cache.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

MAX_CANDIDATES = int(os.getenv("INTRADAY_MAX_CANDIDATES", "240"))
MAX_WORKERS = int(os.getenv("INTRADAY_MAX_WORKERS", "12"))
TTL_HOURS = float(os.getenv("INTRADAY_TTL_HOURS", "4"))
FORCE = str(os.getenv("INTRADAY_FORCE", "")).lower() in ("1", "true", "yes")
MIN_TURNOVER = 5_000_000_000
MIN_MARKETCAP = 100_000_000_000

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 stock-research-auto/1.0",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.5",
})


def parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception:
        return None


def load_existing():
    if not OUT_PATH.exists():
        return {}
    try:
        return json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def normalize_market(value):
    s = str(value or "").upper()
    if "KOSDAQ" in s or "KQ" in s:
        return "KOSDAQ"
    return "KOSPI"


def yahoo_symbol(item):
    code = str(item.get("code") or "").zfill(6)
    return code + (".KQ" if normalize_market(item.get("market")) == "KOSDAQ" else ".KS")


def safe_num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def fetch_yahoo_rows(item):
    symbol = yahoo_symbol(item)
    params = "interval=60m&range=1mo&includePrePost=false&events=div%2Csplits"
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{symbol}?{params}"
        try:
            r = SESSION.get(url, timeout=(3.5, 8.0))
            if r.status_code != 200:
                errors.append(f"{host}:{r.status_code}")
                continue
            payload = r.json()
            chart = payload.get("chart") or {}
            result = (chart.get("result") or [None])[0]
            if not result:
                errors.append(f"{host}:empty")
                continue
            timestamps = result.get("timestamp") or []
            quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            vols = quote.get("volume") or []
            rows = []
            for i, ts in enumerate(timestamps):
                if i >= min(len(opens), len(highs), len(lows), len(closes)):
                    break
                o, h, l, c = map(safe_num, (opens[i], highs[i], lows[i], closes[i]))
                if None in (o, h, l, c):
                    continue
                v = safe_num(vols[i] if i < len(vols) else 0) or 0
                dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(KST)
                rows.append({
                    "date": dt.strftime("%Y%m%d%H%M%S"),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                })
            rows.sort(key=lambda x: x["date"])
            if len(rows) >= 20:
                return {
                    "code": str(item.get("code") or "").zfill(6),
                    "symbol": symbol,
                    "source": "YAHOO_60M",
                    "host": host,
                    "rows": rows[-180:],
                    "row_count": len(rows[-180:]),
                    "last_bar": rows[-1]["date"],
                    "fetched_at": NOW.isoformat(),
                    "error": None,
                }
            errors.append(f"{host}:rows={len(rows)}")
        except Exception as e:
            errors.append(f"{host}:{type(e).__name__}")
    return {
        "code": str(item.get("code") or "").zfill(6),
        "symbol": symbol,
        "source": "NONE",
        "rows": [],
        "row_count": 0,
        "last_bar": None,
        "fetched_at": NOW.isoformat(),
        "error": " | ".join(errors[-4:]),
    }


def select_candidates(universe):
    items = list(universe.get("items") or [])
    liquid = [
        x for x in items
        if float(x.get("turnover") or 0) >= MIN_TURNOVER
        and float(x.get("marketcap") or 0) >= MIN_MARKETCAP
    ]
    liquid.sort(key=lambda x: (
        -float(x.get("turnover") or 0),
        -float(x.get("marketcap") or 0),
        str(x.get("code") or ""),
    ))
    if len(liquid) < 50:
        liquid = [x for x in items if float(x.get("marketcap") or 0) >= MIN_MARKETCAP]
        liquid.sort(key=lambda x: (
            float(x.get("trading_value_rank") or 999999),
            float(x.get("volume_rank") or 999999),
            -float(x.get("marketcap") or 0),
        ))
    return liquid[:MAX_CANDIDATES]


def should_skip(existing):
    if FORCE:
        return False
    meta = existing.get("meta") or {}
    updated = parse_iso(meta.get("updated_at"))
    if not updated:
        return False
    return NOW - updated < timedelta(hours=TTL_HOURS)


def main():
    universe = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    existing = load_existing()
    if should_skip(existing):
        print(json.dumps({
            "status": "skipped",
            "reason": "cache fresh",
            "updated_at": (existing.get("meta") or {}).get("updated_at"),
            "count": len(existing.get("items") or {}),
        }, ensure_ascii=False))
        return 0

    candidates = select_candidates(universe)
    old_items = existing.get("items") or {}
    fetched = {}
    failures = 0

    started = time.time()
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(candidates)))) as ex:
        futs = {ex.submit(fetch_yahoo_rows, item): item for item in candidates}
        for fut in as_completed(futs):
            item = futs[fut]
            code = str(item.get("code") or "").zfill(6)
            try:
                row = fut.result()
            except Exception as e:
                row = {
                    "code": code,
                    "source": "NONE",
                    "rows": [],
                    "row_count": 0,
                    "last_bar": None,
                    "fetched_at": NOW.isoformat(),
                    "error": type(e).__name__,
                }

            if row.get("row_count", 0) >= 20:
                fetched[code] = row
            else:
                failures += 1
                old = old_items.get(code)
                if old and len(old.get("rows") or []) >= 20:
                    old = dict(old)
                    old["stale"] = True
                    old["refresh_error"] = row.get("error")
                    fetched[code] = old
                else:
                    fetched[code] = row

    usable = sum(1 for x in fetched.values() if len(x.get("rows") or []) >= 20)
    high_ready = sum(1 for x in fetched.values() if len(x.get("rows") or []) >= 40)
    last_bars = [x.get("last_bar") for x in fetched.values() if x.get("last_bar")]
    last_bar = max(last_bars) if last_bars else None

    payload = {
        "meta": {
            "updated_at": NOW.isoformat(),
            "market_date": universe.get("as_of"),
            "source": "Yahoo Finance public chart API · 60m",
            "source_note": "Static GitHub Pages fallback for MY Trading 1H/4H. Browser may additionally try live Naver/Yahoo.",
            "candidate_count": len(candidates),
            "usable_count": usable,
            "high_confidence_ready_count": high_ready,
            "failure_count": failures,
            "max_candidates": MAX_CANDIDATES,
            "last_bar": last_bar,
            "elapsed_seconds": round(time.time() - started, 1),
        },
        "items": fetched,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(OUT_PATH)

    print(json.dumps({
        "status": "ok",
        "candidates": len(candidates),
        "usable": usable,
        "high_ready": high_ready,
        "failures": failures,
        "last_bar": last_bar,
        "elapsed_seconds": round(time.time() - started, 1),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
