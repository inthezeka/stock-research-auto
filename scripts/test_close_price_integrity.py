#!/usr/bin/env python3
"""Offline regression tests: discovery quotes cannot masquerade as dated closes."""
from datetime import datetime, date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import update_data as stock
import update_sector_closes as sector

DAY = "2026-10-29"


def candle_rows(price):
    end = date.fromisoformat(DAY)
    return [
        {"date": (end - timedelta(days=i)).isoformat(), "open": float(price),
         "high": float(price) + 100, "low": float(price) - 100,
         "close": float(price), "volume": 1000}
        for i in range(75, -1, -1)
    ]


with TemporaryDirectory() as work:
    root = Path(work)
    (root / "data").mkdir()
    prior_root, prior_universe = stock.ROOT, stock.UNIVERSE_PATH
    try:
        stock.ROOT = root
        stock.UNIVERSE_PATH = root / "stock-universe.json"
        universe = {
            "as_of": DAY, "generated_at": "2026-10-30T07:37:02+09:00",
            "count": 4, "items": [
                {"code": "000660", "name": "SK하이닉스", "close": 1526000, "shares": 100},
                {"code": "042700", "name": "한미반도체", "close": 194600, "shares": 100},
                {"code": "222800", "name": "심텍", "close": 123300, "shares": 100},
                {"code": "999999", "name": "테스트", "close": 123, "shares": 100},
            ],
        }
        live = {"000660": {
            "tech": {"price": 1526000, "date": DAY},
            "source": "Yahoo Finance (dated OHLC)",
            "close_cross_check": "MATCH", "source_disagreement_count": 0,
            "rows": candle_rows(1526000),
        }}
        stock.sync_watchlist_universe_prices(universe, live)
        assert universe["verified_close_count"] == 1
        assert universe["unverified_close_count"] == 3
        assert universe["close_schema_version"] == 2
        lookup = {x["code"]: x for x in universe["items"]}
        assert lookup["000660"]["close"] == 1526000
        for code, original_quote in (("042700", 194600), ("222800", 123300)):
            assert lookup[code]["close"] is None
            assert lookup[code]["close_status"] == "UNVERIFIED"
            assert lookup[code]["reference_close"] == original_quote
            assert not lookup[code].get("close_date")
        assert (root / "data" / "verified-stock-bars.json").is_file()

        quotes = {}
        for code, listing, final in (("042700", 194600, 196900), ("222800", 123300, 123400)):
            item = lookup[code]
            quote = sector.normalize_close(item, candle_rows(final),
                                           "Yahoo Finance daily OHLC", DAY)
            assert quote["reference_agrees"] is False
            assert quote["listing_reference_close"] == listing
            quotes[code] = quote
        abnormal = candle_rows(196900)
        abnormal[-1]["volume"] = 0
        try:
            sector.normalize_close(lookup["042700"], abnormal, "Yahoo", DAY)
            raise AssertionError("zero-volume candle unexpectedly accepted")
        except ValueError:
            pass
        abnormal[-1]["volume"] = 100
        abnormal[-1]["close"] = 196900.32
        try:
            sector.normalize_close(lookup["042700"], abnormal, "Yahoo", DAY)
            raise AssertionError("fractional off-tick candle unexpectedly accepted")
        except ValueError:
            pass

        promoted = sector.sync_confirmed_sector_closes(universe, quotes, DAY)
        assert promoted == 2
        assert universe["dated_close_count"] == 3
        assert universe["unverified_close_count"] == 1
        assert universe["listing_reference_disagreements"] == 2
        for code, final in (("042700", 196900), ("222800", 123400)):
            assert lookup[code]["close"] == final
            assert lookup[code]["close_date"] == DAY
            assert lookup[code]["close_status"] == "DAILY_CLOSE"
            assert lookup[code]["reference_agrees"] is False

        selection = {"meta": {"theme_selection_as_of": "2026-10-16"},
                     "theme_picks": [{"ticker": "000660.KS", "price": 1200000}]}
        stock.sync_legacy_theme_picks(selection, live)
        pick = selection["theme_picks"][0]
        assert pick["selection_price"] == 1200000
        assert pick["selection_price_date"] == "2026-10-16"
        assert pick["price"] == 1526000 and pick["price_date"] == DAY
    finally:
        stock.ROOT, stock.UNIVERSE_PATH = prior_root, prior_universe

# Both providers must agree throughout shared historical bars, not only today.
prior_naver, prior_yahoo, prior_tech, prior_now = (
    stock.naver_stock_chart, stock.yahoo_chart, stock.technicals, stock.NOW
)
try:
    naver_rows = candle_rows(1277000)
    yahoo_rows = candle_rows(1277000)
    naver_rows[-11]["close"] = 1276999
    stock.naver_stock_chart = lambda code: (naver_rows, "Naver Stock")
    stock.yahoo_chart = lambda symbol: (yahoo_rows, "Yahoo Finance")
    stock.technicals = lambda rows: {"price": rows[-1]["close"], "date": rows[-1]["date"]}
    stock.NOW = datetime(2026, 10, 30, 8, 0)
    v = stock.fetch_stock({"stock_code": "000660", "ticker": "000660.KS"})
    assert v["close_cross_check"] == "CONFLICT"
    assert v["source_disagreement_count"] == 1
    assert v["rows"] == yahoo_rows
    assert v["source"].startswith("Yahoo Finance")
finally:
    stock.naver_stock_chart, stock.yahoo_chart, stock.technicals, stock.NOW = (
        prior_naver, prior_yahoo, prior_tech, prior_now
    )

print("close-price integrity offline regressions passed")
