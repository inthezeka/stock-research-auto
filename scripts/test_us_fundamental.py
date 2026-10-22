#!/usr/bin/env python3
"""Deterministic SEC/fundamental tests: no network, no prices invented."""
import json
from pathlib import Path
from us_fundamental import extract_sec_annual,summarize_fundamentals,assess_fundamental
from us_investment import CONFIG,classify_company,classify_themes,aggregate_sector_cycle,aggregate_theme_popularity

assert len(CONFIG["sectors"])==44
assert len(CONFIG["performance_models"])==12
for sec in CONFIG["sectors"]:
    assert sum(sec["score_weights"].values())==100,sec["key"]
assert classify_company({"ticker":"NVDA","gics":"Information Technology","subindustry":"Semiconductors"})["sector_key"]=="US01"
assert classify_company({"ticker":"MU","gics":"Information Technology","subindustry":"Semiconductors"})["sector_key"]=="US02"
assert classify_company({"ticker":"PLTR","gics":"","subindustry":"Application Software"})["sector_key"]=="US12"
assert classify_company({"ticker":"???","gics":"","subindustry":""})["sector_key"] is None
themes=classify_themes("US01")
assert len(themes["primary_themes"])<=3 and all(x["status"]=="NARRATIVE ONLY" for x in themes["primary_themes"])

end="2025-12-31"
def dur(tag,value,prev=None,unit="USD"):
    rows=[
        {"start":"2025-01-01","end":end,"val":value,"form":"10-K","fp":"FY","fy":2025,"filed":"2026-02-20","accn":"a"},
    ]
    if prev is not None:
        rows.append({"start":"2024-01-01","end":"2024-12-31","val":prev,"form":"10-K","fp":"FY","fy":2024,"filed":"2025-02-20","accn":"b"})
    return {tag:{"units":{unit:rows}}}
def inst(tag,value,unit="USD"):
    return {tag:{"units":{unit:[{"end":end,"val":value,"form":"10-K","fy":2025,"filed":"2026-02-20","accn":"a"}]}}}
facts={}
facts.update(dur("RevenueFromContractWithCustomerExcludingAssessedTax",1500,1250))
facts.update(dur("OperatingIncomeLoss",360))
facts.update(dur("NetIncomeLoss",280))
facts.update(dur("GrossProfit",920))
facts.update(dur("EarningsPerShareDiluted",5.4,unit="USD/shares"))
facts.update(dur("NetCashProvidedByUsedInOperatingActivities",420))
facts.update(dur("PaymentsToAcquirePropertyPlantAndEquipment",110))
facts.update(inst("CashAndCashEquivalentsAtCarryingValue",1000))
facts.update(inst("LongTermDebtNoncurrent",200))
facts.update(inst("StockholdersEquity",1100))
facts.update(inst("CommonStockSharesOutstanding",80,unit="shares"))
payload={"cik":123456,"facts":{"us-gaap":facts}}
fin,score=summarize_fundamentals(payload,price=75,sector_key="US01",as_of="2026-10-21")
assert fin["status"]=="EXTRACTED",fin
assert abs(fin["revenue_growth"]-20)<1e-9
assert abs(fin["fcf"]-310)<1e-9
assert fin["accounting_standard"]=="US GAAP"
assert score is not None and score<=75,(score,fin)
info=fin["score_detail"]
assert info["confidence"]=="LOW" and info["evidence_coverage"]>=.5,info
assert info["profile"]=="NO_CONSENSUS_PROFILE"
assert info["effective_weights"]["R"]==0
assert info["effective_weights"]["G"]==30 and info["effective_weights"]["K"]==30
assert sum(info["effective_weights"].values())==100
assert info["components"]["G"]["basis"]=="HISTORICAL_ACTUAL_PROXY"
# trading-score input is never read by fundamental model
injected=dict(fin,trading_score=999,return20d=2000,rsi=100)
assert assess_fundamental(injected,75,"US01",as_of="2026-10-21")[0]==score
# Missing audited evidence must not become synthetic 0 or 100.
empty={"status":"EXTRACTED","revenue":1500,"fiscal_year_end":"2025-12-31"}
missing_score,detail=assess_fundamental(empty,75,"US01",as_of="2026-10-21")
assert missing_score is None and detail["confidence"]=="INSUFFICIENT"
bad=extract_sec_annual({"facts":{"ifrs-full":{"Revenue":{"units":{}}}}})
assert bad["status"]=="IFRS_OR_UNSUPPORTED"
# Sector/Theme scores are withheld when the seven-factor/price-news evidence is missing.
cycles=aggregate_sector_cycle([{"ticker":"NVDA","sector_key":"US01","close":75,"return20d":5.0}])
assert len(cycles)==44 and cycles[0]["cycle_score"] is None
themes=aggregate_theme_popularity([{"ticker":"NVDA","sector_key":"US01","close":75,"return20d":5.0,"primary_themes":classify_themes("US01")["primary_themes"]}])
assert all(x["popularity_score"] is None for x in themes)
print(json.dumps({"ok":True,"sectors":44,"score":score,"coverage":info["evidence_coverage"],"confidence":info["confidence"]}))
