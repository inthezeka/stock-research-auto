#!/usr/bin/env python3
import json,re,subprocess,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
errors=[]
warnings=[]

def fail(msg): errors.append(msg)

try:
    data=json.loads((ROOT/"data/market-data.json").read_text(encoding="utf-8"))
except Exception as e:
    raise SystemExit("market-data.json invalid: "+str(e))
try:
    cfg=json.loads((ROOT/"config/watchlist.json").read_text(encoding="utf-8"))
except Exception as e:
    raise SystemExit("watchlist.json invalid: "+str(e))

profiles=data.get("stock_profiles") or []
if len(profiles)!=5:
    fail(f"TOP5 profile count={len(profiles)}")

financial_keys=[]
for i in range(1,5):
    financial_keys += [
        f"y{i}", f"rev{i}", f"op{i}a", f"opm{i}",
        f"ni{i}", f"eps{i}", f"yoy{i}"
    ]

for p in profiles:
    name=p.get("name","?")
    text=p.get("text") or {}
    for k in financial_keys:
        v=str(text.get(k,"")).strip()
        if not v or "[검증 필요]" in v:
            fail(f"{name} financial missing: {k}")
    links=p.get("links") or {}
    for lk in ("disclosure","ir","consensus","news"):
        u=str(links.get(lk,""))
        if not u.startswith("https://"):
            fail(f"{name} invalid {lk} link")
    if not p.get("stock_code"):
        fail(f"{name} missing stock_code")

cards=data.get("theme_cards") or []
if len(cards)!=19:
    fail(f"theme card count={len(cards)}")
for c in cards:
    picks=c.get("picks") or []
    if len(picks)<3:
        fail(f"theme {c.get('code')} has fewer than 3 picks")
    for p in picks:
        state=str(p.get("state",""))
        if "업데이트 대기" in state or "자동" in state:
            fail(f"theme {c.get('code')} has placeholder state")

if len(cfg.get("stocks") or [])<54:
    fail("watchlist stocks fewer than 54")
if len(cfg.get("sectors") or {})!=19:
    fail("watchlist sectors not 19")

meta=data.get("meta") or {}
if meta.get("status")=="ok":
    cov=meta.get("coverage") or {}
    if float(cov.get("stock_fresh_ratio",0))<0.80:
        fail("live stock coverage below 80%")
    if float(cov.get("market_fresh_ratio",0))<0.60:
        fail("live market coverage below 60%")

    # When a live refresh is accepted, every Theme Radar pick must have a price.
    # This prevents a successful deployment with blank cards.
    for c in cards:
        for p in c.get("picks") or []:
            try:
                price=float(p.get("price"))
            except Exception:
                price=0
            if price<=0:
                fail(f"theme {c.get('code')} missing live price: {p.get('name')}")

html=(ROOT/"index.html").read_text(encoding="utf-8")
m=re.search(r"const EMBEDDED_FALLBACK_DATA=(.*?);\n",html,re.S)
if not m:
    fail("embedded fallback missing")
else:
    try:
        embedded=json.loads(m.group(1))
        if embedded!=data:
            fail("embedded fallback differs from market-data.json")
    except Exception as e:
        fail("embedded fallback invalid: "+str(e))

# Validate inline JavaScript syntax when Node is available.
scripts=re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>",html,re.S|re.I)
js="\n".join(scripts)
try:
    with tempfile.NamedTemporaryFile("w",suffix=".js",encoding="utf-8",delete=False) as f:
        f.write(js); tmp=f.name
    r=subprocess.run(["node","--check",tmp],capture_output=True,text=True)
    if r.returncode!=0:
        fail("JavaScript syntax: "+(r.stderr.strip()[:500] or "failed"))
except FileNotFoundError:
    warnings.append("node unavailable; JS syntax check skipped")

report=[
    "# QA REPORT",
    "",
    f"- passed: **{not errors}**",
    f"- errors: **{len(errors)}**",
    f"- warnings: **{len(warnings)}**",
    f"- TOP5: **{len(profiles)}**",
    f"- themes: **{len(cards)}**",
    f"- watchlist stocks: **{len(cfg.get('stocks') or [])}**",
]
if errors:
    report+=["","## Errors"]+[f"- {x}" for x in errors]
if warnings:
    report+=["","## Warnings"]+[f"- {x}" for x in warnings]
(ROOT/"QA_REPORT.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report))
if errors:
    raise SystemExit(1)
