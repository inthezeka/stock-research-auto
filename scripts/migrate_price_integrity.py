#!/usr/bin/env python3
"""Migrate historical sector OHLC snapshots to safe, date-backed records.

Quarantine zero-volume candles and impossible fractional closes before
publishing. The listing quote remains visible only as an UNVERIFIED reference.
Idempotent so it is safe in both push and scheduled workflows.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SP = ROOT / "data/sector-closes.json"
UP = ROOT / "stock-universe.json"
MP = ROOT / "data/market-data.json"


def write_json(path, obj, pretty=False):
    tmp = path.with_suffix(path.suffix + ".tmp")
    options = {"ensure_ascii": False}
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    tmp.write_text(json.dumps(obj, **options) + ("\n" if pretty else ""),
                   encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(path)


def main():
    sector = json.loads(SP.read_text(encoding="utf-8"))
    universe = json.loads(UP.read_text(encoding="utf-8"))
    market = json.loads(MP.read_text(encoding="utf-8"))
    asof = market["meta"]["market_date"]
    if asof != universe.get("as_of") or asof != sector.get("as_of"):
        raise RuntimeError("refusing to migrate different trading dates")
    by_code = {x["code"]: x for x in universe["items"]}
    quarantined = []
    for code, quote in list((sector.get("items") or {}).items()):
        if quote.get("status") != "DAILY_CLOSE":
            continue
        rows = quote.get("rows") or []
        bar = rows[-1] if rows else {}
        raw = float(bar.get("close") or 0)
        volume = float(bar.get("volume") or 0)
        if len(rows) >= 65 and bar.get("date") == asof and (
                volume > 0 and abs(raw - round(raw)) <= 0.05
                and round(raw) == round(float(quote.get("close") or 0))):
            continue
        item = by_code.get(code)
        if not item:
            raise RuntimeError(f"quarantined code missing from universe: {code}")
        if item.get("close_status") == "DAILY_CLOSE":
            if item.get("close") != quote.get("close"):
                raise RuntimeError(f"sector/universe mismatch: {code}")
            if "reference_close" not in item:
                raise RuntimeError(f"listing reference missing: {code}")
            item["close"] = None
            item.pop("close_date", None)
            item["close_status"] = "UNVERIFIED"
            item["close_source"] = "Naver listing quote (unverified)"
            item["close_cross_check"] = "NOT_CHECKED"
            item["marketcap"] = None
            item.pop("reference_agrees", None)
        elif item.get("close_status") != "UNVERIFIED":
            raise RuntimeError(f"cannot quarantine verified watchlist: {code}")
        del sector["items"][code]
        sector.setdefault("errors", {})[code] = (
            "Rejected non-traded, fractional, stale or short-history daily candle; "
            "reference quote is not an official closing price"
        )
        quarantined.append(code)
    sector["confirmed"] = len(sector.get("items") or {})
    sector["unavailable"] = len(sector.get("errors") or {})
    sector["reference_disagreements"] = sum(
        x.get("reference_agrees") is False for x in sector["items"].values()
    )
    if sector["confirmed"] + sector["unavailable"] != sector["requested"]:
        raise RuntimeError("sector coverage not conserved")
    statuses = {key: sum(x.get("close_status") == key for x in universe["items"])
                for key in ("VERIFIED", "DAILY_CLOSE", "UNVERIFIED")}
    universe["verified_close_count"] = statuses["VERIFIED"]
    universe["daily_close_count"] = statuses["DAILY_CLOSE"]
    universe["dated_close_count"] = statuses["VERIFIED"] + statuses["DAILY_CLOSE"]
    universe["unverified_close_count"] = statuses["UNVERIFIED"]
    universe["listing_reference_disagreements"] = sector["reference_disagreements"]
    universe["close_schema_version"] = 2
    universe["sector_closes_synced"] = True
    if sum(statuses.values()) != universe["count"]:
        raise RuntimeError("universe total mismatch")
    coverage = market["meta"].setdefault("coverage", {})
    coverage.update({
        "sector_requested_count": sector["requested"],
        "sector_confirmed_count": sector["confirmed"],
        "sector_unavailable_count": sector["unavailable"],
        "sector_success_rate": round(sector["confirmed"] / max(1, sector["requested"]), 6),
        "universe_verified_close_count": statuses["VERIFIED"],
        "universe_dated_close_count": universe["dated_close_count"],
        "universe_unverified_close_count": statuses["UNVERIFIED"],
        "universe_dated_close_ratio": round(universe["dated_close_count"] / max(1, universe["count"]), 6),
        "sector_daily_close_count": statuses["DAILY_CLOSE"],
        "listing_reference_disagreements": sector["reference_disagreements"],
    })
    write_json(SP, sector)
    write_json(UP, universe)
    write_json(MP, market, pretty=True)
    print(json.dumps({"quarantined": quarantined, "sector_confirmed": sector["confirmed"],
                      "sector_unavailable": sector["unavailable"],
                      "universe_dated": universe["dated_close_count"],
                      "universe_unverified": universe["unverified_close_count"]},
                     ensure_ascii=False))
    if sector["confirmed"] / max(1, sector["requested"]) < 0.95:
        raise RuntimeError("sector dated coverage below 95%")


if __name__ == "__main__":
    main()
