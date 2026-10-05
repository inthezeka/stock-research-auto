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

groups=data.get("stock_profile_groups") or {}
kospi_profiles=groups.get("KOSPI") or data.get("stock_profiles") or []
kosdaq_profiles=groups.get("KOSDAQ") or data.get("stock_profiles_kosdaq") or []
profiles=kospi_profiles
if len(kospi_profiles)!=5:
    fail(f"KOSPI TOP5 profile count={len(kospi_profiles)}")
if len(kosdaq_profiles)!=5:
    fail(f"KOSDAQ TOP5 profile count={len(kosdaq_profiles)}")
all_profiles=kospi_profiles+kosdaq_profiles

financial_keys=[]
for i in range(1,5):
    financial_keys += [
        f"y{i}", f"rev{i}", f"op{i}a", f"opm{i}",
        f"ni{i}", f"eps{i}", f"yoy{i}"
    ]

for p in all_profiles:
    name=p.get("name","?")
    text=p.get("text") or {}
    for k in financial_keys:
        v=str(text.get(k,"")).strip()
        if not v or "[검증 필요]" in v:
            fail(f"{name} financial missing: {k}")

    if p.get("financial_basis")!="quarterly":
        fail(f"{name} financial basis is not quarterly")
    qlabels=[str(text.get(f"y{i}","")) for i in range(1,5)]
    if not all("Q" in x for x in qlabels):
        fail(f"{name} quarterly labels invalid: {qlabels}")
    if not any(x.endswith("E") for x in qlabels):
        fail(f"{name} next-quarter estimate missing")
    if "adjusted_score" in p or "sentiment_modifier" in p:
        fail(f"{name} stock sentiment adjustment must be removed")
    ts=str(text.get("tradingScore","")).strip()
    if not re.fullmatch(r"\d{1,3}",ts):
        fail(f"{name} Trading Score must be numeric only")
    else:
        score=int(ts)
        if not 0<=score<=100:
            fail(f"{name} Trading Score out of range")
    for k in ("macdScore","maScore","rsiScore","stochScore","tradingGrade","tradingTrend","tradingAction","divergence"):
        if not str(text.get(k,"")).strip():
            fail(f"{name} technical field missing: {k}")
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

    sentiment=data.get("sentiment") or {}
    kr=sentiment.get("kr") or {}
    us=sentiment.get("us") or {}
    try:
        kr_score=float(kr.get("score"))
    except Exception:
        kr_score=-1
    if not (0 <= kr_score <= 100):
        fail("Korea sentiment score missing/invalid")
    if len(kr.get("components") or []) < 6:
        fail("Korea sentiment components incomplete")
    try:
        us_score=float(us.get("score"))
    except Exception:
        us_score=-1
    if us.get("available") and not (0 <= us_score <= 100):
        fail("US sentiment score invalid")
    try:
        blend=float(sentiment.get("blended_score"))
    except Exception:
        blend=-1
    if not (0 <= blend <= 100):
        fail("blended sentiment score missing/invalid")

html=(ROOT/"index.html").read_text(encoding="utf-8")
if "TOP5 추천점수 시장심리 조정" in html or "심리 0 [AI]" in html:
    fail("stock sentiment score wording remains in HTML")
if "Trading Score" not in html or "다음 분기 EPS(E)" not in html:
    fail("new Trading Score / quarterly valuation UI missing")
if "Trading Indicators" in html:
    fail("duplicate Trading Indicators block remains")
if "data-profile-market=\"KOSPI\"" not in html or "data-profile-market=\"KOSDAQ\"" not in html:
    fail("KOSPI/KOSDAQ TOP5 selector missing")
if "trendStateCard" not in html or "actionStateCard" not in html or "heatStateCard" not in html:
    fail("colored trading state cards missing")

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
    f"- KOSPI TOP5: **{len(kospi_profiles)}**",
    f"- KOSDAQ TOP5: **{len(kosdaq_profiles)}**",
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
