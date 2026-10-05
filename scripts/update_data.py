#!/usr/bin/env python3
from __future__ import annotations
import json, math, os, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/watchlist.json").read_text(encoding="utf-8"))
DATA_PATH = ROOT / "data/market-data.json"
PREV = json.loads(DATA_PATH.read_text(encoding="utf-8"))
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.5",
})
TIMEOUT = (3.0, 7.0)


def req(url, attempts=2, headers=None):
    last = None
    for i in range(attempts):
        try:
            r = SESSION.get(url, timeout=TIMEOUT, headers=headers)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i + 1 < attempts:
                time.sleep(0.35 * (i + 1))
    raise last


def n(v):
    try:
        if v is None:
            return None
        x = float(str(v).replace(",", "").replace("%", "").strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def fmt_price(v):
    return f"{int(round(float(v))):,}" if v is not None else "—"


def pct(a, b):
    if not b:
        return None
    return (a / b - 1.0) * 100.0


def signed(v, digits=1):
    return "—" if v is None else f"{v:+.{digits}f}%"


def yahoo_chart(symbol, range_="1y", interval="1d"):
    last = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            url = f"https://{host}/v8/finance/chart/{quote(symbol, safe='')}?range={range_}&interval={interval}&includePrePost=false"
            j = req(url, 1).json()
            res = j["chart"]["result"][0]
            ts = res.get("timestamp") or []
            q = res["indicators"]["quote"][0]
            adj = ((res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
                   or q.get("close") or [])
            rows = []
            for i, t in enumerate(ts):
                c = adj[i] if i < len(adj) else None
                if c is None:
                    continue
                hi = (q.get("high") or [c] * len(ts))[i] or c
                lo = (q.get("low") or [c] * len(ts))[i] or c
                vol = (q.get("volume") or [0] * len(ts))[i] or 0
                rows.append({
                    "date": datetime.fromtimestamp(t, tz=timezone.utc).astimezone(KST).date().isoformat(),
                    "close": float(c), "high": float(hi), "low": float(lo), "volume": float(vol)
                })
            if len(rows) >= 20:
                return rows, "Yahoo Finance"
        except Exception as e:
            last = e
    raise last or RuntimeError(f"No Yahoo rows for {symbol}")


def naver_stock_chart(code, count=320):
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
    root = ET.fromstring(req(url, 1).text)
    rows = []
    for item in root.findall(".//item"):
        bits = (item.attrib.get("data") or "").split("|")
        if len(bits) < 6:
            continue
        d, o, h, l, c, v = bits[:6]
        cv = n(c)
        if cv is None:
            continue
        ds = str(d)
        if len(ds) == 8:
            ds = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        rows.append({
            "date": ds, "close": cv,
            "high": n(h) or cv, "low": n(l) or cv, "volume": n(v) or 0
        })
    if len(rows) < 20:
        raise RuntimeError(f"Naver history insufficient: {code}")
    return rows, "Naver Stock"


def fetch_stock(s):
    try:
        rows, source = naver_stock_chart(s["stock_code"])
    except Exception:
        rows, source = yahoo_chart(s["ticker"])
    tech = technicals(rows)
    return {
        **s,
        "rows": rows,
        "source": source,
        "fresh": True,
        "tech": tech,
    }


def sma(vals, p):
    return statistics.mean(vals[-p:]) if len(vals) >= p else None


def ema_series(vals, p):
    if not vals:
        return []
    a = 2 / (p + 1)
    out = [vals[0]]
    for x in vals[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def rsi(vals, p=14):
    if len(vals) <= p:
        return None
    gains, losses = [], []
    for a, b in zip(vals[-(p + 1):-1], vals[-p:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = statistics.mean(gains)
    al = statistics.mean(losses)
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def stochastic(rows, p=14):
    if len(rows) < p:
        return (None, None)
    ks = []
    for i in range(max(p - 1, len(rows) - 5), len(rows)):
        w = rows[i - p + 1:i + 1]
        lo = min(x["low"] for x in w)
        hi = max(x["high"] for x in w)
        k = 50.0 if hi == lo else (rows[i]["close"] - lo) / (hi - lo) * 100
        ks.append(k)
    k = ks[-1]
    d = statistics.mean(ks[-3:]) if len(ks) >= 3 else k
    return k, d


def quantile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] * (hi - pos) + s[hi] * (pos - lo)


def technicals(rows):
    closes = [x["close"] for x in rows]
    price = closes[-1]
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    macd_series = [a - b for a, b in zip(e12, e26)]
    sig = ema_series(macd_series, 9)
    sk, sd = stochastic(rows)
    last60 = closes[-60:] if len(closes) >= 60 else closes
    support = quantile(last60, 0.20)
    resistance = quantile(last60, 0.80)
    rv = rsi(closes, 14)
    m5, m20, m60 = sma(closes, 5), sma(closes, 20), sma(closes, 60)
    if m5 and m20 and m60 and m5 > m20 > m60:
        state = "상승 추세"
    elif m5 and m20 and m60 and m5 < m20 < m60:
        state = "하락 추세"
    else:
        state = "관망"
    return {
        "price": price,
        "date": rows[-1]["date"],
        "r20": pct(price, closes[-21]) if len(closes) >= 21 else None,
        "r60": pct(price, closes[-61]) if len(closes) >= 61 else None,
        "rsi": rv, "stoch_k": sk, "stoch_d": sd,
        "macd": macd_series[-1] if macd_series else None,
        "macd_signal": sig[-1] if sig else None,
        "macd_hist": (macd_series[-1] - sig[-1]) if macd_series and sig else None,
        "m5": m5, "m20": m20, "m60": m60,
        "support": support, "resistance": resistance,
        "high52": max(closes[-252:]) if closes else None,
        "state": state,
    }


def market_series():
    syms = CFG.get("settings", {}).get("market_symbols", {})
    out, errors = {}, []
    for key, symbol in syms.items():
        try:
            rows, source = yahoo_chart(symbol)
            vals = [x["close"] for x in rows]
            out[key] = {
                "value": vals[-1],
                "change_pct": pct(vals[-1], vals[-2]) if len(vals) > 1 else None,
                "date": rows[-1]["date"],
                "fresh": True,
                "source": source,
                "rows": rows,
            }
        except Exception as e:
            errors.append(f"{key}: {e}")
            out[key] = {"fresh": False}
    return out, errors



def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def percentile_rank(values, current):
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    if current is None or len(vals) < 10:
        return 50.0
    cur = float(current)
    return 100.0 * sum(1 for x in vals if x <= cur) / len(vals)


def rolling_returns(closes, period):
    out = []
    for i in range(period, len(closes)):
        v = pct(closes[i], closes[i - period])
        if v is not None:
            out.append(v)
    return out


def daily_returns(closes):
    out = []
    for i in range(1, len(closes)):
        v = pct(closes[i], closes[i - 1])
        if v is not None:
            out.append(v)
    return out


def rolling_realized_vol(closes, window=20):
    rets = daily_returns(closes)
    out = []
    for i in range(window, len(rets) + 1):
        w = rets[i - window:i]
        if len(w) == window:
            out.append(statistics.pstdev(w) * math.sqrt(252))
    return out


def sentiment_label(score):
    if score is None:
        return "데이터 확인 중"
    s = float(score)
    if s < 25:
        return "극단적 공포"
    if s < 45:
        return "공포"
    if s <= 55:
        return "중립"
    if s < 75:
        return "탐욕"
    return "극단적 탐욕"


def fetch_us_sentiment(previous=None):
    """CNN Fear & Greed primary, public CNN-derived mirror fallback."""
    errors = []
    try:
        headers = {
            "User-Agent": SESSION.headers.get("User-Agent"),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://edition.cnn.com/markets/fear-and-greed",
        }
        j = req("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", 2, headers=headers).json()
        fg = j.get("fear_and_greed") or {}
        score = n(fg.get("score"))
        if score is None:
            raise RuntimeError("CNN score missing")
        return {
            "available": True,
            "fresh": True,
            "score": round(clamp(score), 1),
            "rating": sentiment_label(score),
            "rating_en": str(fg.get("rating") or ""),
            "previous_close": n(fg.get("previous_close")),
            "previous_1_week": n(fg.get("previous_1_week")),
            "previous_1_month": n(fg.get("previous_1_month")),
            "timestamp": fg.get("timestamp") or NOW.isoformat(),
            "source": "CNN Fear & Greed",
        }
    except Exception as e:
        errors.append(f"CNN: {e}")

    try:
        j = req("https://fearandgreedgraph.com/api/fear-greed", 1).json()
        vals = j.get("values") or []
        dates = j.get("dates") or []
        score = n(vals[-1] if vals else None)
        if score is None:
            raise RuntimeError("mirror score missing")
        return {
            "available": True,
            "fresh": True,
            "score": round(clamp(score), 1),
            "rating": sentiment_label(score),
            "rating_en": "",
            "previous_close": n(vals[-2] if len(vals) > 1 else None),
            "previous_1_week": n(vals[-6] if len(vals) > 5 else None),
            "previous_1_month": n(vals[-22] if len(vals) > 21 else None),
            "timestamp": (dates[-1] if dates else NOW.date().isoformat()),
            "source": "FearAndGreedGraph mirror · CNN-derived",
        }
    except Exception as e:
        errors.append(f"mirror: {e}")

    if previous and n(previous.get("score")) is not None:
        out = dict(previous)
        out["fresh"] = False
        out["fallback_reason"] = " / ".join(errors)[:300]
        return out
    return {
        "available": False,
        "fresh": False,
        "score": None,
        "rating": "데이터 확인 중",
        "source": "CNN Fear & Greed",
        "fallback_reason": " / ".join(errors)[:300],
    }


def compute_korea_sentiment(market):
    """Keyless Korea sentiment score; own calculation [AI].

    Volatility uses KOSPI 20-day realized volatility instead of scraping
    VKOSPI. USD/KRW 20-day momentum is used as a safe-haven/FX proxy.
    Component scores are trailing-percentile normalized.
    """
    try:
        k_rows = (market.get("kospi") or {}).get("rows") or []
        q_rows = (market.get("kosdaq") or {}).get("rows") or []
        fx_rows = (market.get("usdkrw") or {}).get("rows") or []
        kc = [x["close"] for x in k_rows]
        qc = [x["close"] for x in q_rows]
        fc = [x["close"] for x in fx_rows]
        if len(kc) < 80 or len(qc) < 80 or len(fc) < 40:
            raise RuntimeError("insufficient history")

        k_daily = daily_returns(kc)
        q_daily = daily_returns(qc)
        k20 = rolling_returns(kc, 20)
        fx20 = rolling_returns(fc, 20)
        vols = rolling_realized_vol(kc, 20)
        if not (k_daily and q_daily and k20 and fx20 and vols):
            raise RuntimeError("indicator series empty")

        momentum_score = percentile_rank(k_daily, k_daily[-1])
        kosdaq_score = percentile_rank(q_daily, q_daily[-1])
        strength_score = (momentum_score + kosdaq_score) / 2.0
        trend_score = percentile_rank(k20, k20[-1])
        vol_score = 100.0 - percentile_rank(vols, vols[-1])
        fx_score = 100.0 - percentile_rank(fx20, fx20[-1])

        components = [
            {"key": "volatility", "label": "변동성 · 20D RV", "score": round(clamp(vol_score), 1), "weight": 30, "value": f"{vols[-1]:.1f}%"},
            {"key": "momentum", "label": "KOSPI 모멘텀", "score": round(clamp(momentum_score), 1), "weight": 25, "value": signed(k_daily[-1], 2)},
            {"key": "strength", "label": "주가 강도", "score": round(clamp(strength_score), 1), "weight": 15, "value": signed((k_daily[-1] + q_daily[-1]) / 2.0, 2)},
            {"key": "trend", "label": "KOSPI 20D 추세", "score": round(clamp(trend_score), 1), "weight": 15, "value": signed(k20[-1], 2)},
            {"key": "kosdaq", "label": "KOSDAQ 모멘텀", "score": round(clamp(kosdaq_score), 1), "weight": 10, "value": signed(q_daily[-1], 2)},
            {"key": "safe_haven", "label": "안전자산/FX proxy", "score": round(clamp(fx_score), 1), "weight": 5, "value": f"USD/KRW 20D {signed(fx20[-1], 2)}"},
        ]
        score = sum(x["score"] * x["weight"] for x in components) / 100.0
        return {
            "available": True,
            "fresh": True,
            "score": round(clamp(score), 1),
            "rating": sentiment_label(score),
            "timestamp": NOW.isoformat(),
            "source": "KOSPI·KOSDAQ·USD/KRW · 자체 정규화 [AI]",
            "method": "20D 실현변동성 30% + KOSPI 모멘텀 25% + 주가강도 15% + KOSPI 20D 추세 15% + KOSDAQ 10% + FX proxy 5%",
            "components": components,
        }
    except Exception as e:
        return {
            "available": False,
            "fresh": False,
            "score": None,
            "rating": "데이터 확인 중",
            "timestamp": NOW.isoformat(),
            "source": "KOSPI·KOSDAQ·USD/KRW · 자체 정규화 [AI]",
            "error": str(e)[:250],
            "components": [],
        }


def build_sentiment(us, kr):
    us_score = n((us or {}).get("score"))
    kr_score = n((kr or {}).get("score"))
    if us_score is not None and kr_score is not None:
        blended = kr_score * 0.55 + us_score * 0.45
    elif kr_score is not None:
        blended = kr_score
    elif us_score is not None:
        blended = us_score
    else:
        blended = None

    if blended is None:
        modifier = 0
        regime = "NEUTRAL · SENTIMENT DATA CHECK"
        note = "심리지수 연결 상태를 확인 중입니다."
    elif blended >= 80:
        modifier = -3
        regime = "RISK-ON · EXTREME GREED WATCH"
        note = "과열 구간입니다. 신규 추격매수 점수를 낮추고 눌림·확인 매매를 우선합니다. [AI]"
    elif blended >= 70:
        modifier = -1
        regime = "RISK-ON · GREED"
        note = "탐욕 우위입니다. 추세는 우호적이지만 신규 진입은 가격 위치를 더 엄격히 봅니다. [AI]"
    elif blended >= 56:
        modifier = 0
        regime = "SELECTIVE RISK-ON"
        note = "심리는 위험자산 선호 쪽이지만 과열은 아닙니다. 종목별 실적·기술적 위치를 우선합니다. [AI]"
    elif blended >= 45:
        modifier = 0
        regime = "NEUTRAL · SELECTIVE"
        note = "심리가 중립권입니다. 시장보다 종목별 Catalyst와 Risk/Reward 비중을 높입니다. [AI]"
    elif blended >= 25:
        modifier = -1
        regime = "SELECTIVE RISK-OFF"
        note = "공포 우위입니다. 하락 추세 종목의 신규 진입은 감점하고 확인형 접근을 우선합니다. [AI]"
    else:
        modifier = -1
        regime = "RISK-OFF · EXTREME FEAR"
        note = "극단적 공포입니다. 일괄 매수 가점은 주지 않고 기술적 반등 조건이 확인된 종목만 별도 가점합니다. [AI]"

    gap = None if us_score is None or kr_score is None else kr_score - us_score
    return {
        "us": us,
        "kr": kr,
        "blended_score": None if blended is None else round(clamp(blended), 1),
        "blended_rating": sentiment_label(blended),
        "kr_us_gap": None if gap is None else round(gap, 1),
        "regime": regime,
        "recommendation": {
            "base_modifier": modifier,
            "note": note,
            "profiles": [],
        },
        "updated_at": NOW.isoformat(),
    }


def apply_sentiment_to_profiles(profiles, sentiment, live_by_code):
    rec = sentiment.setdefault("recommendation", {})
    blend = n(sentiment.get("blended_score"))
    base_modifier = int(rec.get("base_modifier") or 0)
    adjustments = []

    for p in profiles[:5]:
        base = int(round(n(p.get("score")) or 0))
        modifier = base_modifier
        rationale = "시장심리 기본 조정"

        if blend is not None and blend < 25:
            live = live_by_code.get(p.get("stock_code"))
            tech = (live or {}).get("tech") or {}
            price, m5, rv = tech.get("price"), tech.get("m5"), tech.get("rsi")
            bounce = (
                price is not None and m5 is not None and rv is not None
                and price > m5 and 30 <= rv <= 55
            )
            if bounce:
                modifier = 2
                rationale = "극단적 공포 + 단기 반등 조건 충족"
            else:
                modifier = -1
                rationale = "극단적 공포 · 반등 확인 전"

        adjusted = int(round(clamp(base + modifier, 0, 100)))
        p["adjusted_score"] = adjusted
        p["sentiment_modifier"] = modifier
        p.setdefault("text", {})["topPickScore"] = f"{adjusted} / {p.get('rating','')} [AI]"
        adjustments.append({
            "name": p.get("name"),
            "base_score": base,
            "modifier": modifier,
            "adjusted_score": adjusted,
            "reason": rationale,
        })

    for p in profiles:
        txt = p.setdefault("text", {})
        for i, q in enumerate(profiles[:5], 1):
            adj = q.get("adjusted_score", q.get("score"))
            mod = int(q.get("sentiment_modifier") or 0)
            suffix = f" · 심리 {mod:+d} [AI]" if mod else " · 심리 0 [AI]"
            txt[f"score{i}"] = f"{int(adj)} / 100{suffix}"

    rec["profiles"] = adjustments


def update_market_text(d, market):
    t = d.setdefault("text", {})
    latest_dates = []
    def set_market(key, text_key, delta_key, suffix=""):
        x = market.get(key) or {}
        if x.get("value") is None:
            return
        v = x["value"]
        if key == "us10y":
            t[text_key] = f"{v:.1f}%"
        else:
            t[text_key] = f"{int(round(v)):,}"
        cp = x.get("change_pct")
        t[delta_key] = (signed(cp, 2) if cp is not None else "—") + (f" · {x.get('date','')[-5:]}" if x.get("date") else "")
        if x.get("date"):
            latest_dates.append(x["date"])
    set_market("kospi", "kospi", "kospiDelta")
    set_market("kosdaq", "kosdaq", "kosdaqDelta")
    set_market("nasdaq", "nasdaq", "nasdaqDelta")
    set_market("usdkrw", "fx", "fxDelta")
    set_market("us10y", "us10y", "us10yDelta")
    if latest_dates:
        md = max(latest_dates)
        t["sourceMarketDate"] = f"{md} 종가"
        t["sourceKR"] = f"KOSPI {t.get('kospi','—')} · KOSDAQ {t.get('kosdaq','—')} · USD/KRW {t.get('fx','—')}"
        t["sourceUS"] = f"Nasdaq {t.get('nasdaq','—')} · US10Y {t.get('us10y','—')}"
        return md
    return d.get("meta", {}).get("market_date")


def profile_live_update(p, live):
    if not live:
        return
    tech = live["tech"]
    price = tech["price"]
    txt = p.setdefault("text", {})
    inp = p.setdefault("inputs", {})
    inp["currentPrice"] = str(int(round(price)))
    txt["currentPriceText"] = f"{fmt_price(price)}원"

    target = n(inp.get("target2"))
    if target:
        txt["upsideText"] = f"{pct(target, price):+.1f}% [AI]"

    sup, res = tech.get("support"), tech.get("resistance")
    if sup is not None:
        txt["support"] = f"{fmt_price(sup)}원 [AI]"
    if res is not None:
        txt["resistance"] = f"{fmt_price(res)}원 [AI]"

    rv = tech.get("rsi")
    sk, sd = tech.get("stoch_k"), tech.get("stoch_d")
    macd, sig, hist = tech.get("macd"), tech.get("macd_signal"), tech.get("macd_hist")
    m5, m20, m60 = tech.get("m5"), tech.get("m20"), tech.get("m60")

    txt["technicalState"] = tech.get("state") or "관망"
    txt["rsiValue"] = "—" if rv is None else f"{rv:.1f}"
    if rv is None:
        txt["rsiSignal"] = "데이터 확인 중"
    elif rv >= 70:
        txt["rsiSignal"] = "과매수 주의"
    elif rv <= 30:
        txt["rsiSignal"] = "과매도 반등 관찰"
    else:
        txt["rsiSignal"] = "상승 우위" if rv >= 50 else "중립 이하"

    if sk is None or sd is None:
        txt["stochValue"] = "—"
        txt["stochSignal"] = "데이터 확인 중"
    else:
        txt["stochValue"] = f"%K {sk:.1f} / %D {sd:.1f}"
        txt["stochSignal"] = "K>D · 단기 모멘텀 우위" if sk > sd else "K≤D · 단기 모멘텀 확인"

    if macd is None or sig is None:
        txt["macdValue"] = "—"
        txt["macdSignal"] = "데이터 확인 중"
    else:
        txt["macdValue"] = f"MACD {macd:,.0f} / Signal {sig:,.0f}"
        txt["macdSignal"] = "Histogram + · 추세 모멘텀 우위" if (hist or 0) > 0 else "Histogram - · 추세 둔화 확인"

    if m5 is None or m20 is None or m60 is None:
        txt["maValue"] = "—"
        txt["maSignal"] = "데이터 확인 중"
    else:
        txt["maValue"] = f"5D {fmt_price(m5)} / 20D {fmt_price(m20)} / 60D {fmt_price(m60)}"
        txt["maSignal"] = "정배열" if m5 > m20 > m60 else ("역배열" if m5 < m20 < m60 else "혼조 배열")

    r20, r60 = tech.get("r20"), tech.get("r60")
    txt["technicalComment"] = (
        f"현재가 {fmt_price(price)}원, 20일 {signed(r20)}, 60일 {signed(r60)}, RSI14 "
        f"{'—' if rv is None else f'{rv:.1f}'}. 지지 {fmt_price(sup)}원 / 저항 {fmt_price(res)}원을 함께 확인합니다. [AI]"
    )
    txt["sourceMarketDate"] = f"{tech.get('date','')} 종가"

    if sup is not None:
        buy1 = int(round((price * 0.67 + sup * 0.33) / 1000) * 1000)
        buy2 = int(round(sup / 1000) * 1000)
        stop = int(round((sup * 0.95) / 1000) * 1000)
        inp["buy1"], inp["buy2"], inp["stopPrice"] = str(buy1), str(buy2), str(stop)


def update_theme_cards(d, live_by_code):
    cards = d.get("theme_cards") or []
    for card in cards:
        scores = []
        for p in card.get("picks") or []:
            code = str(p.get("ticker", "")).split(".")[0]
            live = live_by_code.get(code)
            if not live:
                continue
            tech = live["tech"]
            p["price"] = tech["price"]
            p["price_label"] = f"{fmt_price(tech['price'])}원"
            p["r20"] = None if tech.get("r20") is None else round(tech["r20"], 2)
            p["r60"] = None if tech.get("r60") is None else round(tech["r60"], 2)
            p["state"] = tech.get("state") or "관망"
            scores.append(float(p.get("score") or 0))
        if scores:
            card["theme_score"] = round(statistics.mean(scores), 1)


def validate_financial_snapshot(d):
    """Validate the financial keys that the HTML/profile renderer actually uses.

    Each profile stores four annual rows as:
    y1/rev1/op1a/opm1/ni1/eps1/yoy1 ... y4/rev4/op4a/opm4/ni4/eps4/yoy4.
    The previous validator incorrectly checked non-existent fy24Revenue-style keys,
    which rejected otherwise valid live market updates before prices could be saved.
    """
    bad = []
    row_suffixes = ("rev", "op", "opm", "ni", "eps", "yoy")
    for p in d.get("stock_profiles") or []:
        txt = p.get("text") or {}
        for i in range(1, 5):
            year_key = f"y{i}"
            yv = str(txt.get(year_key, "")).strip()
            if not yv or "[검증 필요]" in yv:
                bad.append(f"{p.get('name')}:{year_key}")
            for base in row_suffixes:
                k = f"{base}{i}" if base != "op" else f"op{i}a"
                v = str(txt.get(k, "")).strip()
                if not v or "[검증 필요]" in v:
                    bad.append(f"{p.get('name')}:{k}")
    return bad


def main():
    d = PREV
    stocks = CFG.get("stocks") or []
    live_by_code, errors = {}, []

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_stock, s): s for s in stocks}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                live = fut.result()
                live_by_code[s["stock_code"]] = live
            except Exception as e:
                errors.append(f"{s.get('name')}: {e}")

    market, market_errors = market_series()
    errors.extend(market_errors)

    stock_ratio = len(live_by_code) / max(1, len(stocks))
    market_fresh = sum(1 for x in market.values() if x.get("fresh"))
    market_ratio = market_fresh / max(1, len(market))

    min_stock = float(CFG.get("settings", {}).get("stock_fresh_min_ratio", 0.80))
    min_market = float(CFG.get("settings", {}).get("market_fresh_min_ratio", 0.60))
    if stock_ratio < min_stock or market_ratio < min_market:
        print(json.dumps({
            "status": "rejected",
            "reason": "fresh coverage below threshold",
            "stock_fresh_ratio": stock_ratio,
            "market_fresh_ratio": market_ratio,
            "errors": errors[:12],
        }, ensure_ascii=False))
        return 2

    md = update_market_text(d, market)

    previous_sentiment = d.get("sentiment") or {}
    us_sentiment = fetch_us_sentiment(previous_sentiment.get("us"))
    kr_sentiment = compute_korea_sentiment(market)
    sentiment = build_sentiment(us_sentiment, kr_sentiment)
    d["sentiment"] = sentiment
    if sentiment.get("regime"):
        d.setdefault("text", {})["marketRegime"] = sentiment["regime"]

    profiles = d.get("stock_profiles") or []
    for p in profiles:
        profile_live_update(p, live_by_code.get(p.get("stock_code")))

    apply_sentiment_to_profiles(profiles, sentiment, live_by_code)
    update_theme_cards(d, live_by_code)

    # Refresh TOP5 summary cards/bind without touching verified financial snapshot fields.
    bind = d.setdefault("bind", {})
    for i, p in enumerate(profiles[:5], 1):
        live = live_by_code.get(p.get("stock_code"))
        if live:
            bind[f"stock{i}Price"] = fmt_price(live["tech"]["price"])
        target = n((p.get("inputs") or {}).get("target2"))
        if target:
            bind[f"stock{i}Target"] = f"{fmt_price(target)} Cons."

    # Keep initial page synchronized with TOP1 profile.
    if profiles:
        d["text"].update(profiles[0].get("text") or {})
        d["inputs"].update(profiles[0].get("inputs") or {})

    financial_bad = validate_financial_snapshot(d)
    if financial_bad:
        print(json.dumps({"status": "rejected", "reason": "financial snapshot incomplete", "bad": financial_bad[:20]}, ensure_ascii=False))
        return 3

    meta = d.setdefault("meta", {})
    sources = meta.setdefault("sources", [])
    for src in (
        "CNN Fear & Greed · 미국 투자심리",
        "KOSPI·KOSDAQ·USD/KRW 기반 한국 공포탐욕 자체계산 [AI]",
    ):
        if src not in sources:
            sources.append(src)

    meta.update({
        "generated_at": NOW.isoformat(),
        "market_date": md,
        "status": "ok",
        "ui_version": "v12-top5-financials-github",
        "coverage": {
            "stocks_fresh": len(live_by_code),
            "stocks_total": len(stocks),
            "stock_fresh_ratio": round(stock_ratio, 4),
            "market_fresh": market_fresh,
            "market_total": len(market),
            "market_fresh_ratio": round(market_ratio, 4),
        },
        "errors": errors[:20],
        "note": "실시간 가격/기술지표 갱신. TOP5 4개년 실적/컨센서스는 마지막 검증 스냅샷을 보존합니다.",
    })

    tmp = DATA_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(DATA_PATH)

    print(json.dumps({
        "status": "ok",
        "market_date": md,
        "stock_fresh_ratio": round(stock_ratio, 4),
        "market_fresh_ratio": round(market_ratio, 4),
        "errors": errors[:10],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
