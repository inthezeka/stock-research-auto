#!/usr/bin/env python3
"""Fail deployment if the published GitHub Pages prices differ from the commit.

This runs *after* actions/deploy-pages, not merely after generating local JSON.
It validates live published JSON, the SK hynix historical anchor and a real
headless-browser render of the current-price field.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("PUBLISHED_SITE_URL", "https://inthezeka.github.io/stock-research-auto/").rstrip("/") + "/"
MARKET = json.loads((ROOT / "data/market-data.json").read_text(encoding="utf-8"))
UNIVERSE = json.loads((ROOT / "stock-universe.json").read_text(encoding="utf-8"))
HISTORY = json.loads((ROOT / "data/verified-stock-bars.json").read_text(encoding="utf-8"))
SECTORS = json.loads((ROOT / "data/sector-closes.json").read_text(encoding="utf-8"))
DAY = MARKET["meta"]["market_date"]
GENERATED = MARKET["meta"]["generated_at"]


def get(path):
    url = BASE + path + "?" + urlencode({"audit": GENERATED})
    request = Request(url, headers={"User-Agent": "StockRoom-PriceIntegrity/1.0", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError(f"{path}: HTTP {response.status}")
        return response.read().decode("utf-8")


def get_json(path):
    return json.loads(get(path))


def by_code(payload):
    return {record["code"]: record for record in payload.get("items") or []}


def historical_anchor(payload):
    rows = ((payload.get("items") or {}).get("000660") or {}).get("rows") or []
    bars = [r for r in rows if r.get("date") == "2026-10-19"]
    if len(bars) != 1 or round(bars[0].get("close") or 0) != 1277000:
        raise AssertionError("SK hynix 2026-10-19 OHLC close != 1,277,000")


def check_snapshot(market, universe, history, sector, index):
    if market.get("meta", {}).get("generated_at") != GENERATED:
        raise AssertionError("Published market-data.json is from a different run")
    if universe.get("generated_at") != UNIVERSE.get("generated_at"):
        raise AssertionError("Published universe snapshot is stale")
    if universe.get("as_of") != DAY or sector.get("as_of") != DAY or history.get("as_of") != DAY:
        raise AssertionError("Published market/universe/sector/history trading dates do not agree")
    if universe.get("count") != UNIVERSE.get("count"):
        raise AssertionError("Published universe count differs from GitHub")
    expected = by_code(UNIVERSE)
    shown = by_code(universe)
    for code, local in expected.items():
        online = shown.get(code) or {}
        fields = ("close", "close_date", "close_status", "reference_close")
        for field in fields:
            if online.get(field) != local.get(field):
                raise AssertionError(f"{code} {field}: displayed JSON differs from repository")
        if local.get("close_status") == "UNVERIFIED" and (online.get("close") is not None or online.get("close_date")):
            raise AssertionError(f"{code} undated listing quote misrepresented as official close")
    if "const STOCK_UNIVERSE_URL='stock-universe.json'" not in index:
        raise AssertionError("Public site uses inconsistent cross-origin universe")
    if 'data-edit data-key="currentPriceText">시세 확인 중' not in index:
        raise AssertionError("Public site still has stale hardcoded current price")
    expected_covers = MARKET.get("meta", {}).get("coverage") or {}
    online_covers = market.get("meta", {}).get("coverage") or {}
    if online_covers != expected_covers:
        raise AssertionError("Published collection coverage differs from repository")
    for pick in market.get("theme_picks") or []:
        code = str(pick.get("ticker") or "").split(".")[0]
        row = shown.get(code) or {}
        if row.get("close_status") == "VERIFIED":
            if pick.get("price") != row.get("close") or pick.get("price_date") != DAY:
                raise AssertionError(f"{code} month theme pick current price is outdated")
    historical_anchor(HISTORY)
    historical_anchor(history)
    for code in ("000660", "005930", "009150", "010120", "035420"):
        if (shown.get(code) or {}).get("close_status") != "VERIFIED":
            raise AssertionError(f"{code}: missing dated watchlist close")
    for code in ("042700", "222800"):
        q = (sector.get("items") or {}).get(code) or {}
        row = shown.get(code) or {}
        if q.get("date") != DAY or q.get("status") not in ("DAILY_CLOSE", "VERIFIED"):
            raise AssertionError(f"{code}: no dated sector OHLC close")
        if row.get("close_status") == "UNVERIFIED" and row.get("reference_close") is None:
            raise AssertionError(f"{code}: reference price missing")
    return len(expected)


def check_browser():
    chrome = next((shutil.which(name) for name in (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(name)), None)
    if not chrome:
        raise RuntimeError("Chrome headless unavailable: rendered UI verification cannot be claimed")
    proc = subprocess.run(
        [chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage", "--ignore-certificate-errors",
         "--disable-features=HttpsFirstBalancedModeAutoEnable",
         "--hide-scrollbars", "--virtual-time-budget=15000",
         "--dump-dom", BASE + "index.html?" + urlencode({"market": "KOSPI", "audit": GENERATED})],
        capture_output=True, text=True, timeout=70,
    )
    if proc.returncode != 0:
        raise RuntimeError("Headless Chrome failed: " + proc.stderr[-500:])
    matches = re.findall(r'data-key=["\x27]currentPriceText["\x27][^>]*>([^<]*)', proc.stdout)
    if not matches:
        snippet = re.sub(r"\\s+", " ", proc.stdout[:1800])
        raise AssertionError(
            "Current-price field missing in live DOM; "
            + "DOM length=" + str(len(proc.stdout))
            + "; has_data_key=" + str("data-key" in proc.stdout)
            + "; head=" + snippet[:1000]
            + "; chrome_stderr=" + proc.stderr[-350:]
        )
    value = re.sub(r"\s+", "", matches[-1])
    # The live UI can select a different profile from the static summary text.
    # Compare against actual, dated closes, not the fallback top-pick placeholder.
    verified_labels = {
        f"{int(round(item['close'])):,}원"
        for item in UNIVERSE["items"]
        if item.get("close_status") in ("VERIFIED", "DAILY_CLOSE")
        and item.get("close_date") == DAY
        and isinstance(item.get("close"), (int, float)) and item["close"] > 0
    }
    if value not in verified_labels:
        raise AssertionError(f"Live DOM current price is not a dated published close: {value!r}")
    return value


def main():
    last = None
    for attempt in range(1, 5):
        try:
            market = get_json("data/market-data.json")
            universe = get_json("stock-universe.json")
            sector = get_json("data/sector-closes.json")
            history = get_json("data/verified-stock-bars.json")
            index = get("index.html")
            n = check_snapshot(market, universe, history, sector, index)
            rendered = check_browser()
            print(json.dumps({
                "status": "PUBLISHED_DATA_AND_DOM_MATCH",
                "url": BASE, "market_date": DAY, "generated_at": GENERATED,
                "universe_count": n, "watchlist_verified": universe.get("verified_close_count"),
                "universe_dated_close_count": universe.get("dated_close_count"),
                "rendered_current_price": rendered,
                "reference_price_conflicts": sector.get("reference_disagreements"),
            }, ensure_ascii=False))
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                    f.write(f"\n### Published data and DOM verification\n- Site: {BASE}\n")
                    f.write(f"- Market date: {DAY}; generated: {GENERATED}\n")
                    f.write(f"- Verified records: {universe.get('verified_close_count')}\n")
                    f.write(f"- Dated OHLC records: {universe.get('dated_close_count')}\n")
                    f.write(f"- Rendered current price: {rendered}\n")
                    f.write(f"- Listing reference differences (not verified closes): {sector.get('reference_disagreements')}\n")
            return 0
        except Exception as exc:
            last = exc
            print(f"Published audit attempt {attempt}/4 failed: {exc}", flush=True)
            if attempt != 4:
                time.sleep(10)
    print("PUBLICATION NOT VERIFIED: " + str(last), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
