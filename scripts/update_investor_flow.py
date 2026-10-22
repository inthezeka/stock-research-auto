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

from kis_investor_flow import KisInvestorClient, SOURCE as KIS_SOURCE
from investor_flow_csv import read_backfill_csv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investor-flow.json"
UNIVERSE = ROOT / "stock-universe.json"
WATCHLIST = ROOT / "config" / "watchlist.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
WORKERS = min(24, max(1, int(os.getenv("FLOW_WORKERS", "18"))))
MAX_CANDIDATES = min(800, max(50, int(os.getenv("FLOW_MAX_CANDIDATES", "500"))))
HISTORY_DAYS = 90  # historical backfill modules are retained but disabled
FLOW_ACTIVE_DAYS = 5  # public site only collects, stores and displays 5 trading days
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
        (f"https://api.stock.naver.com/api/domestic/detail/{code}/trend?tradeType=KRX&startIdx=0&pageSize=5",
         f"https://stock.naver.com/domestic/stock/{code}/trend", "Naver stock investor trend"),
        (f"https://m.stock.naver.com/api/stock/{code}/integration",
         f"https://m.stock.naver.com/domestic/stock/{code}", "Naver integration investor trend"),
    ]


def pc_rows(code, session, as_of):
    rows = []
    for page in range(1, 3):
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
            if len(rows) >= FLOW_ACTIVE_DAYS or len(rows) == previous_count:
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
                if len(best) >= FLOW_ACTIVE_DAYS:
                    break
            except (requests.RequestException, ValueError, TypeError) as exc:
                errors.append(type(exc).__name__)
        if len(best) < FLOW_ACTIVE_DAYS:
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
    best = best[-FLOW_ACTIVE_DAYS:]
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



def merge_verified_source(existing, previous, incoming, source, cutoff):
    """Never mix investor-category market definitions within one dated series.

    KIS is a consistent KRX source. If the previous series was also KIS,
    merge the two KRX histories. Do not merge anonymous Naver rows with KRX.
    """
    combined = {}
    if str((previous or {}).get("source") or "").startswith("KIS investor"):
        combined.update({r["date"]: r for r in previous.get("rows", [])
                         if r.get("date") and r["date"] <= cutoff})
    combined.update({r["date"]: r for r in incoming})
    series = sorted(combined.values(), key=lambda x: x["date"])[-HISTORY_DAYS:]
    if not series:
        return existing
    old_last = str((existing or {}).get("last_date") or "")
    if old_last and series[-1]["date"] < old_last:
        # Show last confirmed official source, but only if at most a week
        # behind the live Naver display.
        try:
            lag = (datetime.strptime(old_last, "%Y-%m-%d") -
                   datetime.strptime(series[-1]["date"], "%Y-%m-%d")).days
        except ValueError:
            lag = 999
        if lag > 7:
            return existing
    return {
        "source": source,
        "unit": "shares",
        "market_scope": "KRX",
        "rows": series,
        "last_date": series[-1]["date"],
        "row_count": len(series),
        "error": None,
        "stale": False,
    }


def optional_kis_backfill(codes, results, old, cutoff):
    """Credentials-only official backfill; never block market-data deployment."""
    if str(os.getenv("KIS_FLOW_ENABLED", "1")).lower() in ("0", "false", "no"):
        return {"status": "disabled_on_push", "requested": 0, "covered_20d": 0,
                "covered_60d": 0, "errors": 0, "source": KIS_SOURCE}
    client = KisInvestorClient()
    report = {"status": "credentials_missing", "requested": 0, "covered_20d": 0,
              "covered_60d": 0, "source": KIS_SOURCE, "errors": 0}
    if not client.configured:
        # Keep recent previously fetched KIS history when credentials have
        # temporarily disappeared, instead of silently losing 60D history.
        for code in codes:
            cached = old.get(code)
            if not cached or not str(cached.get("source", "")).startswith("KIS investor"):
                continue
            rows = cached.get("rows") or []
            if rows:
                results[code] = merge_verified_source(results.get(code), cached, [], KIS_SOURCE, cutoff)
        return report
    if not client.authenticate():
        report["status"] = client.error or "authentication_failed"
        return report
    report["status"] = "authenticated"
    # Three regression-pilot tickers first; remaining symbols by trading value.
    front = ["005930", "000660", "356860"]
    priority = list(dict.fromkeys([c for c in front if c in results] + list(codes)))
    limit = min(500, max(3, int(os.getenv("KIS_FLOW_MAX_CODES", "320"))))
    for code in priority[:limit]:
        previous = old.get(code) or {}
        old_rows = previous.get("rows") or []
        if (str(previous.get("source", "")).startswith("KIS investor") and
                len(old_rows) >= 65 and previous.get("last_date") == cutoff):
            results[code] = previous
            report["covered_20d"] += 1
            report["covered_60d"] += 1
            continue
        try:
            need = 20 if len(old_rows) >= 60 and str(previous.get("source", "")).startswith("KIS investor") else 65
            history = client.fetch_history(code, cutoff, target_rows=need)
            report["requested"] += 1
            if history and len(history) >= 5:
                results[code] = merge_verified_source(results.get(code), previous, history, KIS_SOURCE, cutoff)
                n = len(results[code]["rows"])
                report["covered_20d"] += int(n >= 20)
                report["covered_60d"] += int(n >= 60)
            else:
                report["errors"] += 1
            if report["requested"] >= 10 and report["errors"] / report["requested"] >= 0.8:
                report["status"] = "source_unavailable"
                break
        except (requests.RequestException, ValueError, TypeError, KeyError):
            report["requested"] += 1
            report["errors"] += 1
            if report["requested"] >= 10 and report["errors"] / report["requested"] >= 0.8:
                report["status"] = "source_unavailable"
                break
    if report["status"] == "authenticated":
        report["status"] = "collected" if report["covered_20d"] or report["covered_60d"] else "empty_history"
    return report


def optional_csv_backfill(codes, results, cutoff):
    """Offline KRX route requiring actual signed share quantities from a user file."""
    import_path = ROOT / "data" / "investor-flow-backfill.csv"
    report = {"status": "not_provided", "imported": 0, "covered_20d": 0, "covered_60d": 0}
    if not import_path.is_file():
        return report
    try:
        lookup = read_backfill_csv(import_path, cutoff, HISTORY_DAYS)
    except (ValueError, UnicodeError, OSError) as exc:
        report["status"] = "invalid_csv"
        report["reason"] = type(exc).__name__
        return report
    for code in codes:
        rows = lookup.get(code) or []
        if len(rows) < 20:
            continue
        current = results.get(code) or {}
        if current.get("source", "").startswith("KIS investor") and len(current.get("rows") or []) >= len(rows):
            continue
        current_last = str(current.get("last_date") or "")
        if current_last:
            try:
                gap = (datetime.strptime(current_last, "%Y-%m-%d") -
                       datetime.strptime(rows[-1]["date"], "%Y-%m-%d")).days
                if gap > 7:
                    continue
            except ValueError:
                continue
        results[code] = {
            "source": "KRX user-authorized CSV import",
            "unit": "shares",
            "market_scope": "KRX",
            "rows": rows,
            "last_date": rows[-1]["date"],
            "row_count": len(rows),
            "error": None,
            "stale": False,
        }
        report["imported"] += 1
        report["covered_20d"] += int(len(rows) >= 20)
        report["covered_60d"] += int(len(rows) >= 60)
    report["status"] = "imported" if report["imported"] else "no_valid_20d_rows"
    return report


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
                historical_item = old[code]
                historical_source = str(historical_item.get("source") or "")
                if historical_source.startswith(("KIS investor", "KRX user-authorized")):
                    # Never mix public Naver day rows with verified KRX/KIS rows.
                    # Prefer the verified history while it is still within one
                    # calendar week; show its actual as-of date on the site.
                    verified_last = str(historical_item.get("last_date") or "")
                    try:
                        age_days = (datetime.strptime(cutoff, "%Y-%m-%d") -
                                    datetime.strptime(verified_last, "%Y-%m-%d")).days
                    except ValueError:
                        age_days = 999
                    if 0 <= age_days <= 7:
                        result = dict(historical_item)
                        result["stale"] = False
                else:
                    # Merge only same Naver source family to build rolling history.
                    historical = {r["date"]: r for r in historical_item["rows"]
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

    # The public dashboard is currently 5D only. Keep long-range KIS/CSV
    # adapters dormant for future use; do not request authenticated or
    # historical backfill data, even if GitHub repository secrets exist.
    kis_report = {"status": "disabled_5d_only", "requested": 0,
                  "covered_20d": 0, "covered_60d": 0, "errors": 0}
    csv_report = {"status": "disabled_5d_only", "imported": 0,
                  "covered_20d": 0, "covered_60d": 0}
    for result in results.values():
        rows = (result.get("rows") or [])[-FLOW_ACTIVE_DAYS:]
        result["rows"] = rows
        result["row_count"] = len(rows)
        result["last_date"] = rows[-1]["date"] if rows else None

    ready = sum(1 for item in results.values()
                if item.get("last_date") and item.get("last_date") <= cutoff and not item.get("stale")
                and len(item.get("rows") or []) >= 5)
    payload = {
        "meta": {
            "generated_at": NOW.isoformat(), "market_date": as_of,
            "published_cutoff": cutoff,
            "unit": "shares", "source": "Naver public investor trends (5D)",
            "note": "Daily investor net-buy quantities (shares), never estimated KRW. Missing periods are unavailable.",
            "candidate_count": len(codes), "valid_5d": ready,
            "active_history_sessions": FLOW_ACTIVE_DAYS,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "kis_backfill": kis_report,
            "krx_csv_backfill": csv_report,
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
                      "valid_5d": ready,
                      "seconds": payload["meta"]["elapsed_seconds"],
                      "history": FLOW_ACTIVE_DAYS, "backfill": "disabled_5d_only"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
