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
try:
    sector_cfg=json.loads((ROOT/"config/fundamental_themes.json").read_text(encoding="utf-8"))
except Exception as e:
    raise SystemExit("fundamental_themes.json invalid: "+str(e))
sectors=sector_cfg.get("sectors") or []
if len(sectors)!=36:
    fail(f"Sector taxonomy count={len(sectors)}; expected 36")
for s in sectors:
    if not s.get("key") or not s.get("label"):
        fail("Sector taxonomy missing key/label")
    if not (s.get("subsectors") or []):
        fail(f"{s.get('label','?')} Sub-sector missing")
    if not (s.get("themes") or []):
        fail(f"{s.get('label','?')} Theme mapping missing")
sector_cycle=data.get("sector_cycle") or {}
sector_rows=sector_cycle.get("sectors") or []
if len(sector_rows)!=36:
    fail(f"daily Sector Cycle count={len(sector_rows)}; expected 36")
sector_keys={str(x.get("key") or "") for x in sector_rows}
missing_cycle=[s.get("key") for s in sectors if s.get("key") not in sector_keys]
if missing_cycle:
    fail("daily Sector Cycle missing taxonomy keys: "+", ".join(missing_cycle[:10]))

groups=data.get("stock_profile_groups") or {}
kospi_profiles=groups.get("KOSPI") or data.get("stock_profiles") or []
kosdaq_profiles=groups.get("KOSDAQ") or data.get("stock_profiles_kosdaq") or []
profiles=kospi_profiles
if len(kospi_profiles)!=5:
    fail(f"KOSPI TOP5 profile count={len(kospi_profiles)}")
if not (0 <= len(kosdaq_profiles) <= 5):
    fail(f"KOSDAQ qualified profile count={len(kosdaq_profiles)}")
if kosdaq_profiles:
    priorities=[0 if float(p.get("selection_trading_score",0))>=70 else 1 for p in kosdaq_profiles]
    if priorities!=sorted(priorities):
        fail("KOSDAQ 70+ priority ordering violated")
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
    for k in ("currentPriceText","fairPriceText","price1","target1Display"):
        if not str(text.get(k,"")).strip() or str(text.get(k,"")).strip()=="—":
            fail(f"{name} visible profile data missing: {k}")
    if p.get("market")=="KOSDAQ":
        for k in ("bullPrice","bullProb","bullReturn","bullCond","basePrice","baseProb","baseReturn","baseCond","bearPrice","bearProb","bearReturn","bearCond"):
            val=str(text.get(k,"")).strip()
            if not val or val=="—":
                fail(f"{name} KOSDAQ scenario missing: {k}")
        ir=(p.get("links") or {}).get("ir","")
        if "finance.naver.com/item/main.naver" in ir or not ir.startswith("http"):
            fail(f"{name} KOSDAQ IR link not connected: {ir}")
        try:
            gate=float(p.get("selection_trading_score"))
            fundamental=float(p.get("fundamental_score"))
            final=float(p.get("selection_score"))
        except Exception:
            fail(f"{name} KOSDAQ selection scores missing")
        else:
            if gate < 60:
                fail(f"{name} KOSDAQ Trading Score gate failed: {gate}")
            expected=fundamental*0.60+gate*0.40
            if abs(final-expected)>0.11:
                fail(f"{name} KOSDAQ composite mismatch: {final} vs {expected:.2f}")
            priority=str(p.get("selection_priority",""))
            if gate>=70 and priority!="70+":
                fail(f"{name} KOSDAQ priority mismatch")
            if 60<=gate<70 and priority!="60~69":
                fail(f"{name} KOSDAQ priority mismatch")
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
    kospi_sent=kr.get("kospi") or {}
    kosdaq_sent=kr.get("kosdaq") or {}
    if kospi_sent or kosdaq_sent:
        if len(kospi_sent.get("components") or []) < 6:
            fail("KOSPI sentiment components incomplete")
        if len(kosdaq_sent.get("components") or []) < 6:
            fail("KOSDAQ sentiment components incomplete")
        for label,obj in (("KOSPI",kospi_sent),("KOSDAQ",kosdaq_sent)):
            flow=obj.get("flow") or {}
            if flow.get("foreign") is None or flow.get("institution") is None:
                fail(f"{label} sentiment flow incomplete")
    elif len(kr.get("components") or []) < 6:
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
if '<section id="blueprint"' in html or "Theme Radar · 테마별 추천 3종목" in html:
    fail("obsolete Theme Radar section must be removed")
# Fundamental Theme + MY Trading dual-selection UI validation.
# The former Technical & Market Structure / Trading Score page was intentionally removed.
if 'id="technical"' in html or "Technical & Market Structure · Trading Score" in html:
    fail("legacy Technical & Market Structure section must be removed")
required_ui=[
    "③ 종목 선정 · Fundamental / MY Trading",
    "① Fundamental Sector",
    "Fundamental TOP 20",
    'id="fundSubsectorList"',
    'id="fundPrimaryThemeList"',
    'id="sectorUniverseList"',
    "MY Trading TOP 10",
    "ENTRY READY",
    "WATCH LIST",
    'id="fundThemeList"',
    'id="fundamentalScanBtn"',
    'id="ictScanBtn"',
    'id="fundTop10Body"',
    'id="ictTop10Body"',
    'id="entryReadyBody"',
    'id="watchListBody"',
]
for token in required_ui:
    if token not in html:
        fail(f"Fundamental/MY Trading screening UI missing: {token}")
if 'id="stockSearchInput"' not in html or 'id="stockSearchMarket"' not in html or 'function buildSearchProfile' not in html:
    fail("KOSPI/KOSDAQ live stock search UI missing")
if "front-api/search/autoComplete" not in html or "chart/domestic/item/" not in html:
    fail("live stock search data endpoints missing")
if "function runFundamentalPipeline" not in html or "function runIctTradingScan" not in html:
    fail("dual screening engine missing")
if "Sub-sector</th>" not in html or "Sector > Sub-sector > Theme" not in html:
    fail("3-level Sector/Sub-sector/Theme taxonomy UI missing")

# 36-sector Fundamental scoring model validation.
model_match=re.search(r"const SR_SECTOR_SCORE_MODELS=(\{.*?\});\nconst SR_SECTOR_VARIANT_PATTERNS",html,re.S)
sector_model_count=0
sector_model_weight_errors=[]
if not model_match:
    fail("36-sector Fundamental score model config missing")
else:
    try:
        score_models=json.loads(model_match.group(1))
        sector_model_count=len(score_models)
        if sector_model_count!=36:
            fail(f"Fundamental score model count={sector_model_count}; expected 36")
        taxonomy_keys={str(x.get("key") or "") for x in sectors}
        missing_models=sorted(taxonomy_keys-set(score_models))
        if missing_models:
            fail("Fundamental score models missing: "+", ".join(missing_models))
        for key,model in score_models.items():
            groups=model.get("variants") or {"default":model.get("items") or []}
            if not groups:
                sector_model_weight_errors.append(f"{key}: no scoring items")
                continue
            for variant,items in groups.items():
                total=sum(float(x[1]) for x in items if isinstance(x,list) and len(x)>=2)
                if abs(total-100)>0.001:
                    sector_model_weight_errors.append(f"{key}/{variant}: total={total}")
        if sector_model_weight_errors:
            fail("Sector score model weights must total 100: "+"; ".join(sector_model_weight_errors[:10]))
    except Exception as e:
        fail("Fundamental score model config invalid: "+str(e))

# Each Sector may evaluate all configured members, but the visible ranking must be capped at TOP 20.
if "const top20=reports.slice(0,20);" not in html:
    fail("Fundamental Sector result is not capped at TOP 20")
sector_candidate_counts={str(x.get("label") or x.get("key")):len(x.get("codes") or []) for x in sectors}
sector_display_caps={k:min(20,v) for k,v in sector_candidate_counts.items()}
if any(v>20 for v in sector_display_caps.values()):
    fail("Sector TOP20 display cap validation failed")

ict_framework_tokens=[
    "HTF Long Bias",
    "Protected Low 유지",
    "Upward DOL",
    "Bullish Liquidity Event",
    "Bullish MSS / BOS",
    "Bullish Displacement",
    "Bullish FVG / OB / Breaker",
    "Discount / OTE",
    "Risk / Reward",
    "Setup Freshness",
    "Data Confidence",
    "Normalized:",
]
for token in ict_framework_tokens:
    if token not in html:
        fail(f"MY Trading ICT score framework missing: {token}")

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
    f"- Fundamental sector score models: **{sector_model_count}/36**",
    f"- Fundamental Sector visible cap: **TOP 20**",
    f"- Sector configured members: **min {min(sector_candidate_counts.values()) if sector_candidate_counts else 0} / max {max(sector_candidate_counts.values()) if sector_candidate_counts else 0}**",
    f"- Sectors with >=20 configured members: **{sum(1 for v in sector_candidate_counts.values() if v>=20)}/36**",
]
if errors:
    report+=["","## Errors"]+[f"- {x}" for x in errors]
if warnings:
    report+=["","## Warnings"]+[f"- {x}" for x in warnings]
(ROOT/"QA_REPORT.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report))
if errors:
    raise SystemExit(1)
