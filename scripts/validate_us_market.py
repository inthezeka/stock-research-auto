#!/usr/bin/env python3
"""US market production QA independent of Korean stock data and scores."""
from __future__ import annotations
import json
import math
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/"data/us-market.json").read_text(encoding="utf-8"))
cfg=json.loads((ROOT/"config/us_investment_sectors.json").read_text(encoding="utf-8"))
sectors=data.get("sectors") or []
required=[s["key"] for s in cfg["sectors"]]
assert len(required)==44 and len(sectors)==44 and [x["key"] for x in sectors]==required, "US must use US-only 44 investment sectors"
assert len(set(required))==44 and all(x.startswith("US") for x in required)
assert len(cfg["performance_models"])==12
assert len(cfg["themes"])>=30
assert all(sum(m.values())==100 and set(m)==set("GQB VKR".replace(" ","")) for m in cfg["performance_models"].values())
assert data["meta"]["currency"]=="USD"
assert data["meta"]["market_timezone"]=="America/New_York"
for s in sectors:
    assert sum(s["score_weights"].values())==100
    assert len(s["kpis"])>0
items=data.get("items") or []
assert isinstance(items,list)
symbols=set();quoted=0;scored=0
for row in items:
    symbol=row.get("ticker")
    assert re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?",str(symbol)),f"invalid ticker {symbol}"
    assert symbol not in symbols,"duplicate ticker "+symbol
    symbols.add(symbol)
    assert row.get("sector_key") in (None,*required), (symbol,row.get("sector_key"))
    assert row.get("sector_classification")=="Internal Sector Classification","GICS mislabel"
    assert set(row.get("indices") or [])<= {"NASDAQ100","SP500","DOW30"}
    assert len(row.get("primary_themes") or [])<=3
    assert len(row.get("secondary_themes") or [])<=5
    for t in row.get("primary_themes") or []:
        assert t.get("status") in ("NARRATIVE ONLY","CONTRACT / PIPELINE","VERIFIED REVENUE")
        if t.get("status")!="NARRATIVE ONLY":
            assert str(t.get("source_url","")).startswith("https://")
    if row.get("close") is not None:
        quoted+=1
        assert 0<float(row["close"])<1e9
        assert row.get("price_date")==data["meta"].get("as_of"),"US stale daily quote marked current: "+symbol
        assert row.get("price_source"),"missing OHLC source for "+symbol
    if row.get("trading_score") is not None:
        assert 0<=row["trading_score"]<=100 and row.get("close") is not None
    score=row.get("fundamental_score")
    if score is not None:
        scored+=1
        assert 0<=score<=100
        assert row.get("fundamental_confidence")!="INSUFFICIENT"
        assert row.get("fundamental_coverage",0)>=.5
        if row.get("fundamental_confidence")=="LOW":assert score<=75
        fi=row.get("financials") or {}
        sd=(fi.get("score_detail") or {})
        assert fi.get("status")=="EXTRACTED" and sd.get("profile")=="NO_CONSENSUS_PROFILE"
        assert sum(sd.get("effective_weights",{}).values())==100
        assert fi.get("source_basis")=="HISTORICAL ACTUAL"
        assert row.get("investment_score") is None,"Investment / Fundamental must not mix"
    assert row.get("fundamental_confidence") in ("HIGH","MEDIUM","LOW","INSUFFICIENT")
    assert row.get("investment_score") is None or row.get("investment_score")>=0
    flow=row.get("flow5d") or {}
    assert "foreign" not in flow and "institution" not in flow
    if flow:assert flow.get("obv_trend") in ("상승","하락","횡보")
assert quoted==data["meta"]["verified_close_count"],"US quote coverage mismatch"
assert scored==data["meta"]["fundamental_scored_count"],"US Fundamental scored count mismatch"
assert data["meta"]["full_us_market_fundamental_rank_verified"] is False,"full market not fully verified"
assert len(data.get("sector_cycles") or [])==44
assert len(data.get("theme_popularity") or [])==len(cfg["themes"])
for cyc in data["sector_cycles"]:
    assert cyc["key"] in required
    if cyc.get("cycle_score") is not None:
        assert cyc.get("evidence_coverage",0)>=.7
for th in data["theme_popularity"]:
    if th.get("popularity_score") is not None:
        assert th.get("evidence_coverage",0)>=.85
assert data["investment_best5"]["status"] in ("INSUFFICIENT","PARTIAL","VERIFIED")
for path in ("markets.html","us.html","scripts/us-market-ui.js"):
    assert (ROOT/path).exists(),path+" missing"
us=(ROOT/"us.html").read_text(encoding="utf-8")
ui=(ROOT/"scripts/us-market-ui.js").read_text(encoding="utf-8")
home=(ROOT/"markets.html").read_text(encoding="utf-8")
assert "scripts/us-market-ui.js" in us
assert "data/us-market.json" in ui
assert "US Stock Research" in us
assert "index.html?market=kr" in home and "us.html" in home
assert "EMBEDDED_FALLBACK_DATA" in (ROOT/"index.html").read_text(encoding="utf-8")

# The first US landing tab must contain the three heavily covered reference indexes.
# Full exchange-wide scanning is clearly marked as partial.
assert 'data-index="INDEX3"' in us and 'data-index="ALL"' in us, "three-index default / optional full market filter absent"
assert 'data-us-mode="fundamental"' in us and 'data-us-mode="trading"' in us, "two-mode US selector missing"
assert 'id="usFundamentalPanel"' in us and 'id="usTradingPanel"' in us, "independent US screening panels absent"
assert "function setUsMode(" in ui and "renderAll();" in ui, "mode switch does not trigger rendering"
assert "selectedIndex='INDEX3'" in ui and "function inIndexScope(" in ui, "3-index default scope absent"
for name in ("fundWatchRows","usUniverseCount","usQuoteCount","usSectorCount","usFundCount",
             "sectorTop3Rows","themeTop3Rows","best5Rows"):
    assert 'id="'+name+'"' in us and "'"+name+"'" in ui, "missing US UI data field: "+name
assert "valid(t.fundamental_score)" in ui and "valid(t.trading_score)" in ui, "score null handling absent"
assert "marketCapUSD(" in ui and 'Nasdaq Screener' in ui, "Nasdaq cap context missing from page"
idx=[r for r in items if r.get("indices")]
if data["meta"].get("source_member_counts",{}).get("SP500",0)>=480 and idx:
    assert len(idx)>=480, "index membership incomplete despite index status"
    assert len([r for r in idx if r.get("close") is not None])>=max(350,int(.7*len(idx))), "3-index default close coverage too low"

print(json.dumps({"status":"OK","stocks":len(items),"dated_quotes":quoted,
                  "fundamental_scores":scored,"sectors":len(sectors),
                  "themes":len(cfg["themes"]),"universe_status":data["meta"]["status"]},ensure_ascii=False))
