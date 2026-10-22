#!/usr/bin/env python3
"""Refresh dated, per-stock foreign/institution/individual net BUY quantities.

Net buying is in SHARES, not KRW. No estimated trading amounts are fabricated.
Only completed trading dates <= stock-universe.as_of are persisted.
Missing data remains null. The dashboard never treats missing 20D as 5D.
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investor-flow.json"
UNIVERSE = ROOT / "stock-universe.json"
WATCHLIST = ROOT / "config" / "watchlist.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
WORKERS = min(24, max(1, int(os.getenv("FLOW_WORKERS", "18"))))
MAX_CANDIDATES = min(800, max(50, int(os.getenv("FLOW_MAX_CANDIDATES", "500"))))
HISTORY_DAYS = 90  # retain at least 60 completed sessions, with validation headroom
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7",
}
KEYS = {
    "foreign": ("foreignerPureBuyQuant", "foreignPureBuyQuant", "frgnPureBuyQuant",
                "frgn_pure_buy_quant", "foreignPureBuyVolume", "foreignNetBuyQuantity"),
    "institution": ("organPureBuyQuant", "institutionPureBuyQuant", "orgPureBuyQuant",
                    "organ_pure_buy_quant", "institutionalPureBuyQuant", "institutionNetBuyQuantity"),
    "individual": ("individualPureBuyQuant", "personalPureBuyQuant",
                   "indiPureBuyQuant", "indi_pure_buy_quant", "personalNetBuyQuantity"),
}
DATE_KEYS = ("bizdate", "bizDate", "date", "tradeDate", "tradingDate",
             "localTradedAt", "day", "baseDate")
CONTAINERS = ("dealTrendInfos", "result", "data", "items", "rows", "list",
              "content", "tradeTrendInfos", "trendInfos", "trendList")


def num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        s = str(value).replace(",", "").replace(" ", "").replace("주", "").strip()
        if not s or s in ("-", "—", "N/A", "null"):
            return None
        if not re.fullmatch(r"[+-]?\d+(?:\.0+)?", s):
            return None
        return int(float(s))
    except (ValueError, OverflowError):
        return None


def date_iso(value):
    s = str(value or "").strip()
    m = re.match(r"^(20\d{2})[-./]?(\d{2})[-./]?(\d{2})", s)
    if not m:
        return None
    day = "-".join(m.groups())
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return None
    return day


def field(obj, aliases):
    for name in aliases:
        if name in obj:
            return num(obj.get(name))
    return None


def normalize_row(raw):
    if not isinstance(raw, dict):
        return None
    day = next((date_iso(raw.get(k)) for k in DATE_KEYS if date_iso(raw.get(k))), None)
    if not day:
        return None
    vals = {kind: field(raw, names) for kind, names in KEYS.items()}
    if sum(v is not None for v in vals.values()) < 2:
        return None
    return {"date": day, **vals}


def extract_rows(payload):
    found = []
    seen_nodes = 0

    def walk(value, depth=0):
        nonlocal seen_nodes
        seen_nodes += 1
        if depth > 6 or seen_nodes > 4000:
            return
        if isinstance(value, list):
            for row in value[:100]:
                walk(row, depth + 1)
        elif isinstance(value, dict):
            row = normalize_row(value)
            if row:
                found.append(row)
                return
            for key in CONTAINERS:
                if key in value:
                    walk(value[key], depth + 1)

    walk(payload)
    return sorted({r["date"]: r for r in found}.values(), key=lambda r: r["date"])


def json_sources(code):
    return [
        (f"https://m.stock.naver.com/front-api/stock/domestic/trend?code={code}",
         f"https://m.stock.naver.com/domestic/stock/{code}/total", "Naver front-api investor trend"),
        (f"https://api.stock.naver.com/api/domestic/detail/{code}/trend?tradeType=KRX&startIdx=0&pageSize=90",
         f"https://stock.naver.com/domestic/stock/{code}/trend", "Naver stock investor trend"),
        (f"https://m.stock.naver.com/api/stock/{code}/integration",
         f"https://m.stock.naver.com/domestic/stock/{code}", "Naver integration investor trend"),
    ]


def pc_rows(code, session, as_of):
    rows = []
    for page in range(1, 9):
        url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
        try:
            resp = session.get(url, headers={**HEADERS, "Referer": "https://finance.naver.com/"},
                               timeout=(2.5, 6.0))
            if resp.status_code != 200:
                break
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")
            # Identify dated rows across all tables, not just the first table.type2.
            previous_count = len(rows)
            for tr in soup.select("table tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
                if len(cells) < 9:
                    continue
                day = date_iso(cells[0])
                if not day or day > as_of:
                    continue
                foreign, institutional = num(cells[6]), num(cells[5])
                if foreign is None or institutional is None:
                    continue
                rows.append({"date": day, "foreign": foreign,
                             "institution": institutional, "individual": None})
            if len(rows) >= 65 or len(rows) == previous_count:
                break
        except requests.RequestException:
            break
    return sorted({x["date"]: x for x in rows}.values(), key=lambda x: x["date"])


def fetch_code(code, as_of):
    session = requests.Session()
    best, best_source, errors = [], "", []
    try:
        for url, referer, source in json_sources(code):
            try:
                resp = session.get(url, headers={**HEADERS, "Referer": referer},
                                   timeout=(2.5, 6.0))
                resp.raise_for_status()
                rows = [x for x in extract_rows(resp.json()) if x["date"] <= as_of]
                if len(rows) > len(best):
                    best, best_source = rows, source
                if len(best) >= 20:
                    break
            except (requests.RequestException, ValueError, TypeError) as exc:
                errors.append(type(exc).__name__)
        if len(best) < 20:
            pc = pc_rows(code, session, as_of)
            # PC HTML has only two investor categories, so keep richer JSON if 5D
            # already exists unless PC meaningfully expands the trading window.
            # Ignore an out-of-date public PC HTML snapshot (can lag weeks).
            recent_pc = bool(pc and (not best or
                               (datetime.strptime(best[-1]["date"], "%Y-%m-%d") -
                                datetime.strptime(pc[-1]["date"], "%Y-%m-%d")).days <= 7))
            if len(pc) > len(best) and recent_pc:
                by_day = {r["date"]: r for r in pc}
                for row in best:
                    by_day[row["date"]] = row
                best = sorted(by_day.values(), key=lambda r: r["date"])
                best_source = (best_source + " + " if best_source else "") + "Naver PC investor trend"
    finally:
        session.close()
    best = best[-HISTORY_DAYS:]
    return {"source": best_source or "UNAVAILABLE", "unit": "shares",
            "rows": best, "last_date": best[-1]["date"] if best else None,
            "row_count": len(best), "error": ",".join(errors[-3:]) if not best else None}


def candidates(universe):
    items = [x for x in universe.get("items", [])
             if float(x.get("marketcap") or 0) >= 100_000_000_000
             and float(x.get("turnover") or 0) >= 5_000_000_000]
    items.sort(key=lambda x: (-float(x.get("turnover") or 0),
                              -float(x.get("marketcap") or 0)))
    if len(items) < 50:
        items = sorted((x for x in universe.get("items", [])
                        if float(x.get("marketcap") or 0) >= 100_000_000_000),
                       key=lambda x: (float(x.get("trading_value_rank") or 999999),
                                      float(x.get("volume_rank") or 999999)))[:500]
    codes = [str(x.get("code") or "").zfill(6) for x in items[:MAX_CANDIDATES]]
    try:
        watch = json.loads(WATCHLIST.read_text(encoding="utf-8"))
        for stock in watch.get("stocks", []):
            code = str(stock.get("code") or stock.get("stock_code") or
                       stock.get("ticker") or "").split(".")[0].zfill(6)
            if re.fullmatch(r"\d{6}", code):
                codes.append(code)
    except (ValueError, OSError, AttributeError):
        pass
    return list(dict.fromkeys(c for c in codes if re.fullmatch(r"\d{6}", c)))


def main():
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    as_of = str(universe["as_of"])
    # Investor-classified flows often finalize after the price quote. Exclude current partial session.
    cutoff = ((NOW.date() - timedelta(days=1)).isoformat()
              if as_of == NOW.date().isoformat() and (NOW.hour < 15 or (NOW.hour == 15 and NOW.minute < 45)) else as_of)
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("items") or {}
        except (OSError, ValueError):
            old = {}
    codes = candidates(universe)
    started = time.monotonic()
    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        jobs = {executor.submit(fetch_code, code, cutoff): code for code in codes}
        for job in as_completed(jobs):
            code = jobs[job]
            try:
                result = job.result()
            except Exception as exc:
                result = {"source": "UNAVAILABLE", "unit": "shares", "rows": [],
                          "last_date": None, "row_count": 0, "error": type(exc).__name__}
            if result["rows"] and old.get(code, {}).get("rows"):
                # Merge dates captured on previous days, so a 5-day provider
                # eventually yields a true 20-day record without inventing data.
                historical = {r["date"]: r for r in old[code]["rows"]
                              if r.get("date") and r["date"] <= cutoff}
                historical.update({r["date"]: r for r in result["rows"]})
                combined = sorted(historical.values(), key=lambda r: r["date"])[-HISTORY_DAYS:]
                result["rows"] = combined
                result["row_count"] = len(combined)
                result["last_date"] = combined[-1]["date"]
            if not result["rows"] and old.get(code, {}).get("rows"):
                previous = dict(old[code])
                previous["stale"] = True
                previous["refresh_error"] = result.get("error") or "source unavailable"
                result = previous
            results[code] = result

    ready = sum(1 for item in results.values()
                if item.get("last_date") and item.get("last_date") <= cutoff and not item.get("stale")
                and len(item.get("rows") or []) >= 5)
    ready20 = sum(1 for item in results.values()
                  if item.get("last_date") and item.get("last_date") <= cutoff and not item.get("stale")
                  and len(item.get("rows") or []) >= 20)
    ready60 = sum(1 for item in results.values()
                  if item.get("last_date") and item.get("last_date") <= cutoff and not item.get("stale")
                  and len(item.get("rows") or []) >= 60)
    payload = {
        "meta": {
            "generated_at": NOW.isoformat(), "market_date": as_of,
            "published_cutoff": cutoff,
            "unit": "shares", "source": "Naver public investor trends (KRX)",
            "note": "Daily investor net-buy quantities (shares), never estimated KRW. Missing periods are unavailable.",
            "candidate_count": len(codes), "valid_5d": ready, "valid_20d": ready20,
            "valid_60d": ready60, "history_retention_sessions": HISTORY_DAYS,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        },
        "items": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(OUT)
    print(json.dumps({"date": as_of, "candidates": len(codes),
                      "valid_5d": ready, "valid_20d": ready20, "valid_60d": ready60,
                      "seconds": payload["meta"]["elapsed_seconds"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
