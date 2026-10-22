#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("upd",ROOT/"scripts/update_data.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

rows=[]
for i in range(100):
    c=100+i*1.25
    rows.append({"date":f"2026-01-{(i%28)+1:02d}","close":c,"high":c+2,"low":c-2,"volume":1000+i})

t=m.technicals(rows)
required=["price","r20","r60","rsi","stoch_k","stoch_d","macd","macd_signal","m5","m20","m60","support","resistance"]
missing=[k for k in required if t.get(k) is None]
if missing:
    raise SystemExit("technical smoke test failed: "+",".join(missing))

cfg=m.CFG
if len(cfg.get("stocks",[]))<54:
    raise SystemExit("watchlist has fewer than 54 stocks")
if len(cfg.get("sectors",{}))!=19:
    raise SystemExit("theme count is not 19")


# Investor-flow parsing checks: legitimate signed quantities and null handling.
flow_spec=importlib.util.spec_from_file_location("flow",ROOT/"scripts/update_investor_flow.py")
flow=importlib.util.module_from_spec(flow_spec)
flow_spec.loader.exec_module(flow)
fixture={"dealTrendInfos":[
    {"bizdate":"20261021","foreignerPureBuyQuant":"-1,240",
     "organPureBuyQuant":"400","individualPureBuyQuant":"840"},
    {"bizdate":"20261020","foreignerPureBuyQuant":"50",
     "organPureBuyQuant":"-100","individualPureBuyQuant":"50"},
]}
parsed=flow.extract_rows(fixture)
if len(parsed)!=2 or parsed[-1]["foreign"]!=-1240 or parsed[-1]["institution"]!=400:
    raise SystemExit("investor-flow numeric parsing failed")
if parsed[0]["date"]!="2026-10-20" or flow.num("—") is not None:
    raise SystemExit("investor-flow date/null parsing failed")
if flow.extract_rows({"dealTrendInfos":[{"bizdate":"20261021","foreignBuyValue":100}] }):
    raise SystemExit("investor-flow KRW/value must not be mistaken for share quantity")
print("investor flow smoke PASS")

print("smoke test PASS")
