#!/usr/bin/env python3
"""Independent non-mutating validation for US market page and dated snapshot."""
import json
import re
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
d=json.loads((ROOT/"data/us-market.json").read_text(encoding="utf-8"))
sector_cfg=json.loads((ROOT/"config/fundamental_themes.json").read_text(encoding="utf-8"))
sectors=d.get("sectors") or []
expected=[x["key"] for x in sector_cfg["sectors"]]
assert len(sectors)==36 and [s["key"] for s in sectors]==expected,"US/KR taxonomy differs"
assert d["meta"]["currency"]=="USD"
assert d["meta"]["market_timezone"]=="America/New_York"
items=d.get("items") or []
assert isinstance(items,list)
symbols=set()
for t in items:
    tick=str(t.get("ticker") or "")
    assert re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?",tick),f"bad symbol {tick}"
    assert tick not in symbols,f"duplicate {tick}"
    symbols.add(tick)
    assert t.get("sector_key") in expected,(tick,t.get("sector_key"))
    assert set(t.get("indices") or []) <= {"NASDAQ100","SP500","DOW30"}
    close=t.get("close")
    if close is not None:
        assert isinstance(close,(int,float)) and 0<close<1e8,(tick,close)
        assert t.get("price_date")==d["meta"].get("as_of"),"stale quote incorrectly shown for "+tick
        assert t.get("price_source"),f"missing quote source for {tick}"
    if t.get("fundamental_score") is not None:
        score=t["fundamental_score"]
        assert 0<=score<=100
        assert (t.get("financials") or {}).get("scoring_status")=="verified","unverified US financial score"
    if t.get("trading_score") is not None:
        assert 0<=t["trading_score"]<=100
        assert close is not None, "trading score lacks verified close"
    if t.get("flow5d"):
        assert t["flow5d"].get("obv_trend") in ("상승","하락","횡보")
        assert "foreign" not in t["flow5d"] and "institution" not in t["flow5d"]
assert sum(t.get("close") is not None for t in items)==d["meta"].get("verified_close_count"),"coverage count mismatch"
us=(ROOT/"us.html").read_text(encoding="utf-8")
portal=(ROOT/"markets.html").read_text(encoding="utf-8")
kr=(ROOT/"index.html").read_text(encoding="utf-8")
ui=(ROOT/"scripts/us-market-ui.js").read_text(encoding="utf-8")
assert '<script src="./scripts/us-market-ui.js' in us
assert "data/us-market.json" in ui
assert "US Stock Research" in us
assert "MY Stock Room" in portal
assert "index.html?market=kr" in portal and "us.html" in portal
assert "EMBEDDED_FALLBACK_DATA" in kr
assert not re.search(r"(?:0|00)\.[0-9]+[Ee]\+?0*0",json.dumps(items)), "unexpected numeric notation"
print(json.dumps({"status":"OK","US_status":d["meta"].get("status"),"stocks":len(items),
                  "verified_closes":d["meta"].get("verified_close_count"),"sectors":len(sectors)},ensure_ascii=False))
