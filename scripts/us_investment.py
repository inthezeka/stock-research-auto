"""US-only company universe, Internal Sector Classification, Theme and sector evidence.

Never identify a manually mapped investment category as official GICS.
Sector and Theme market labels are independent of Fundamental scores.
"""
from __future__ import annotations
import csv
import io
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
import json
import requests

ROOT=Path(__file__).resolve().parents[1]
CONFIG=json.loads((ROOT/"config/us_investment_sectors.json").read_text(encoding="utf-8"))
SECTORS={x["key"]:x for x in CONFIG["sectors"]}
THEME_NAMES={x["name"] for x in CONFIG["themes"]}
SEED_LOOKUP={}
for sec in CONFIG["sectors"]:
    for code in sec["seed_tickers"]:
        # Duplicate seed tickers get a single, provisional main investment sector.
        SEED_LOOKUP.setdefault(code,sec["key"])
# Explicit high-level cross-sector cases should be validated against segment results.
SEED_LOOKUP.update({"GOOGL":"US13","GOOG":"US13","AMZN":"US09","ETN":"US16","GE":"US23","HON":"US15"})
EXCHANGE_MAP={"Q":"NASDAQ","G":"NASDAQ","S":"NASDAQ","N":"NYSE","A":"NYSE American"}
LIST_URLS=[
    ("NASDAQ","https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"),
    ("OTHER","https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt")
]
KEYWORDS=[
    ("US01",("graphic processor","gpu","semiconductors","semiconductor designer","ai chip")),
    ("US02",("storage peripherals","storage devices","memory products","flash memory","data storage")),
    ("US03",("foundries","semiconductor foundry")),
    ("US04",("semiconductor equipment","semiconductor capital equipment")),
    ("US05",("electronic test","semiconductor materials","semiconductor testing")),
    ("US06",("computer server","server hardware","enterprise hardware")),
    ("US07",("networking equipment","communication equipment","optical","network infrastructure")),
    ("US08",("data center infrastructure","cooling equipment","electrical infrastructure")),
    ("US09",("cloud infrastructure","cloud computing","hyperscaler")),
    ("US10",("application software","enterprise software","saas","database software")),
    ("US11",("cybersecurity","security software")),
    ("US12",("data analytics","ai software","data processing software")),
    ("US13",("interactive media","digital advertising","social media","internet advertising")),
    ("US14",("technology hardware","consumer electronics","computer hardware")),
    ("US15",("industrial automation","automation equipment","robotics")),
    ("US16",("electrical components","electrical equipment","electrical utilities equipment","power equipment")),
    ("US17",("electric utilities","independent power","multi-utilities","regulated utilities")),
    ("US18",("nuclear","uranium")),
    ("US19",("oil & gas exploration","integrated oil","oil and gas exploration","oil production")),
    ("US20",("oil & gas storage","oil & gas transportation","midstream","lng")),
    ("US21",("solar","renewable energy","energy storage")),
    ("US22",("aerospace & defense","defense industry","defense contractors")),
    ("US23",("commercial aerospace","aircraft manufacturers","airlines equipment","space infrastructure")),
    ("US24",("construction machinery","industrial machinery","trading companies & distributors","heavy machinery")),
    ("US25",("automobile manufacturers","automotive components","auto parts")),
    ("US26",("battery","lithium")),
    ("US27",("metals & mining","copper","steel","rare earth","aluminum","gold")),
    ("US28",("pharmaceuticals","drug manufacturers")),
    ("US29",("biotechnology","life sciences tools")),
    ("US30",("health care equipment","medical devices","health care technology")),
    ("US31",("managed health care","healthcare providers","health care services","hospitals")),
    ("US32",("diversified banks","regional banks","consumer banking","banks")),
    ("US33",("capital markets","asset management","investment banking")),
    ("US34",("insurance","reinsurance")),
    ("US35",("transaction & payment processing","payment processing","fintech","payments")),
    ("US36",("digital asset","crypto platform")),
    ("US37",("specialized reits","equity reits","real estate investment trust","real estate")),
    ("US38",("broadline retail","specialty retail","consumer staples distribution","retailing","e-commerce")),
    ("US39",("hotels","resorts","travel services","cruise lines","leisure")),
    ("US40",("entertainment","movies","gaming","broadcasting","media","interactive home entertainment")),
    ("US41",("telecommunication services","wireless telecommunication","telecom")),
    ("US42",("ground transportation","air freight","cargo transportation","railroads","trucking","marine transportation")),
    ("US43",("agricultural chemicals","fertilizer","agriculture","crop")),
    ("US44",("beverages","packaged foods","household products","personal care","tobacco")),
]
GICS_FALLBACK={
    "Information Technology":"US10","Health Care":"US31","Communication Services":"US13",
    "Consumer Discretionary":"US38","Consumer Staples":"US44",
    "Financials":"US32","Energy":"US19","Utilities":"US17",
    "Industrials":"US24","Materials":"US27","Real Estate":"US37"
}

def classify_company(item):
    ticker=str(item.get("ticker") or "").upper()
    industry=(str(item.get("subindustry") or "")+" "+str(item.get("industry") or "")).lower()
    market_sector=str(item.get("gics") or "")
    key=None;method="UNCLASSIFIED"
    # Specific industry wording takes priority over the generic company-name match.
    for sid,keys in KEYWORDS:
        if any(term in industry for term in keys):
            key=sid;method="PUBLIC_INDUSTRY_TEXT";break
    # Candidate dictionary is provisional. It is not a verified segment-revenue mix.
    if ticker in SEED_LOOKUP:
        key=SEED_LOOKUP[ticker];method="CURATED_TICKER_CANDIDATE"
    if key is None:
        key=GICS_FALLBACK.get(market_sector)
        if key:method="PUBLIC_SECTOR_TEXT_FALLBACK"
    if key is None:
        return {"sector_key":None,"sector_label":"업종 검증 필요",
                "sector_classification":"Internal Sector Classification",
                "classification_method":"UNCLASSIFIED","classification_review":True}
    group=SECTORS[key]
    review=method!="PUBLIC_INDUSTRY_TEXT" or ticker in ("GOOGL","GOOG","AMZN","ETN")
    return {"sector_key":key,"sector_label":group["label"],
            "sector_classification":"Internal Sector Classification",
            "classification_method":method,"classification_review":review}

def classify_themes(sector_key,verification=None):
    """Return candidate theme tags, not revenue-beneficiary claims."""
    cfg=SECTORS.get(sector_key) or {}
    cands=[t for t in cfg.get("primary_theme_candidates",[]) if t in THEME_NAMES]
    verified=verification or {}
    primary=[];secondary=[]
    for theme in cands[:3]:
        evidence=verified.get(theme) or {}
        status=evidence.get("status") if evidence.get("source_url") else None
        if status not in ("VERIFIED REVENUE","CONTRACT / PIPELINE"):
            status="NARRATIVE ONLY"
        primary.append({"theme":theme,"status":status,"source_url":evidence.get("source_url"),
                        "evidence_note":"Sector-based watch candidate, revenue relationship unverified" if status=="NARRATIVE ONLY" else evidence.get("note")})
    return {"primary_themes":primary,"secondary_themes":secondary}

def is_common_stock(name, etf):
    text=str(name or "").lower()
    if etf=="Y" or not text:return False
    if any(t in text for t in ("warrant", "right to buy","subscription rights","preferred stock",
                               "preferred shares","depositary shares representing preferred",
                               "unit consisting","trust unit","exchange-traded fund",
                               "exchange traded fund","etn","exchange traded note",
                               "  units")):return False
    # Securities such as ETFs, warrants and units use separate risk models.
    return True

def normalize_symbol(s):
    s=str(s or "").strip().upper().replace(".","-").replace("/","-")
    return s if re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?",s) else None

def fetch_us_listed(session=None):
    """NASDAQ Trader symbol directories; NASDAQ, NYSE, NYSE American common-stock candidates."""
    session=session or requests.Session()
    out={};coverage={};errors={}
    for kind,url in LIST_URLS:
        try:
            r=session.get(url,timeout=25)
            r.raise_for_status()
            rows=list(csv.DictReader(io.StringIO(r.text),delimiter="|"))
            accepted=0
            for row in rows:
                if any(str(v or "").startswith("File Creation Time") for v in row.values()):continue
                ticker=normalize_symbol(row.get("Symbol") if kind=="NASDAQ" else row.get("NASDAQ Symbol") or row.get("ACT Symbol"))
                name=row.get("Security Name","")
                etf=str(row.get("ETF") or "N").upper()
                if not ticker or row.get("Test Issue")=="Y" or not is_common_stock(name,etf):continue
                exchange="NASDAQ" if kind=="NASDAQ" else EXCHANGE_MAP.get(row.get("Exchange"))
                if exchange not in ("NASDAQ","NYSE","NYSE American"):continue
                status=str(row.get("Financial Status") or "")
                adr=bool(re.search(r"\b(ADR|ADS|american depositary)\b",name,re.I))
                out[ticker]={"ticker":ticker,"name":name,
                             "exchange":exchange,"security_type":"ADR" if adr else "COMMON_STOCK_CANDIDATE",
                             "foreign_issuer":True if adr else None,"financial_status":status,
                             "gics":"","subindustry":"","indices":[],"listing_source":"NasdaqTrader symbol directory"}
                accepted+=1
            if accepted<500:raise RuntimeError(f"{kind} directory too small: {accepted}")
            coverage[kind]=accepted
        except Exception as e:errors[kind]=str(e)[:180]
    return out,coverage,errors

def _median(data):
    vals=[float(v) for v in data if isinstance(v,(int,float)) and math.isfinite(v)]
    return statistics.median(vals) if vals else None

def aggregate_sector_cycle(items):
    """Price-only sector signals; do not infer unverified inventory/orders/guidance."""
    groups=defaultdict(list)
    for row in items:
        if row.get("sector_key") in SECTORS:groups[row["sector_key"]].append(row)
    result=[]
    for key,sec in SECTORS.items():
        members=groups.get(key,[])
        valid=[x for x in members if x.get("close") is not None]
        returns=[x.get("return20d") for x in valid if x.get("return20d") is not None]
        ratio= len(valid)/len(members) if members else 0
        breadth=sum(1 for x in returns if x>0)/len(returns) if returns else None
        momentum=_median(returns)
        # Need actual 7-factor business evidence to publish a 100-point cycle score.
        result.append({"key":key,"label":sec["label"],"count":len(members),
                       "verified_quotes":len(valid),"quote_coverage":round(ratio,3),
                       "median_20d":round(momentum,2) if momentum is not None else None,
                       "positive_breadth":round(breadth,3) if breadth is not None else None,
                       "actual_cycle":"미판정","cycle_attractiveness":"미판정",
                       "cycle_score":None,"fundamental_environment":None,"investment_score":None,
                       "evidence_coverage":round(20/100*ratio,3) if momentum is not None else 0,
                       "note":"가격 모멘텀만 검증됨. 재고·수주·Guidance 등 7개 Cycle 요인 부족"})
    return result

def aggregate_theme_popularity(items):
    """Return provisional price-based theme watchlist without a fabricated popularity score."""
    by_theme=defaultdict(list)
    for row in items:
        for tag in row.get("primary_themes") or []:
            if tag.get("theme"):by_theme[tag["theme"]].append(row)
    output=[]
    for tag in CONFIG["themes"]:
        name=tag["name"];members=by_theme.get(name,[])
        relevant=[x for x in members if x.get("return20d") is not None and x.get("close") is not None]
        rt=_median([x["return20d"] for x in relevant]);bd=sum(1 for x in relevant if x["return20d"]>0)/len(relevant) if relevant else None
        sectors=len(set(x.get("sector_key") for x in members if x.get("sector_key")))
        output.append({"theme":name,"members":len(members),
                       "sector_count":sectors,
                       "representative_tickers":[x["ticker"] for x in relevant[:5]],
                       "price_momentum_20d":round(rt,2) if rt is not None else None,
                       "price_breadth":round(bd,3) if bd is not None else None,
                       "popularity_score":None,"status":"INSUFFICIENT",
                       "evidence_coverage":0.0,
                       "catalyst":"미확인","monetization_stage":"NARRATIVE ONLY",
                       "note":"뉴스/실적 Catalyst/상대강도 등 검증값 부족: 인기테마 판정 보류"})
    return sorted(output,key=lambda x:(x["price_momentum_20d"] is not None,x["price_momentum_20d"] or -100),reverse=True)

def aggregate_investment_best5(items):
    # Valuation / Risk Reward and verifiable catalysts are not yet available.
    return {"status":"INSUFFICIENT","ranking":[],"note":"Fundamental/Valuation-RR/Catalyst/Technical/Liquidity 5개 축을 모두 검증하지 못해 BEST5 미발행"}

def sector_model(key):
    return (SECTORS.get(key) or {}).get("model")

def initial_membership_check(items):
    return {"scope":"tracked_US_listed_candidates","total":len(items),
            "scored":sum(x.get("fundamental_score") is not None for x in items),
            "minimum_model_coverage_met":False,
            "full_universe_ranking_verified":False}
