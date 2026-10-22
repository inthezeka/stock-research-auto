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
from us_investment import (CONFIG as US_CONFIG, fetch_us_listed, classify_company, classify_themes,
                           aggregate_sector_cycle, aggregate_theme_popularity,
                           aggregate_investment_best5, initial_membership_check)
from us_fundamental import summarize_fundamentals,assess_fundamental

ET = ZoneInfo("America/New_York")
NOW = datetime.now(ET)
OUT = ROOT / "data" / "us-market.json"
SECTORS = ROOT / "config" / "us_investment_sectors.json"
MAX_WORKERS = min(16, max(2, int(os.getenv("US_WORKERS", "8"))))
SEC_UA = os.getenv("US_SEC_USER_AGENT", "").strip()
SEC_LIMIT = min(250, max(0, int(os.getenv("US_SEC_LIMIT", "80"))))
MAX_CHARTS = min(1200, max(50, int(os.getenv("US_MAX_CHARTS", "950"))))
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
        universe = {x[0]: dict(ticker=x[0], name=x[1], gics=x[2], subindustry=x[3], indices=[],
                                  membership_status="SEED_UNVERIFIED") for x in SEED}
        failures["universe"] = "seed-only: live constituent lists unavailable"
    return universe, ok, failures


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


def sec_data(universe, prices, old, classifications):
    """Bounded SEC request queue; rotate towards unscored companies and cache verified FY data."""
    if not SEC_UA or not re.search(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", SEC_UA):
        return {}, {"status":"not_configured","note":"Set GitHub secret US_SEC_USER_AGENT to organization and reachable contact email; SEC public API has no key."}
    sec=requests.Session()
    sec.headers.update({"User-Agent":SEC_UA,"Accept":"application/json","Accept-Encoding":"gzip, deflate"})
    try:
        r=sec.get("https://www.sec.gov/files/company_tickers.json",timeout=22)
        r.raise_for_status()
        lookup={str(x.get("ticker","")).upper().replace(".","-"):int(x["cik_str"])
                for x in r.json().values()}
    except Exception as e:
        return {},{"status":"cik_list_error","error":str(e)[:180]}
    older={x.get("ticker"):x for x in old.get("items") or []}
    def priority(ticker):
        prior=(older.get(ticker) or {}).get("financials") or {}
        missing=0 if prior.get("status")!="EXTRACTED" else 1
        # refresh old FY disclosures less urgently than previously unscored symbols
        last=str(prior.get("sec_collected_at") or "")
        tier=0 if "NASDAQ100" in (universe[ticker].get("indices") or []) or "DOW30" in (universe[ticker].get("indices") or []) else 1
        return (missing,tier,last,ticker)
    tickers=sorted((t for t in universe if prices.get(t,{}).get("close") and t in lookup),key=priority)
    out={};attempted=0;failed=0;unsupported=0
    for ticker in tickers[:SEC_LIMIT]:
        cik=lookup.get(ticker)
        if cik is None:continue
        attempted+=1
        try:
            # SEC submissions SIC + 20-F filing status are distinct from official GICS.
            submissions=None
            try:
                sub=sec.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",timeout=18)
                sub.raise_for_status()
                submissions=sub.json()
                desc=str(submissions.get("sicDescription") or "")
                sic=submissions.get("sic")
                if desc or sic:
                    universe[ticker]["sic_description"]=desc
                    universe[ticker]["sic"]=sic
                    updated=classify_company(universe[ticker])
                    if classifications[ticker].get("sector_key") is None or (
                        classifications[ticker].get("classification_method")=="PUBLIC_SECTOR_TEXT_FALLBACK"):
                        classifications[ticker]=updated
                recent_forms=((submissions.get("filings") or {}).get("recent") or {}).get("form") or []
                if any(form in ("20-F","6-K","20-F/A") for form in recent_forms[:150]):
                    universe[ticker]["foreign_issuer"]=True
                    universe[ticker]["security_type"]="FOREIGN_ISSUER"
            except Exception:
                # SIC metadata is optional. Do not invent it when submissions unavailable.
                pass
            time.sleep(.25)
            rr=sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",timeout=20)
            rr.raise_for_status()
            sector=classifications[ticker].get("sector_key")
            if not sector:
                unsupported+=1
                continue
            fin,score=summarize_fundamentals(rr.json(),prices[ticker].get("close"),sector,as_of=prices[ticker]["price_date"])
            fin["sec_collected_at"]=datetime.now(timezone.utc).date().isoformat()
            if fin.get("status")=="IFRS_OR_UNSUPPORTED":unsupported+=1
            out[ticker]={"financials":fin,"fundamental_score":score}
        except Exception as e:
            failed+=1
        finally:
            # Conservative < 5 requests/second, including the CIK lookup.
            time.sleep(.26)
    return out, {"status":"complete" if attempted and failed==0 else "partial",
                 "attempted":attempted,"matched":len(out),"errors":failed,
                 "ifrs_or_unsupported":unsupported,
                 "quota":SEC_LIMIT,
                 "note":"Historical SEC annual FY values only; no licensed analyst consensus."}


def main():
    previous={}
    try:previous=json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:pass
    previous_items={x.get("ticker"):x for x in previous.get("items") or []}
    universe,coverage,universe_errors=merge_universe(previous)
    # Full exchange directory is the candidate universe; only a bounded rotating sample is priced daily.
    listings,listing_coverage,listing_errors=fetch_us_listed(HTTP)
    for ticker,row in listings.items():
        if ticker in universe:
            prior=universe[ticker]
            for k in ("exchange","security_type","foreign_issuer","financial_status","listing_source"):
                prior[k]=row.get(k)
        else:
            universe[ticker]=row
    # Preserve earlier candidate identities if a directory has a transient failure.
    if listing_errors and previous.get("items"):
        for old in previous["items"]:
            ticker=old.get("ticker")
            if ticker and ticker not in universe:
                universe[ticker]={k:old.get(k) for k in (
                    "ticker","name","gics","subindustry","indices","exchange","security_type",
                    "foreign_issuer","financial_status","listing_source")}
    sector_labels={s["key"]:s["label"] for s in US_CONFIG["sectors"]}
    classifications={ticker:classify_company(item) for ticker,item in universe.items()}
    results={};errors=[]
    # Index securities always receive first priority; additional listed names are scanned in rotating batches.
    indexed=sorted((x for x in universe if universe[x].get("indices")),
                   key=lambda x:(0 if "DOW30" in universe[x]["indices"] else 1,
                                 0 if "NASDAQ100" in universe[x]["indices"] else 1,x))
    extra=sorted(x for x in universe if x not in set(indexed))
    remaining=max(0,MAX_CHARTS-len(indexed))
    if remaining and extra:
        # A rolling window keeps index coverage while eventually visiting non-index exchange names.
        start=(NOW.date().toordinal()*max(1,remaining))%len(extra)
        rotated=extra[start:]+extra[:start]
        candidates=indexed+rotated[:remaining]
    else:
        candidates=indexed[:MAX_CHARTS]

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
    sec,sec_meta=sec_data(universe,results,previous,classifications)
    items=[]
    for ticker,company in sorted(universe.items()):
        price_info=results.get(ticker) or {}
        historic=previous_items.get(ticker) or {}
        prior_fin=(historic.get("financials") or {})
        financial=sec.get(ticker,{}).get("financials") or prior_fin
        classified=classifications[ticker]
        key=classified.get("sector_key")
        # SEC history can be cached, but score must be recalculated against today's price/sector.
        if key and price_info.get("close") is not None and financial.get("status")=="EXTRACTED":
            score,score_detail=assess_fundamental(financial,price_info["close"],key,as_of=asof)
            financial={**financial,"scoring_status":"verified" if score is not None else "unscored",
                       "score_detail":score_detail,
                       "trailing_fy_pe":score_detail.get("valuation_context",{}).get("pe_fy")}
        else:
            score=None
            score_detail={"confidence":"INSUFFICIENT","evidence_coverage":0,
                          "reason":"SEC FY or current verified closing price unavailable"}
        themes=classify_themes(key,ticker=ticker)
        items.append({**company,**classified,**themes,**price_info,
                      "financials":financial if financial else {},
                      "fundamental_score":score,
                      "fundamental_confidence":score_detail["confidence"],
                      "fundamental_coverage":score_detail.get("evidence_coverage",0),
                      "fundamental_model":(financial.get("score_detail") or {}).get("model") if financial else None,
                      "investment_score":None,
                      "score_profile":(financial.get("score_detail") or {}).get("profile") if financial else None})
    sector_cycles=aggregate_sector_cycle(items)
    theme_popularity=aggregate_theme_popularity(items)
    investment_best5=aggregate_investment_best5(items)
    membership=initial_membership_check(items)
    complete_universe=(coverage.get("SP500",0)>=480 and coverage.get("NASDAQ100",0)>=95 and coverage.get("DOW30",0)>=29)
    universe_price_coverage=sum(bool(x.get("close")) for x in items)/max(1,len(items))
    common_candidate_count=len(listings)
    scored_count=sum(x.get("fundamental_score") is not None for x in items)
    mapped_count=sum(x.get("sector_key") in sector_labels for x in items)
    meta={"as_of":asof,"generated_at":datetime.now(timezone.utc).isoformat(),
          "status":"ok" if complete_universe and asof and len(results)>=max(10,int(len(candidates)*.8)) and
                   universe_price_coverage>=.80 and scored_count/max(1,len(items))>=.70 else "partial",
          "market_timezone":"America/New_York","currency":"USD",
          "universe_total":len(items),"verified_close_count":sum(bool(x.get("close")) for x in items),
          "source_member_counts":coverage,"constituent_errors":universe_errors,
          "exchange_candidate_count":common_candidate_count,
          "directory_status":"ok" if len(listing_coverage)==2 and common_candidate_count>3000 else "partial",
          "exchange_counts":listing_coverage,"directory_errors":listing_errors,
          "coverage_scope":"US index 3 plus exchange-listed common-stock candidates; nightly quote/SEC sampling",
          "priced_universe_fraction":round(universe_price_coverage,4),
          "screened_market_status":"partial" if universe_price_coverage<.8 else "broad_quote_coverage",
          "full_market_scoring_status":"partial" if scored_count/max(1,len(items))<.70 else "broad_score_coverage",
          "mapped_sector_count":mapped_count,"fundamental_scored_count":scored_count,
          "fundamental_eligible_high_medium":sum(x.get("fundamental_confidence") in ("HIGH","MEDIUM") for x in items),
          "full_us_market_fundamental_rank_verified":False,
          "quote_failure_count":len(errors),"quote_failure_sample":errors[:12],
          "price_source":"Yahoo Finance unadjusted daily OHLC","sec":sec_meta}
    output={"meta":meta,"indices":indices,
            "sectors":[{"key":k,"label":v,"model":US_CONFIG["sectors"][i]["model"],
                        "score_weights":US_CONFIG["sectors"][i]["score_weights"],
                        "kpis":US_CONFIG["sectors"][i]["sector_kpis"]} for i,(k,v) in enumerate(sector_labels.items())],
            "themes":US_CONFIG["themes"],"sector_cycles":sector_cycles,
            "theme_popularity":theme_popularity,"investment_best5":investment_best5,
            "universe_coverage":membership,
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
