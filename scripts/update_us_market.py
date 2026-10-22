#!/usr/bin/env python3
"""US index constituent / daily OHLC / optional SEC fundamentals builder.

Preserve KR production data. Treat missing or stale market data as unavailable.
Fundamental = audited/SEC XBRL fiscal data only; no synthetic earnings or targets.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from update_data import technicals

ET = ZoneInfo("America/New_York")
NOW = datetime.now(ET)
OUT = ROOT / "data" / "us-market.json"
SECTORS = ROOT / "config" / "fundamental_themes.json"
MAX_WORKERS = min(16, max(2, int(os.getenv("US_WORKERS", "8"))))
SEC_UA = os.getenv("US_SEC_USER_AGENT", "").strip()
SEC_LIMIT = min(250, max(0, int(os.getenv("US_SEC_LIMIT", "80"))))
MAX_CHARTS = min(1000, max(1, int(os.getenv("US_MAX_CHARTS", "700"))))
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "MYStockRoom/1.0 (public market data dashboard; https://github.com/inthezeka/stock-research-auto)",
                     "Accept-Language": "en-US,en;q=0.8"})
PAGES = {
    "SP500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "NASDAQ100": "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
    "DOW30": "https://en.wikipedia.org/wiki/List_of_Dow_Jones_Industrial_Average_companies",
}
SEED = [
    ("NVDA","NVIDIA","Information Technology","Semiconductors",["NASDAQ100","SP500"]),
    ("AAPL","Apple","Information Technology","Technology Hardware",["NASDAQ100","SP500"]),
    ("MSFT","Microsoft","Information Technology","Systems Software",["NASDAQ100","SP500"]),
    ("AMZN","Amazon","Consumer Discretionary","Broadline Retail",["NASDAQ100","SP500"]),
    ("AVGO","Broadcom","Information Technology","Semiconductors",["NASDAQ100","SP500"]),
    ("GOOGL","Alphabet Class A","Communication Services","Interactive Media & Services",["NASDAQ100","SP500","DOW30"]),
    ("GOOG","Alphabet Class C","Communication Services","Interactive Media & Services",["NASDAQ100","SP500"]),
    ("META","Meta Platforms","Communication Services","Interactive Media & Services",["NASDAQ100","SP500"]),
    ("MU","Micron Technology","Information Technology","Semiconductors",["NASDAQ100","SP500"]),
    ("TSLA","Tesla","Consumer Discretionary","Automobile Manufacturers",["NASDAQ100","SP500"]),
]
COLS = {"SP500": ("Symbol", "Security"), "NASDAQ100": ("Ticker", "Company"), "DOW30": ("Symbol", "Company")}


def clean(value):
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", "", str(value or ""))).strip()


def symbol(value):
    code = clean(value).upper().replace(".", "-")
    return code if re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?", code) else None



def fetch_sp500_backup():
    """Maintained open CSV of 500 index members, used only if Wikipedia is inaccessible."""
    url="https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
    r=HTTP.get(url,timeout=17);r.raise_for_status()
    found={}
    for row in csv.DictReader(io.StringIO(r.text)):
        ticker=symbol(row.get("Symbol"))
        if ticker:
            found[ticker]={"ticker":ticker,"name":clean(row.get("Security")),
                           "gics":clean(row.get("GICS Sector")),
                           "subindustry":clean(row.get("GICS Sub-Industry")),
                           "indices":["SP500"]}
    if len(found)<480:raise RuntimeError("Open CSV S&P500 incomplete "+str(len(found)))
    return found


def fetch_constituents(index):
    url = PAGES[index]
    try:
        response = HTTP.get(url, timeout=17)
        response.raise_for_status()
    except Exception:
        if index=="SP500":return fetch_sp500_backup()
        raise
    doc = BeautifulSoup(response.text, "html.parser")
    first, second = COLS[index]
    for table in doc.find_all("table"):
        trs = table.find_all("tr", recursive=True)
        if not trs:
            continue
        headings = [clean(c.get_text(" ", strip=True)) for c in trs[0].find_all(["th", "td"], recursive=False)]
        if first not in headings or second not in headings:
            continue
        h = {name: i for i, name in enumerate(headings)}
        result = {}
        for tr in trs[1:]:
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"], recursive=False)]
            if max(h[first], h[second]) >= len(cells):
                continue
            ticker = symbol(cells[h[first]])
            name = cells[h[second]]
            if not ticker or not name:
                continue
            sector = cells[h["GICS Sector"]] if "GICS Sector" in h and h["GICS Sector"] < len(cells) else (
                cells[h["Sector"]] if "Sector" in h and h["Sector"] < len(cells) else
                cells[h["ICB Industry"]] if "ICB Industry" in h and h["ICB Industry"] < len(cells) else "")
            sub = cells[h["GICS Sub-Industry"]] if "GICS Sub-Industry" in h and h["GICS Sub-Industry"] < len(cells) else (
                cells[h["ICB Subsector"]] if "ICB Subsector" in h and h["ICB Subsector"] < len(cells) else "")
            result[ticker] = {"ticker": ticker, "name": name, "gics": sector, "subindustry": sub, "indices": [index]}
        minimum = {"SP500":480,"NASDAQ100":95,"DOW30":29}[index]
        if len(result) >= minimum:
            return result
    if index=="SP500":return fetch_sp500_backup()
    raise RuntimeError(f"{index}: constituent table missing/incomplete")


def merge_universe(old):
    universe = {}
    failures = {}
    ok = {}
    for idx in ("SP500", "NASDAQ100", "DOW30"):
        try:
            result = fetch_constituents(idx)
            ok[idx] = len(result)
            for ticker, item in result.items():
                if ticker in universe:
                    current = universe[ticker]
                    current["indices"] = sorted(set(current["indices"] + [idx]))
                    if not current["gics"] and item["gics"]:
                        current["gics"] = item["gics"]
                        current["subindustry"] = item["subindustry"]
                else:
                    universe[ticker] = item
        except Exception as exc:
            failures[idx] = str(exc)[:130]
            for item in old.get("items", []):
                if idx in (item.get("indices") or []):
                    ticker = item.get("ticker")
                    if ticker and ticker not in universe:
                        universe[ticker] = {k: item.get(k) for k in ("ticker","name","gics","subindustry","indices")}
                    elif ticker in universe:
                        universe[ticker]["indices"] = sorted(set(universe[ticker]["indices"] + [idx]))
    if not universe:
        # Verified official-index examples; intentionally marked bootstrap / not a full universe.
        universe = {x[0]: dict(ticker=x[0], name=x[1], gics=x[2], subindustry=x[3], indices=x[4]) for x in SEED}
        failures["universe"] = "seed-only: live constituent lists unavailable"
    return universe, ok, failures


def sector_key(s, sub, name):
    t = (s+" "+sub+" "+name).lower()
    def has(*words): return any(w in t for w in words)
    if has("semiconductor equipment", "semiconductor materials", "wafer fabrication equipment"): return "semi_equipment"
    if has("semiconductor", "microprocessor", "memory"): return "semi_memory_ai"
    if has("electronic components", "electronic equipment", "hardware storage", "computer hardware", "technology hardware"): return "it_hardware"
    if has("cybersecurity", "security software"): return "security_quantum"
    if has("cloud", "application software", "systems software", "it consulting", "software"): return "software_cloud_ai"
    if has("interactive media", "interactive home", "social media", "internet content", "search engine"): return "platform"
    if has("telecom", "communication equipment", "wireless", "telecommunication"): return "telecom_network"
    if has("electric utilities", "electrical equipment", "power generation", "renewable electricity", "multi-utilities"): return "power_grid"
    if has("independent power producer", "oil", "gas", "lng", "energy", "renewable", "solar"): return "energy_lng_refining"
    if has("automobile", "automotive", "motor vehicle", "auto parts"): return "auto"
    if has("aerospace", "defense", "defence"): return "defense_space"
    if has("construction", "engineering", "infrastructure", "homebuilding", "building products"): return "construction"
    if has("industrial machinery", "machinery", "industrial equipment", "automation", "robotic"): return "industrial"
    if has("battery", "energy storage"): return "battery_ess"
    if has("pharmaceutical", "biotechnology", "life sciences tools"): return "bio"
    if has("health care equipment", "health care technology", "medical equipment", "medical device"): return "medical_device"
    if has("managed health care", "health care provider", "health care services"): return "medical_device"
    if has("insurance"): return "insurance"
    if has("bank", "capital markets", "financial services", "asset management", "consumer finance", "financial"): return "finance"
    if has("interactive entertainment", "game", "video games"): return "game"
    if has("entertainment", "movies", "broadcasting", "media", "publishing"): return "entertainment"
    if has("airlines", "hotels", "resorts", "leisure", "casinos"): return "travel"
    if has("ground transportation", "air freight", "marine transportation", "cargo", "logistics"): return "shipping_logistics"
    if has("food", "beverages", "tobacco", "packaged", "household products"): return "food"
    if has("cosmetics", "personal care", "beauty"): return "beauty"
    if has("specialty retail", "broadline retail", "distributors", "retail", "consumer discretionary distribution"): return "retail"
    if has("metal", "chemical", "materials", "steel", "gold", "mining", "copper", "paper"): return "materials"
    if has("waste management", "environmental", "water utilities"): return "environment"
    if has("real estate", "reit", "diversified"): return "holding_conglomerate"
    if has("industrial", "capital goods"): return "industrial"
    return {"Information Technology":"software_cloud_ai", "Technology":"software_cloud_ai",
            "Health Care":"medical_device","Consumer Staples":"food","Consumer Discretionary":"retail",
            "Materials":"materials","Industrials":"industrial","Financials":"finance",
            "Communication Services":"platform","Telecommunications":"telecom_network",
            "Utilities":"power_grid","Energy":"energy_lng_refining"}.get(s,"holding_conglomerate")


def yahoo_rows(ticker, period="1y"):
    errors = []
    for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{requests.utils.quote(ticker,safe='')}?range={period}&interval=1d&includePrePost=false"
        try:
            r = HTTP.get(url, timeout=10, headers={"Accept": "application/json"})
            r.raise_for_status()
            q = r.json()["chart"]["result"][0]
            stamps = q.get("timestamp") or []
            bars = (q.get("indicators") or {}).get("quote") or []
            if not bars: continue
            bar=bars[0]; out = {}
            for i,ts in enumerate(stamps):
                try:
                    close = float(bar["close"][i]); high = float(bar["high"][i]); low = float(bar["low"][i])
                    op = float(bar["open"][i]); vol = int(bar["volume"][i] or 0)
                    if not all(math.isfinite(v) for v in (close,high,low,op)) or close<=0 or not low<=close<=high:continue
                    day = datetime.fromtimestamp(ts, timezone.utc).astimezone(ET).date().isoformat()
                    out[day] = {"date":day,"close":round(close,6),"open":round(op,6),
                                "high":round(high,6),"low":round(low,6),"volume":vol}
                except (ValueError,TypeError,KeyError,IndexError):continue
            rows = sorted(out.values(), key=lambda x:x["date"])
            if len(rows)>=60:return rows, "Yahoo Finance daily OHLC"
        except Exception as e:errors.append(str(e)[:90])
    raise RuntimeError("; ".join(errors) or "insufficient OHLC")


def calculate_flow(rows):
    recent=rows[-5:]
    if len(recent)<5:return {}
    prev=rows[-6:-1]
    obv=sum((1 if cur["close"]>before["close"] else -1 if cur["close"]<before["close"] else 0)*cur["volume"]
            for cur,before in zip(recent,prev))
    return {"volume":sum(int(x["volume"]) for x in recent),
            "obv":obv,"obv_trend":"상승" if obv>0 else "하락" if obv<0 else "횡보"}


def fy_us_gaap(facts, aliases, unit="USD"):
    gaap = facts.get("us-gaap") or {}
    for name in aliases:
        blocks=gaap.get(name) or {}
        vals=(blocks.get("units") or {}).get(unit) or []
        options={}
        for row in vals:
            if row.get("form") not in ("10-K","10-K/A") or row.get("fp")!="FY":
                continue
            if not row.get("start") or not row.get("end") or row.get("val") is None:
                continue
            try:
                if not 300 <= (datetime.fromisoformat(row["end"])-datetime.fromisoformat(row["start"])).days <= 380:continue
                fy=int(row.get("fy"))
                if fy<2020:continue
                end=row["end"]
                if end not in options or row.get("filed","")>options[end].get("filed",""):
                    options[end]=row
            except (TypeError,ValueError):continue
        result=sorted(options.values(),key=lambda r:r["end"],reverse=True)
        if result and result[0].get("val") is not None:return result
    return []


def balance_us_gaap(facts, aliases):
    gaap=facts.get("us-gaap") or {}
    for name in aliases:
        vals=(gaap.get(name,{}).get("units") or {}).get("USD") or []
        r=[x for x in vals if x.get("form") in ("10-K","10-K/A") and x.get("val") is not None and x.get("end")]
        if r:
            r.sort(key=lambda x:(x.get("end",""),x.get("filed","")),reverse=True)
            return float(r[0]["val"])
    return None


def fundamental_from_sec(payload, close):
    fact=(payload.get("facts") or {})
    gaap=fact.get("us-gaap") or {}
    rev=fy_us_gaap(fact,["RevenueFromContractWithCustomerExcludingAssessedTax","Revenues",
                            "SalesRevenueNet","RevenueFromContractWithCustomerIncludingAssessedTax","SalesRevenueGoodsNet"])
    op=fy_us_gaap(fact,["OperatingIncomeLoss"])
    eps=fy_us_gaap(fact,["EarningsPerShareDiluted","EarningsPerShareBasic"],"USD/shares")
    net=fy_us_gaap(fact,["NetIncomeLoss","ProfitLoss"])
    if len(rev)<2 or not op or len(eps)<2 or not net:return None, None
    last_date=rev[0]["end"]
    # Never combine different fiscal-year periods.
    same=lambda rows:next((float(x["val"]) for x in rows if x["end"]==last_date),None)
    operating=same(op); ni=same(net); eps_new=same(eps)
    if operating is None or ni is None or eps_new is None:return None, None
    cash=balance_us_gaap(fact,["CashAndCashEquivalentsAtCarryingValue","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"])
    debt=balance_us_gaap(fact,["LongTermDebtNoncurrent","LongTermDebt","LongTermDebtCurrent"])
    revenue=float(rev[0]["val"]); prior=float(rev[1]["val"])
    if revenue<=0 or prior<=0:return None,None
    growth=(revenue/prior-1)*100;opm=operating/revenue*100; netmargin=ni/revenue*100
    pe=close/eps_new if close and eps_new>0 else None
    score=None
    # 25 growth + 25 profitability + 20 FY earnings valuation + 15 net margin
    # + 15 financial stability; mark missing input as unscored.
    if pe and math.isfinite(pe) and cash is not None and debt is not None and revenue>0:
        growth_pts=max(0,min(25,12.5+(growth-8)*.55))
        profit_pts=max(0,min(25,opm*.65))
        value_pts=max(0,min(20,20-(pe-12)*.38))
        net_pts=max(0,min(15,netmargin*.48))
        safe_pts=15 if debt<=0 else max(0,min(15,7.5+5*math.log10(max(.001,cash/debt))))
        score=round(growth_pts+profit_pts+value_pts+net_pts+safe_pts,1)
    return {"fy_end":last_date,"revenue":revenue,"revenue_growth":round(growth,2),
            "opm":round(opm,2),"net_margin":round(netmargin,2),"eps_fy":eps_new,
            "trailing_fy_pe":round(pe,2) if pe and math.isfinite(pe) else None,
            "cash":cash,"debt":debt,"source":"SEC EDGAR XBRL Company Facts",
            "scoring_status":"verified" if score is not None else "incomplete"},score


def sec_data(tickers, prices, old):
    if not SEC_UA:
        return {},{"status":"not_configured","note":"Set US_SEC_USER_AGENT with organization and contact email (SEC fair access)."}
    sec=requests.Session();sec.headers.update({"User-Agent":SEC_UA,"Accept":"application/json"})
    try:
        response=sec.get("https://www.sec.gov/files/company_tickers.json",timeout=15);response.raise_for_status()
        lookup={str(x.get("ticker","")).upper().replace(".","-"):int(x["cik_str"])
                for x in response.json().values()}
    except Exception as e:return {},{"status":"cik_list_error","error":str(e)[:140]}
    out={};attempted=0;errors=0
    priorities=sorted((t for t in tickers if prices.get(t,{}).get("close")),
                      key=lambda t:(0 if "DOW30" in tickers[t]["indices"] else 1,
                                    0 if "NASDAQ100" in tickers[t]["indices"] else 1,t))
    for ticker in priorities[:SEC_LIMIT]:
        cik=lookup.get(ticker)
        if not cik:continue
        attempted+=1
        try:
            rr=sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",timeout=14)
            rr.raise_for_status()
            f,score=fundamental_from_sec(rr.json(),prices[ticker].get("close"))
            if f:out[ticker]={"financials":f,"fundamental_score":score}
        except Exception:errors+=1
        time.sleep(.14)
    return out,{"status":"complete" if attempted and not errors else "partial",
                "attempted":attempted,"matched":len(out),"errors":errors}


def main():
    previous={}
    try:previous=json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:pass
    previous_items={x.get("ticker"):x for x in previous.get("items") or []}
    universe,coverage,universe_errors=merge_universe(previous)
    sector_labels={s["key"]:s["label"] for s in json.loads(SECTORS.read_text(encoding="utf-8"))["sectors"]}
    results={};errors=[]
    candidates=list(universe)[:MAX_CHARTS]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures={executor.submit(yahoo_rows,code):code for code in candidates}
        for fut in as_completed(futures):
            ticker=futures[fut]
            try:
                rows,source=fut.result()
                t=technicals(rows)
                result={"price_date":rows[-1]["date"],"close":rows[-1]["close"],
                        "price_source":source,"chart":[{"date":x["date"],"close":x["close"]} for x in rows[-65:]],
                        "return5d":round((rows[-1]["close"]/rows[-6]["close"]-1)*100,2) if len(rows)>6 else None,
                        "return20d":t.get("r20"),"return60d":t.get("r60"),
                        "trading_score":(t.get("trading_score") or {}).get("total"),
                        "trend":(t.get("trading_score") or {}).get("trend"),"rsi":t.get("rsi"),
                        "m20":t.get("m20"),"m60":t.get("m60"),
                        "volume_ratio":t.get("volume_ratio"),"flow5d":calculate_flow(rows),
                        "data_confidence":"HIGH" if len(rows)>=120 else "LOW"}
                p=result["close"];m20=result["m20"];m60=result["m60"];rsi=result["rsi"] or 0
                strong=m20 and m60 and p>m20>m60 and 45<=rsi<=75
                if strong and p>=float(t.get("high20") or 1e20) and rsi<72:quality="S"
                elif strong and p/m20<=1.045:quality="A"
                elif strong:quality="B"
                else:quality="C"
                result["entry_quality"]=quality
                result["trade_reason"]=("Long 가격구조 후보 · ICT 구조 추가 검증 필요" if quality!="C"
                                        else "확인된 Long 가격구조 조건 미충족")
                results[ticker]=result
            except Exception as e:
                errors.append((ticker,str(e)[:90]))
    fresh_dates=Counter(x["price_date"] for x in results.values())
    asof=fresh_dates.most_common(1)[0][0] if fresh_dates else None
    # Never present a stale bar as the current verified close.
    for ticker,item in results.items():
        if item["price_date"]!=asof:
            item["close"]=None
            item["trading_score"]=None
            item["return5d"]=None
            item["return20d"]=None
            item["return60d"]=None
            item["volume_ratio"]=None
            item["flow5d"]={}
            item["data_confidence"]="LOW"
            item["entry_quality"]="C"
            item["stale"]=True
    indices={}
    for key,ticker in (("NASDAQ100","^NDX"),("SP500","^GSPC"),("DOW30","^DJI"),("NASDAQCOMP","^IXIC")):
        try:
            rows,_=yahoo_rows(ticker,"3mo")
            last=rows[-1]
            indices[key]={"date":last["date"],"close":last["close"] if last["date"]==asof else None,
                          "change_pct":round((rows[-1]["close"]/rows[-2]["close"]-1)*100,2) if last["date"]==asof else None}
        except Exception as e:
            indices[key]={"date":None,"close":None,"error":str(e)[:90]}
    # SEC can be enabled separately and runs on a bounded, prioritized subset.
    sec,sec_meta=sec_data(universe,results,previous)
    items=[]
    for ticker,meta in sorted(universe.items()):
        p=results.get(ticker) or {}
        old=previous_items.get(ticker) or {}
        # Reuse previous SEC fiscal facts, but never previous trading-day quote.
        financial=sec.get(ticker,{}).get("financials") or old.get("financials") or {}
        fscore=sec.get(ticker,{}).get("fundamental_score")
        if fscore is None and financial and p.get("close") is not None:
            # A previous FY P/E cannot be carried forward unchanged after quote changes.
            fscore=None
        key=sector_key(meta.get("gics") or "",meta.get("subindustry") or "",meta.get("name") or "")
        items.append({**meta,"sector_key":key,"sector_label":sector_labels.get(key,"기타"),**p,
                      "financials":financial,"fundamental_score":fscore})
    complete_universe=(coverage.get("SP500",0)>=480 and coverage.get("NASDAQ100",0)>=95 and coverage.get("DOW30",0)>=29)
    meta={"as_of":asof,"generated_at":datetime.now(timezone.utc).isoformat(),
          "status":"ok" if complete_universe and asof and len(results)>=max(10,int(len(candidates)*.8)) and
                   sum(v.get("close") is not None for v in results.values())>=max(10,int(len(candidates)*.8)) else "partial",
          "market_timezone":"America/New_York","currency":"USD",
          "universe_total":len(items),"verified_close_count":sum(bool(x.get("close")) for x in items),
          "source_member_counts":coverage,"constituent_errors":universe_errors,
          "quote_failure_count":len(errors),"quote_failure_sample":errors[:12],
          "price_source":"Yahoo Finance unadjusted daily OHLC","sec":sec_meta}
    output={"meta":meta,"indices":indices,
            "sectors":[{"key":k,"label":v} for k,v in sector_labels.items()],
            "items":items}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(output,ensure_ascii=False,separators=(",",":"),allow_nan=False),encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(OUT)
    print(json.dumps({"status":meta["status"],"as_of":asof,"count":len(items),
                      "verified":meta["verified_close_count"],"universe":coverage,
                      "sec":sec_meta,"failures":len(errors)},ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
