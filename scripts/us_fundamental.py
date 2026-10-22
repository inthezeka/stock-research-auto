"""SEC EDGAR XBRL FY extraction + US industry-specific evidence-weighted Fundamental model.

- Sources: HISTORICAL ACTUAL only. Guidance / Consensus are not invented.
- FY facts must have genuine annual duration and a common fiscal-end date.
- Missing weights are declared neutral (2.5/5) only if >=50% evidence exists.
- LOW evidence caps published score at 75; INSUFFICIENT publishes null.
- Trading Score, momentum, 5D volume and timing are not used here.
"""
from __future__ import annotations
import math
from datetime import date,datetime
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/"config/us_investment_sectors.json").read_text(encoding="utf-8"))
SECTORS={x["key"]:x for x in CFG["sectors"]}
DURATION_TAGS={
 "revenue":["RevenueFromContractWithCustomerExcludingAssessedTax","Revenues","SalesRevenueNet","RevenueFromContractWithCustomerIncludingAssessedTax","SalesRevenueGoodsNet"],
 "operating_income":["OperatingIncomeLoss"],
 "net_income":["NetIncomeLoss","ProfitLoss"],
 "eps_diluted":["EarningsPerShareDiluted","EarningsPerShareBasic"],
 "gross_profit":["GrossProfit"],
 "operating_cash_flow":["NetCashProvidedByUsedInOperatingActivities"],
 "capex":["PaymentsToAcquirePropertyPlantAndEquipment","PaymentsToAcquireProductiveAssets"],
 "sbc":["ShareBasedCompensation"],
}
INSTANT_TAGS={
 "cash":["CashAndCashEquivalentsAtCarryingValue","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
 "debt_noncurrent":["LongTermDebtNoncurrent","LongTermDebt"],
 "debt_current":["LongTermDebtCurrent","LongTermDebtAndCapitalLeaseObligationsCurrent"],
 "assets":["Assets"],
 "equity":["StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
 "shares_outstanding":["CommonStockSharesOutstanding"],
}
def finite(v):
    try:
        f=float(v)
        return f if math.isfinite(f) else None
    except (TypeError,ValueError,OverflowError):return None

def _series(payload,tags,units,period="annual"):
    us=(payload.get("facts") or {}).get("us-gaap") or {}
    found={}
    for tag in tags:
        block=us.get(tag) or {}
        for row in (block.get("units") or {}).get(units) or []:
            if row.get("form") not in ("10-K","10-K/A") or not row.get("end") or finite(row.get("val")) is None:
                continue
            if period=="annual":
                if not row.get("start"):continue
                try: duration=(date.fromisoformat(row["end"])-date.fromisoformat(row["start"])).days
                except (TypeError,ValueError):continue
                if not 325<=duration<=380:continue
            elif row.get("start"):
                continue
            if not str(row.get("filed") or "").startswith("20"):continue
            end=str(row["end"]);filed=str(row.get("filed") or "")
            current=found.get(end)
            if current is None or (filed, str(row.get("accn") or "")) > (current.get("filed",""),current.get("accn","")):
                found[end]={"end":end,"value":float(row["val"]),"filed":filed,
                            "form":row.get("form"),"tag":tag,"frame":row.get("frame")}
    return sorted(found.values(),key=lambda r:r["end"],reverse=True)

def extract_sec_annual(payload):
    fact=payload.get("facts") or {}
    gaap=fact.get("us-gaap") or {}
    if not gaap:
        return {"status":"IFRS_OR_UNSUPPORTED","source_basis":"HISTORICAL ACTUAL",
                "reason":"US GAAP fact set unavailable; Foreign Issuer/IFRS requires a separate 20-F mapping."}
    annual={}
    for metric,tags in DURATION_TAGS.items():
        annual[metric]=_series(payload,tags,"USD/shares" if metric=="eps_diluted" else "USD")
    revenue=annual["revenue"]
    # No fiscal-duration guessing or mixing different fiscal ends.
    if len(revenue)<2:
        return {"status":"INSUFFICIENT","source_basis":"HISTORICAL ACTUAL",
                "reason":"At least two FY revenue statements are required"}
    fy_end=revenue[0]["end"]
    extract=lambda key:next((x["value"] for x in annual[key] if x["end"]==fy_end),None)
    def instant(key):
        seq=_series(payload,INSTANT_TAGS[key],"shares" if key=="shares_outstanding" else "USD",period="instant")
        return next((x["value"] for x in seq if x["end"]==fy_end),None)
    revenue_new=extract("revenue")
    revenue_old=next((x["value"] for x in revenue[1:] if x["end"]!=fy_end),None)
    if revenue_new is None or revenue_old is None or revenue_new<=0 or revenue_old<=0:
        return {"status":"INSUFFICIENT","reason":"Invalid fiscal revenue values"}
    # YoY requires adjacent FY closing periods, not the latest arbitrary two filings.
    prev_end=next((x["end"] for x in revenue[1:] if x["end"]!=fy_end),None)
    try:
        annual_gap=(date.fromisoformat(fy_end)-date.fromisoformat(prev_end)).days
    except (ValueError,TypeError):
        annual_gap=0
    if not 320<=annual_gap<=410:
        return {"status":"INSUFFICIENT","reason":"Nonadjacent fiscal-year periods; YoY cannot be verified"}
    metrics={
        "revenue":revenue_new,"prior_revenue":revenue_old,
        "revenue_growth":(revenue_new/revenue_old-1)*100,
        "operating_income":extract("operating_income"),"net_income":extract("net_income"),
        "eps_fy":extract("eps_diluted"),"gross_profit":extract("gross_profit"),
        "operating_cash_flow":extract("operating_cash_flow"),
        "capex":extract("capex"),"sbc":extract("sbc"),
        "cash":instant("cash"),
        "debt_noncurrent":instant("debt_noncurrent"),
        "debt_current":instant("debt_current"),
        "assets":instant("assets"),"equity":instant("equity"),
        "shares_outstanding":instant("shares_outstanding"),
    }
    debt=metrics.get("debt_noncurrent");short=metrics.get("debt_current")
    metrics["debt"]=(debt or 0)+(short or 0) if debt is not None or short is not None else None
    metrics["opm"]=metrics["operating_income"]/revenue_new*100 if metrics["operating_income"] is not None else None
    metrics["net_margin"]=metrics["net_income"]/revenue_new*100 if metrics["net_income"] is not None else None
    metrics["gross_margin"]=metrics["gross_profit"]/revenue_new*100 if metrics["gross_profit"] is not None else None
    cfo=metrics["operating_cash_flow"];capex=metrics["capex"]
    # FCF = operating cash flow - positive cash CAPEX.
    # Negative tagged CAPEX is a filing sign-convention anomaly requiring verification.
    metrics["fcf"]=(cfo-capex) if cfo is not None and capex is not None and capex>=0 else None
    metrics["fcf_margin"]=metrics["fcf"]/revenue_new*100 if metrics["fcf"] is not None else None
    metrics["roe"]=metrics["net_income"]/metrics["equity"]*100 if metrics["net_income"] is not None and metrics["equity"] not in (None,0) else None
    metrics["fiscal_year_end"]=fy_end
    metrics["source_basis"]="HISTORICAL ACTUAL"
    metrics["accounting_standard"]="US GAAP"
    metrics["fiscal_period"]="FY"
    metrics["filing_date"]=revenue[0]["filed"]
    metrics["cik"]=str(payload.get("cik") or "").zfill(10) if payload.get("cik") else None
    metrics["source"]="SEC EDGAR XBRL Company Facts"
    metrics["status"]="EXTRACTED"
    return metrics

def clamp(v,lo=0,hi=5):
    return max(lo,min(hi,finite(v) or 0))

def scored_component(value,coverage,basis,detail):
    if value is None:
        return {"value_0_5":None,"coverage":0.0,"basis":"MISSING","detail":detail}
    return {"value_0_5":round(clamp(value),3),"coverage":max(0,min(1,coverage)),
            "basis":basis,"detail":detail}

def _components(fin,price,sector_key):
    g=finite(fin.get("revenue_growth"))
    opm=finite(fin.get("opm")); net=finite(fin.get("net_margin"))
    gross=finite(fin.get("gross_margin"));fcf=finite(fin.get("fcf_margin"))
    roe=finite(fin.get("roe"));cash=finite(fin.get("cash"));debt=finite(fin.get("debt"))
    eps=finite(fin.get("eps_fy"));equity=finite(fin.get("equity"));shares=finite(fin.get("shares_outstanding"))
    model=SECTORS[sector_key]["model"]
    comps={}
    # G cannot be claimed as actual FORWARD growth without company guidance/consensus.
    comps["G"]=scored_component(clamp(2.5+(g-10)/10) if g is not None else None,
        .65,"HISTORICAL_ACTUAL_PROXY","FY revenue growth; not a forward growth forecast")
    if opm is not None and net is not None:
        quality=.55*clamp(2.5+opm/13)+.45*clamp(2.5+net/14)
        comps["Q"]=scored_component(quality,.85,"HISTORICAL_ACTUAL","FY operating/net margins")
    elif opm is not None:
        comps["Q"]=scored_component(2.5+opm/13,.55,"HISTORICAL_ACTUAL","FY operating margin only")
    else:
        comps["Q"]=scored_component(None,0,"MISSING","Profitability unavailable")
    if fcf is not None and cash is not None and debt is not None:
        liquidity=(cash / max(debt,1)) if debt>=0 else None
        safety=(clamp(2.5+fcf/12)+clamp(2.5+math.log10(max(.01,liquidity))/1.4))/2
        comps["B"]=scored_component(safety,.85,"HISTORICAL_ACTUAL","FY FCF margin + cash/debt")
    elif fcf is not None:
        comps["B"]=scored_component(2.5+fcf/12,.55,"HISTORICAL_ACTUAL","FY free cash flow, debt unknown")
    else:
        comps["B"]=scored_component(None,0,"MISSING","Cash flow or balance sheet unavailable")
    # Fiscal EPS and share price may be on a different split-adjustment basis.
    # For REIT, banks and pre-profit biotech sector-specific valuations are mandatory.
    pe=price/eps if price is not None and eps is not None and eps>0 else None
    pbr=(price*shares/equity) if price and shares and equity and equity>0 else None
    if sector_key in ("US32","US33","US34") and pbr:
        comps["V"]=scored_component(clamp(3.3-.7*math.log(max(.05,pbr))),.70,"HISTORICAL_ACTUAL_PROXY","Book-based valuation; no forward ROE consensus")
    elif sector_key in ("US29","US37") or (model=="Cyclical / Commodities" and pe is None):
        comps["V"]=scored_component(None,0,"MISSING","Sector-specific valuation (rNPV/P-AFFO/midcycle EV) not verified")
    elif pe and 0<pe<500:
        comps["V"]=scored_component(clamp(4.7-math.log(max(1,pe),2.1)),.65,"HISTORICAL_ACTUAL_PROXY","Unadjusted current price / FY GAAP EPS; check stock splits")
    else:
        comps["V"]=scored_component(None,0,"MISSING","Valuation or comparable FY GAAP EPS unavailable")
    # K never represents unverified contracts, backlog, customer CAPEX, or AI revenues.
    sector_metrics=";".join(SECTORS[sector_key].get("sector_kpis") or []).lower()
    kparts=[]
    if gross is not None and "gross margin" in sector_metrics:
        kparts.append(("SEC FY gross margin",clamp(2.4+gross/17)))
    if fcf is not None and ("fcf" in sector_metrics or "cash conversion" in sector_metrics or "현금흐름" in sector_metrics):
        kparts.append(("SEC FY FCF margin",clamp(2.5+fcf/12)))
    if roe is not None and ("roe" in sector_metrics or "자본수익" in sector_metrics):
        kparts.append(("SEC FY ROE",clamp(2.5+roe/15)))
    if sector_key=="US29" and cash is not None and finite(fin.get("operating_cash_flow")) is not None:
        burn=-float(fin["operating_cash_flow"])
        if burn>0:
            runway=cash/burn
            kparts.append(("SEC annual cash runway estimate",clamp(1+runway)))
    if kparts:
        comps["K"]=scored_component(sum(x[1] for x in kparts)/len(kparts),
                                     min(.55,.20*len(kparts)),"HISTORICAL_ACTUAL_PROXY",
                                     "; ".join(x[0] for x in kparts)+"; other industry-specific KPI unverified")
    else:
        comps["K"]=scored_component(None,0,"MISSING","Company-specific sector KPI not verified")
    # No licensed earnings revisions and no validated issuer guidance.
    comps["R"]=scored_component(None,0,"MISSING","Licensed analyst revisions / confirmed guidance unavailable")
    extra={"pe_fy":round(pe,2) if pe is not None and math.isfinite(pe) else None,
           "pbr":round(pbr,2) if pbr is not None and math.isfinite(pbr) else None,
           "revenue_growth":g,"gross_margin":gross,"opm":opm,"fcf_margin":fcf}
    return comps,extra

def assess_fundamental(fin,price,sector_key,as_of=None):
    """Return (published_score_or_none, disclosure). Missing evidence != zero score."""
    if sector_key not in SECTORS or fin.get("status")!="EXTRACTED":
        return None,{"confidence":"INSUFFICIENT","evidence_coverage":0,
                     "reason":"SEC factual annual evidence unavailable", "model":None}
    if finite(fin.get("revenue")) is None or not fin.get("fiscal_year_end"):
        return None,{"confidence":"INSUFFICIENT","evidence_coverage":0,
                     "reason":"FY actual incomplete","model":SECTORS[sector_key]["model"]}
    weights=dict(SECTORS[sector_key]["score_weights"])
    # Explicit NO_CONSENSUS_PROFILE: R/2 to G, R/2 to K.
    bonus=weights["R"]/2
    weights["G"]+=bonus;weights["K"]+=bonus;weights["R"]=0
    assert abs(sum(weights.values())-100)<1e-8
    comps,extra=_components(fin,finite(price),sector_key)
    evidence=sum(weights[k]*comps[k]["coverage"] for k in ("G","Q","B","V","K","R"))/100
    evidence=round(min(1,evidence),3)
    confidence=("HIGH" if evidence>=.85 else "MEDIUM" if evidence>=.70
                else "LOW" if evidence>=.50 else "INSUFFICIENT")
    detail={"model":SECTORS[sector_key]["model"],"effective_weights":weights,
            "profile":"NO_CONSENSUS_PROFILE","source_basis":"HISTORICAL ACTUAL",
            "confidence":confidence,"evidence_coverage":evidence,
            "components":comps,
            "missing": [k for k in ("G","Q","B","V","K") if comps[k]["coverage"]==0],
            "neutral_imputation":[],
            "valuation_context":extra,
            "company_guidance":None,"licensed_analyst_consensus":None,
            "ai_scenario_estimate":None,
            "note":"SEC FY Historical Actual + declared neutral uncovered weights; G is backward-looking proxy. No revisions are inferred."}
    # A very old audited fiscal filing cannot justify a fresh Fundamental rating.
    if as_of:
        try:
            months=(date.fromisoformat(as_of)-date.fromisoformat(fin["fiscal_year_end"])).days
            if months>540:
                detail.update(confidence="INSUFFICIENT",reason="Audited FY older than 540 days",evidence_coverage=0)
                return None,detail
        except ValueError:pass
    if confidence=="INSUFFICIENT":
        detail["reason"]="Evidence coverage below 50%; no numeric rating issued"
        return None,detail
    total=0.0
    for key,weight in weights.items():
        part=comps[key]
        verified=part["coverage"]
        if verified<1 and weight:
            detail["neutral_imputation"].append({
                "component":key,"weight_points":round(weight*(1-verified),2),
                "neutral_rating":2.5,"reason":part["detail"]})
        actual=part["value_0_5"] if part["value_0_5"] is not None else 2.5
        total+=weight*(verified*actual+(1-verified)*2.5)/5
    score=round(min(100,max(0,total)),1)
    if confidence=="LOW":score=min(score,75)
    detail["score"]=score
    return score,detail

def summarize_fundamentals(payload,price,sector_key,as_of=None):
    fin=extract_sec_annual(payload)
    score,detail=assess_fundamental(fin,price,sector_key,as_of=as_of)
    fin["scoring_status"]="verified" if score is not None else "unscored"
    fin["score_detail"]=detail
    fin["trailing_fy_pe"]=detail.get("valuation_context",{}).get("pe_fy")
    return fin,score
