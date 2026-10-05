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

print("smoke test PASS")
