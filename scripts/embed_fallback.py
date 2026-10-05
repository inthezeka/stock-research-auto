#!/usr/bin/env python3
import json, re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
html_path=ROOT/"index.html"
data_path=ROOT/"data/market-data.json"

data=json.loads(data_path.read_text(encoding="utf-8"))
payload=json.dumps(data,ensure_ascii=False,separators=(",",":"))
html=html_path.read_text(encoding="utf-8")

pat=r"const EMBEDDED_FALLBACK_DATA=.*?;\n"
replacement="const EMBEDDED_FALLBACK_DATA="+payload+";\n"
new,count=re.subn(pat,replacement,html,count=1,flags=re.S)
if count!=1:
    raise SystemExit("EMBEDDED_FALLBACK_DATA marker not found exactly once")
html_path.write_text(new,encoding="utf-8")
print("embedded fallback synchronized")
