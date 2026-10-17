#!/usr/bin/env python3
from __future__ import annotations
import copy, json, math, os, re, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/watchlist.json").read_text(encoding="utf-8"))
DATA_PATH = ROOT / "data/market-data.json"
UNIVERSE_PATH = ROOT / "stock-universe.json"
THEME_CONFIG_PATH = ROOT / "config" / "fundamental_themes.json"
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
                op = (q.get("open") or [c] * len(ts))[i] or c
                hi = (q.get("high") or [c] * len(ts))[i] or c
                lo = (q.get("low") or [c] * len(ts))[i] or c
                vol = (q.get("volume") or [0] * len(ts))[i] or 0
                rows.append({
                    "date": datetime.fromtimestamp(t, tz=timezone.utc).astimezone(KST).date().isoformat(),
                    "open": float(op), "close": float(c), "high": float(hi), "low": float(lo), "volume": float(vol)
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
            "date": ds, "open": n(o) or cv, "close": cv,
            "high": n(h) or cv, "low": n(l) or cv, "volume": n(v) or 0
        })
    if len(rows) < 20:
        raise RuntimeError(f"Naver history insufficient: {code}")
    return rows, "Naver Stock"



def parse_korean_market_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace(" ", "").strip()
    direct = n(text)
    if direct is not None:
        return direct
    total = 0.0
    matched = False
    for pattern, mult in ((r"(-?\d+(?:\.\d+)?)조", 1e12), (r"(-?\d+(?:\.\d+)?)억", 1e8), (r"(-?\d+(?:\.\d+)?)만", 1e4)):
        m = re.search(pattern, text)
        if m:
            total += float(m.group(1)) * mult
            matched = True
    return total if matched else None


def _naver_universe_page(order_type="marketSum", start_idx=0, page_size=5000):
    # Verified current Naver stock-list contract.
    url = (
        "https://stock.naver.com/api/domestic/market/stock/default"
        f"?tradeType=KRX&marketType=ALL&orderType={order_type}"
        f"&startIdx={start_idx}&pageSize={page_size}"
    )
    payload = req(url, 2, headers={"Referer": "https://stock.naver.com/"}).json()
    return payload if isinstance(payload, list) else []


def _list_code(x):
    return str(x.get("itemcode") or x.get("itemCode") or "").strip()


def _list_number(x, keys):
    for key in keys:
        value = n(x.get(key))
        if value is not None:
            return value
    return None


def refresh_stock_universe():
    """Refresh the KOSPI/KOSDAQ search/screening universe every workflow run."""
    base_page = _naver_universe_page("marketSum", 0, 5000)
    if len(base_page) < 1500:
        sample_keys = sorted(list((base_page[0] if base_page else {}).keys()))[:30] if base_page else []
        raise RuntimeError(f"Naver stock universe incomplete: {len(base_page)} keys={sample_keys}")

    # Trading-value and volume rankings use the same all-market endpoint but
    # expose liquidity-oriented fields/order. Merge them back into the base list.
    try:
        amount_page = _naver_universe_page("priceTop", 0, 5000)
    except Exception:
        amount_page = []
    try:
        volume_page = _naver_universe_page("quantTop", 0, 5000)
    except Exception:
        volume_page = []

    amount_map = {}
    amount_rank = {}
    for rank, x in enumerate(amount_page, 1):
        code = _list_code(x)
        if not re.fullmatch(r"\d{6}", code):
            continue
        amount_rank[code] = rank
        value = _list_number(x, (
            "accAmount", "accumulatedTradingValue", "accumulatedTradingAmount",
            "tradingValue", "tradeAmount", "amount", "dealAmount"
        ))
        if value is not None:
            amount_map[code] = value

    volume_map = {}
    volume_rank = {}
    for rank, x in enumerate(volume_page, 1):
        code = _list_code(x)
        if not re.fullmatch(r"\d{6}", code):
            continue
        volume_rank[code] = rank
        value = _list_number(x, (
            "accQuant", "accumulatedTradingVolume", "accumulatedVolume",
            "tradingVolume", "tradeVolume", "quant", "volume"
        ))
        if value is not None:
            volume_map[code] = value

    items = []
    seen = set()
    for x in base_page:
        code = _list_code(x)
        name = str(x.get("itemname") or x.get("itemName") or "").strip()
        if not re.fullmatch(r"\d{6}", code) or not name or code in seen:
            continue

        sosok = str(x.get("sosok") or "").strip()
        market = "KOSPI" if sosok == "0" else ("KOSDAQ" if sosok == "1" else None)
        if market is None:
            continue

        close = _list_number(x, ("nowVal", "nowPrice", "closePrice", "currentPrice"))
        shares = _list_number(x, ("listedStockCnt", "listedStockCount", "listedShares"))
        marketcap = _list_number(x, ("marketSum", "marketCap", "marketValue"))
        if marketcap is None:
            marketcap = parse_korean_market_value(x.get("marketValue"))
        if marketcap is None and shares is not None and close is not None:
            marketcap = shares * close

        volume = volume_map.get(code)
        if volume is None:
            volume = _list_number(x, (
                "accQuant", "accumulatedTradingVolume", "accumulatedVolume",
                "tradingVolume", "volume"
            )) or 0

        turnover = amount_map.get(code)
        if turnover is None:
            turnover = _list_number(x, (
                "accAmount", "accumulatedTradingValue", "accumulatedTradingAmount",
                "tradingValue", "amount"
            )) or 0

        # If the ranking endpoint only supplies ranking order but no explicit
        # trading-value field, close*volume is a conservative liquidity proxy.
        if not turnover and close is not None and volume:
            turnover = close * volume

        items.append({
            "code": code,
            "name": name,
            "market": market,
            "ticker": code + (".KQ" if market == "KOSDAQ" else ".KS"),
            "shares": None if shares is None else int(round(shares)),
            "close": close,
            "marketcap": marketcap,
            "volume": volume,
            "turnover": turnover,
            "trading_value_rank": amount_rank.get(code),
            "volume_rank": volume_rank.get(code),
        })
        seen.add(code)

    if len(items) < 1500:
        raise RuntimeError(f"Naver stock universe parsed incomplete: {len(items)}")

    try:
        as_of = naver_domestic_index_snapshot("KOSPI").get("date") or NOW.date().isoformat()
    except Exception:
        as_of = NOW.date().isoformat()

    nonzero_turnover = sum(1 for x in items if (x.get("turnover") or 0) > 0)
    nonzero_volume = sum(1 for x in items if (x.get("volume") or 0) > 0)
    payload = {
        "as_of": as_of,
        "generated_at": NOW.isoformat(),
        "source": "Naver Stock domestic market API · marketSum + priceTop + quantTop",
        "count": len(items),
        "liquidity_coverage": {
            "turnover_nonzero": nonzero_turnover,
            "volume_nonzero": nonzero_volume,
            "price_top_count": len(amount_page),
            "quant_top_count": len(volume_page),
        },
        "items": sorted(items, key=lambda x: (x["market"], x["name"], x["code"])),
    }
    tmp = UNIVERSE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(UNIVERSE_PATH)
    return payload


    # Guard against silently replacing the full KRX universe with a partial API page.
    if len(items) < 1500:
        raise RuntimeError(f"Naver stock universe incomplete: {len(items)}")

    as_of = max(observed_dates) if observed_dates else NOW.date().isoformat()
    payload = {
        "as_of": as_of,
        "generated_at": NOW.isoformat(),
        "source": "Naver Stock domestic market API · KRX",
        "count": len(items),
        "items": sorted(items, key=lambda x: (x["market"], x["name"], x["code"])),
    }
    tmp = UNIVERSE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(UNIVERSE_PATH)
    return payload

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


def atr(rows, p=14):
    if len(rows) <= p:
        return None
    trs = []
    prev_close = rows[-(p + 1)]["close"]
    for row in rows[-p:]:
        tr = max(
            row["high"] - row["low"],
            abs(row["high"] - prev_close),
            abs(row["low"] - prev_close),
        )
        trs.append(tr)
        prev_close = row["close"]
    return statistics.mean(trs) if trs else None


def moving_average_at(vals, p, offset=0):
    end = len(vals) - offset
    start = end - p
    if start < 0 or end <= 0:
        return None
    return statistics.mean(vals[start:end])


def rsi_series(vals, p=14):
    out = [None] * len(vals)
    for i in range(p, len(vals)):
        out[i] = rsi(vals[: i + 1], p)
    return out


def find_pivots(vals, kind="low", lookback=60, wing=2):
    start = max(wing, len(vals) - lookback)
    out = []
    for i in range(start, len(vals) - wing):
        x = vals[i]
        window = vals[i - wing:i + wing + 1]
        if kind == "low" and x == min(window):
            out.append(i)
        elif kind == "high" and x == max(window):
            out.append(i)
    return out


def detect_divergence(closes, rsi_vals, macd_vals):
    labels = []
    lows = find_pivots(closes, "low")
    highs = find_pivots(closes, "high")
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if closes[b] < closes[a]:
            if rsi_vals[a] is not None and rsi_vals[b] is not None and rsi_vals[b] > rsi_vals[a]:
                labels.append("Bullish Divergence · RSI")
            if macd_vals[b] > macd_vals[a]:
                labels.append("Bullish Divergence · MACD")
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if closes[b] > closes[a]:
            if rsi_vals[a] is not None and rsi_vals[b] is not None and rsi_vals[b] < rsi_vals[a]:
                labels.append("Bearish Divergence · RSI")
            if macd_vals[b] < macd_vals[a]:
                labels.append("Bearish Divergence · MACD")
    return labels


def score_macd(macd, signal, hist, prev_macd, prev_signal, prev_hist):
    if None in (macd, signal, hist, prev_macd, prev_signal, prev_hist):
        return 0, "데이터 확인 필요"
    macd_up = macd > prev_macd
    signal_up = signal > prev_signal
    hist_up = hist > prev_hist
    golden = macd > signal and prev_macd <= prev_signal
    dead = macd < signal and prev_macd >= prev_signal

    if dead and hist < 0 and not hist_up:
        return 3, "Dead Cross · 음수 Histogram 확대"
    if macd < signal and hist < 0 and not hist_up:
        return 7, "Signal 하회 · 하락 모멘텀"
    if macd > signal and macd_up and signal_up and hist > 0 and hist_up:
        if macd >= 0:
            return 29, "0선 위 · 상승 모멘텀 강화"
        return 25, "0선 아래 · Golden 구간 회복"
    if golden and hist_up:
        return (28 if macd >= 0 else 24), "Golden Cross · Histogram 개선"
    if macd > signal and hist_up:
        return 22, "상승 우위 · Histogram 개선"
    if macd_up and hist_up:
        return 18, "상승 전환 가능성"
    if hist < prev_hist:
        return 12, "Histogram 둔화"
    return 15, "방향성 혼조"


def score_ma(price, m5, m20, m60, m120, m20_slope, m60_slope):
    if None in (price, m5, m20, m60):
        return 0, "데이터 확인 필요", None, None, 0
    gap20 = pct(price, m20)
    gap60 = pct(price, m60)
    ordered = m5 > m20 > m60
    reverse = m5 < m20 < m60
    slope20_up = (m20_slope or 0) > 0
    slope60_up = (m60_slope or 0) > 0

    if ordered and slope20_up and slope60_up and gap20 is not None and 1 <= gap20 <= 6:
        base, state = 29, "정배열 · 상승기울기 · 적정 괴리"
    elif ordered and slope20_up:
        base, state = 26, "정배열 · 상승 추세"
    elif price > m20 and slope20_up and gap20 is not None and gap20 <= 8:
        base, state = 24, "MA20 상단 · 추세 개선"
    elif price > m20 and price >= m60:
        base, state = 20, "MA20 위 · MA60 지지권"
    elif reverse and (m20_slope or 0) < 0 and (m60_slope or 0) < 0:
        base, state = 4, "역배열 · 주요 이평 하락"
    elif price < m20 and price < m60:
        base, state = 9, "MA20·60 하회"
    else:
        base, state = 15, "이동평균선 혼조"

    penalty = 0
    if gap20 is not None:
        if gap20 >= 15:
            penalty = 10
        elif gap20 >= 12:
            penalty = 7
        elif gap20 >= 8:
            penalty = 4
        elif gap20 >= 5:
            penalty = 1
    score = int(clamp(base - penalty, 0, 30))
    if penalty:
        state += f" · 괴리과열 -{penalty}"
    return score, state, gap20, gap60, penalty


def score_rsi(rv, prev_rv, price, m20):
    if rv is None:
        return 0, "데이터 확인 필요"
    rising = prev_rv is not None and rv > prev_rv
    if 50 <= rv <= 65:
        score = 20 if rising else 18
        state = "건강한 상승구간"
    elif 40 <= rv < 50:
        score = 17 if rising else 14
        state = "50선 돌파 시도" if rising else "중립 하단"
    elif 30 <= rv < 40:
        score = 13 if rising else 9
        state = "과매도 탈출" if rising else "약세 지속 확인"
    elif 65 < rv <= 70:
        score, state = 10, "상승 강함 · 과열 주의"
    elif 70 < rv < 80:
        score, state = 6, "과매수 영역"
    elif rv >= 80:
        score, state = 2, "심한 과열"
    else:
        if rising and m20 is not None and price > m20:
            score, state = 12, "과매도 반등 확인"
        else:
            score, state = 5, "과매도 · 하락추세 여부 확인"
    if prev_rv is not None and rv - prev_rv <= -6:
        score = max(0, score - 2)
        state += " · RSI 급락"
    return score, state


def score_stochastic(k, d, prev_k, prev_d):
    if None in (k, d, prev_k, prev_d):
        return 0, "데이터 확인 필요"
    golden = k > d and prev_k <= prev_d
    dead = k < d and prev_k >= prev_d
    if 20 <= k <= 50 and golden:
        return 20, "20~50 Golden Cross"
    if k < 20 and golden:
        return 16, "과매도 Golden Cross"
    if 50 < k <= 70 and k > d:
        return 13, "50~70 상승 우위"
    if 70 < k <= 80 and k > d:
        return 10, "단기 과열 시작"
    if k > 80 and dead:
        return 2, "과매수 Dead Cross"
    if k > 80:
        return 6, "과매수 영역"
    if golden:
        return 15, "Golden Cross"
    if dead:
        return 5, "Dead Cross"
    if k > d:
        return 12, "K>D · 단기 우위"
    return 8, "방향 확인 필요"


def trading_grade(total):
    if total >= 90:
        return "S", "Strong Buy"
    if total >= 80:
        return "A", "Buy"
    if total >= 70:
        return "B", "Positive"
    if total >= 60:
        return "C", "Neutral"
    if total >= 50:
        return "D", "Caution"
    if total >= 40:
        return "E", "Weak"
    return "F", "Avoid"


def format_amount(v):
    if v is None:
        return "—"
    av = abs(v)
    if av >= 1_000_000_000_000:
        return f"{v/1_000_000_000_000:.2f}조원"
    if av >= 100_000_000:
        return f"{v/100_000_000:.0f}억원"
    return f"{v:,.0f}원"


def price_strategy(tech):
    price = tech.get("price")
    m20, m60 = tech.get("m20"), tech.get("m60")
    support, resistance = tech.get("support"), tech.get("resistance")
    low20, high20, high52, atr14 = tech.get("low20"), tech.get("high20"), tech.get("high52"), tech.get("atr14")
    if price is None:
        return {}

    below = []
    for value, reason in (
        (m20, "MA20"),
        (support, "지지선"),
        (m60, "MA60"),
        (low20, "직전 20일 저점"),
    ):
        if value is not None and value < price:
            below.append((value, reason))
    below.sort(key=lambda x: x[0], reverse=True)

    def uniq_pick(index):
        unique = []
        seen = set()
        for value, reason in below:
            key = round(value, -2)
            if key in seen:
                continue
            seen.add(key)
            unique.append((value, reason))
        return unique[index] if len(unique) > index else (None, "확인 필요")

    entry1, entry1_reason = uniq_pick(0)
    entry2, entry2_reason = uniq_pick(1)
    add, add_reason = uniq_pick(2)

    above = []
    for value, reason in (
        (resistance, "저항선"),
        (high20, "직전 20일 고점"),
        (high52, "52주 고점"),
    ):
        if value is not None and value > price:
            above.append((value, reason))
    above.sort(key=lambda x: x[0])
    target1, target1_reason = (above[0] if above else (None, "확인 필요"))
    target2, target2_reason = (above[1] if len(above) > 1 else (None, "확인 필요"))

    if target1 is None and atr14 is not None:
        target1, target1_reason = price + 1.5 * atr14, "ATR 1.5배"
    if target2 is None and atr14 is not None:
        target2, target2_reason = price + 3.0 * atr14, "ATR 3배"

    base_stop = support if support is not None else (m60 if m60 is not None else low20)
    stop = None
    stop_reason = "확인 필요"
    if base_stop is not None:
        if atr14 is not None:
            stop = max(0, base_stop - 0.5 * atr14)
            stop_reason = "지지 기준 - 0.5 ATR"
        else:
            stop = base_stop
            stop_reason = "지지선"

    return {
        "current": {"price": price, "reason": "현재 종가"},
        "entry1": {"price": entry1, "reason": entry1_reason},
        "entry2": {"price": entry2, "reason": entry2_reason},
        "add": {"price": add, "reason": add_reason},
        "target1": {"price": target1, "reason": target1_reason},
        "target2": {"price": target2, "reason": target2_reason},
        "stop": {"price": stop, "reason": stop_reason},
    }


def technicals(rows):
    closes = [x["close"] for x in rows]
    price = closes[-1]
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    macd_series = [a - b for a, b in zip(e12, e26)]
    sig_series = ema_series(macd_series, 9)
    hist_series = [m - s for m, s in zip(macd_series, sig_series)]

    sk, sd = stochastic(rows)
    psk, psd = stochastic(rows[:-1]) if len(rows) > 15 else (None, None)
    rv = rsi(closes, 14)
    prev_rv = rsi(closes[:-1], 14) if len(closes) > 15 else None
    rsi_vals = rsi_series(closes, 14)

    last60 = closes[-60:] if len(closes) >= 60 else closes
    support = quantile(last60, 0.20)
    resistance = quantile(last60, 0.80)
    m5, m20, m60, m120 = sma(closes, 5), sma(closes, 20), sma(closes, 60), sma(closes, 120)
    m20_prev = moving_average_at(closes, 20, 5)
    m60_prev = moving_average_at(closes, 60, 5)
    m20_slope = None if m20 is None or m20_prev is None else m20 - m20_prev
    m60_slope = None if m60 is None or m60_prev is None else m60 - m60_prev

    macd = macd_series[-1] if macd_series else None
    signal = sig_series[-1] if sig_series else None
    hist = hist_series[-1] if hist_series else None
    prev_macd = macd_series[-2] if len(macd_series) > 1 else None
    prev_signal = sig_series[-2] if len(sig_series) > 1 else None
    prev_hist = hist_series[-2] if len(hist_series) > 1 else None

    macd_score, macd_state = score_macd(macd, signal, hist, prev_macd, prev_signal, prev_hist)
    ma_score, ma_state, gap20, gap60, heat_penalty = score_ma(price, m5, m20, m60, m120, m20_slope, m60_slope)
    rsi_score, rsi_state = score_rsi(rv, prev_rv, price, m20)
    stoch_score, stoch_state = score_stochastic(sk, sd, psk, psd)
    total = macd_score + ma_score + rsi_score + stoch_score
    grade, grade_label = trading_grade(total)

    ordered = all(x is not None for x in (m5, m20, m60)) and m5 > m20 > m60
    reverse = all(x is not None for x in (m5, m20, m60)) and m5 < m20 < m60
    if ordered and (m20_slope or 0) > 0:
        trend = "상승"
    elif m20 is not None and price > m20 and (m20_slope or 0) > 0:
        trend = "상승초기"
    elif reverse and (m20_slope or 0) < 0:
        trend = "하락"
    elif m20 is not None and price < m20:
        trend = "조정"
    else:
        trend = "횡보"

    overheat_hits = sum([
        1 if rv is not None and rv > 70 else 0,
        1 if sk is not None and sk > 80 else 0,
        1 if gap20 is not None and gap20 > 10 else 0,
    ])
    overheat = "높음" if overheat_hits >= 2 else ("주의" if overheat_hits == 1 else "보통")

    if total >= 85 and overheat_hits == 0:
        action = "신규진입"
    elif total >= 75:
        action = "눌림목 대기"
    elif total >= 60:
        action = "관망"
    elif total >= 45:
        action = "일부매도" if trend in ("조정", "하락") else "관망"
    else:
        action = "손절검토"

    confirmations = []
    if (
        macd is not None and signal is not None and prev_macd is not None and prev_signal is not None
        and macd > signal and prev_macd <= prev_signal
        and rv is not None and prev_rv is not None and rv >= 50 > prev_rv
        and sk is not None and sd is not None and psk is not None and psd is not None
        and sk > sd and psk <= psd
        and m20 is not None and price > m20
    ):
        confirmations.append("강한 매수 Confirmation")
    if hist is not None and prev_hist is not None and hist > prev_hist and rv is not None and 50 <= rv <= 65 and ordered:
        confirmations.append("강한 상승 지속")
    if overheat_hits >= 2:
        confirmations.append("과열 경고")
    if (
        macd is not None and signal is not None and prev_macd is not None and prev_signal is not None
        and macd < signal and prev_macd >= prev_signal
        and rv is not None and rv < 50
        and m20 is not None and price < m20
    ):
        confirmations.append("하락 전환 경고")

    divergence = detect_divergence(closes, rsi_vals, macd_series)
    atr14 = atr(rows, 14)
    avg_vol20 = statistics.mean([x["volume"] for x in rows[-20:]]) if len(rows) >= 20 else None
    volume_ratio = (rows[-1]["volume"] / avg_vol20) if avg_vol20 else None
    turnover = price * rows[-1]["volume"]
    gap_pct = None
    if len(rows) >= 2 and rows[-1].get("open") is not None:
        gap_pct = pct(rows[-1]["open"], rows[-2]["close"])
    high20 = max(closes[-20:]) if closes else None
    low20 = min(closes[-20:]) if closes else None
    high52 = max(closes[-252:]) if closes else None
    new_high = bool(len(closes) >= 20 and price >= max(closes[-252:]))

    state = "상승 추세" if trend == "상승" else ("하락 추세" if trend == "하락" else trend)
    out = {
        "price": price,
        "date": rows[-1]["date"],
        "r20": pct(price, closes[-21]) if len(closes) >= 21 else None,
        "r60": pct(price, closes[-61]) if len(closes) >= 61 else None,
        "rsi": rv,
        "rsi_prev": prev_rv,
        "stoch_k": sk,
        "stoch_d": sd,
        "stoch_prev_k": psk,
        "stoch_prev_d": psd,
        "macd": macd,
        "macd_signal": signal,
        "macd_hist": hist,
        "macd_prev": prev_macd,
        "macd_signal_prev": prev_signal,
        "macd_hist_prev": prev_hist,
        "m5": m5,
        "m20": m20,
        "m60": m60,
        "m120": m120,
        "m20_slope": m20_slope,
        "m60_slope": m60_slope,
        "gap20": gap20,
        "gap60": gap60,
        "support": support,
        "resistance": resistance,
        "high20": high20,
        "low20": low20,
        "high52": high52,
        "new_high": new_high,
        "atr14": atr14,
        "volume_ratio": volume_ratio,
        "avg_volume20": avg_vol20,
        "turnover": turnover,
        "gap_pct": gap_pct,
        "state": state,
        "trading_score": {
            "total": total,
            "grade": grade,
            "grade_label": grade_label,
            "trend": trend,
            "action": action,
            "overheat": overheat,
            "confirmation": confirmations,
            "divergence": divergence,
            "components": {
                "macd": {"score": macd_score, "max": 30, "state": macd_state},
                "ma": {"score": ma_score, "max": 30, "state": ma_state, "heat_penalty": heat_penalty},
                "rsi": {"score": rsi_score, "max": 20, "state": rsi_state},
                "stochastic": {"score": stoch_score, "max": 20, "state": stoch_state},
            },
        },
    }
    out["price_strategy"] = price_strategy(out)
    return out

def _naver_world_index_item(url):
    headers = {
        "User-Agent": SESSION.headers.get("User-Agent"),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://stock.naver.com/",
    }
    j = req(url, 2, headers=headers).json()
    if isinstance(j, dict) and isinstance(j.get("datas"), list):
        item = (j.get("datas") or [None])[0] or {}
    else:
        item = j or {}
    value = n(str(item.get("closePrice") or "").replace(",", ""))
    change_pct = n(item.get("fluctuationsRatio"))
    direction = str((item.get("compareToPreviousPrice") or {}).get("code") or "")
    if change_pct is not None:
        if direction == "5":
            change_pct = -abs(change_pct)
        elif direction == "2":
            change_pct = abs(change_pct)
    traded_at = str(item.get("localTradedAt") or "")
    trade_date = traded_at[:10] if len(traded_at) >= 10 else ""
    if value is None or not trade_date:
        raise RuntimeError(f"Naver world index incomplete: {url} keys={list(item)[:12]}")
    return {
        "value": value,
        "change_pct": change_pct,
        "date": trade_date,
        "fresh": True,
        "market_status": item.get("marketStatus"),
        "local_traded_at": traded_at,
    }


def nasdaq_official_snapshot():
    """Latest official Nasdaq Composite daily close.

    Uses Nasdaq's public historical index endpoint as the authoritative close.
    This prevents a stale Yahoo/Naver response from overwriting a newer U.S.
    session in the generated dashboard data.
    """
    end = NOW.date() + timedelta(days=1)
    start = NOW.date() - timedelta(days=12)
    url = (
        "https://api.nasdaq.com/api/quote/COMP/historical"
        f"?assetclass=index&fromdate={start.isoformat()}&todate={end.isoformat()}&limit=30"
    )
    headers = {
        "User-Agent": SESSION.headers.get("User-Agent"),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nasdaq.com/",
        "Origin": "https://www.nasdaq.com",
    }
    j = req(url, 2, headers=headers).json()
    rows = (((j.get("data") or {}).get("tradesTable") or {}).get("rows") or [])
    parsed = []
    for row in rows:
        raw_date = str(row.get("date") or "").strip()
        raw_close = str(row.get("close") or "").replace("$", "").replace(",", "").strip()
        close = n(raw_close)
        if close is None or not raw_date:
            continue
        dt = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw_date, fmt).date()
                break
            except Exception:
                pass
        if dt is not None:
            parsed.append((dt, close))
    if not parsed:
        raise RuntimeError("Nasdaq official historical rows missing")
    parsed.sort(key=lambda x: x[0])
    latest_date, latest_close = parsed[-1]
    prev_close = parsed[-2][1] if len(parsed) >= 2 else None
    return {
        "value": latest_close,
        "change_pct": pct(latest_close, prev_close) if prev_close else None,
        "date": latest_date.isoformat(),
        "fresh": True,
        "source": "Nasdaq official COMP historical",
    }


def naver_world_index_basic_snapshot(reuters_code=".IXIC"):
    x = _naver_world_index_item(f"https://api.stock.naver.com/index/{reuters_code}/basic")
    x["source"] = "Naver Stock index basic"
    return x


def naver_world_index_polling_snapshot(reuters_code=".IXIC"):
    x = _naver_world_index_item(f"https://polling.finance.naver.com/api/realtime/worldstock/index/{reuters_code}")
    x["source"] = "Naver Finance world-index polling"
    return x


def _direction_name(value):
    if isinstance(value, dict):
        return str(value.get("name") or value.get("text") or value.get("code") or "")
    return str(value or "")


def _signed_market_value(value, direction):
    v = n(value)
    if v is None:
        return None
    d = _direction_name(direction).upper()
    if d in {"FALLING", "하락", "5", "4"}:
        return -abs(v)
    if d in {"RISING", "상승", "2", "1"}:
        return abs(v)
    return v


def _naver_index_rows(index_code):
    url = f"https://api.stock.naver.com/chart/domestic/index/{index_code}?periodType=dayCandle"
    payload = req(url, 2).json()
    if isinstance(payload, list):
        items = payload
    else:
        items = payload.get("priceInfos") or payload.get("result") or payload.get("datas") or []
    rows = []
    for x in items:
        ds = str(x.get("localDate") or x.get("localTradedAt") or x.get("date") or "")[:10]
        close = n(x.get("closePrice") or x.get("close"))
        if not ds or close is None:
            continue
        rows.append({
            "date": ds,
            "open": n(x.get("openPrice") or x.get("open")) or close,
            "high": n(x.get("highPrice") or x.get("high")) or close,
            "low": n(x.get("lowPrice") or x.get("low")) or close,
            "close": close,
            "volume": n(x.get("accumulatedTradingVolume") or x.get("volume")) or 0,
        })
    rows.sort(key=lambda x: x["date"])
    if len(rows) < 20:
        raise RuntimeError(f"Naver index history insufficient: {index_code}")
    return rows, "Naver Stock index chart"


def naver_domestic_index_snapshot(index_code):
    url = f"https://stock.naver.com/api/securityFe/api/index/{index_code}/basic"
    x = req(url, 2).json()
    value = n(x.get("closePrice") or x.get("currentPrice"))
    if value is None:
        raise RuntimeError(f"Naver index snapshot missing: {index_code}")
    direction = x.get("fluctuationsType") or x.get("compareToPreviousPrice")
    cp = _signed_market_value(x.get("fluctuationsRatio"), direction)
    ds = str(x.get("localTradedAt") or x.get("localDate") or NOW.date().isoformat())[:10]
    return {
        "value": value,
        "change_pct": cp,
        "date": ds,
        "fresh": True,
        "source": "Naver Stock index basic",
    }


def naver_index_flow(index_code):
    url = f"https://stock.naver.com/api/securityFe/api/index/{index_code}/integration"
    x = req(url, 2).json()
    trend = x.get("dealTrendInfo") or {}
    if not trend:
        raise RuntimeError(f"Naver index flow missing: {index_code}")
    return {
        "date": str(trend.get("bizdate") or "")[:10],
        "individual": n(trend.get("personalValue")),
        "foreign": n(trend.get("foreignValue")),
        "institution": n(trend.get("institutionalValue")),
        "source": "Naver Stock index integration",
    }


def _find_reuters_item(payload, code):
    items = payload if isinstance(payload, list) else (payload.get("datas") or payload.get("result") or [])
    for x in items:
        if str(x.get("reutersCode") or "") == code:
            return x
    raise RuntimeError(f"Naver Reuters code not found: {code}")


def naver_fx_snapshot():
    x = _find_reuters_item(
        req("https://stock.naver.com/api/securityService/marketindex/majors/rpc", 2).json(),
        "FX_USDKRW",
    )
    value = n(x.get("calcPrice") or x.get("closePrice") or x.get("currentPrice"))
    if value is None:
        raise RuntimeError("Naver FX value missing")
    direction = x.get("fluctuationsType") or x.get("compareToPreviousPrice")
    cp = _signed_market_value(x.get("fluctuationsRatio"), direction)
    ds = str(x.get("localTradedAt") or x.get("localDate") or NOW.date().isoformat())[:10]
    return {
        "value": value,
        "change_pct": cp,
        "date": ds,
        "fresh": True,
        "source": "Naver Stock majors RPC",
    }


def naver_us10y_snapshot():
    x = _find_reuters_item(
        req("https://stock.naver.com/api/securityService/marketindex/majors/bond", 2).json(),
        "US10YT=RR",
    )
    value = n(x.get("closePrice") or x.get("currentPrice"))
    if value is None:
        raise RuntimeError("Naver US10Y value missing")
    direction = x.get("fluctuationsType") or x.get("compareToPreviousPrice")
    cp = _signed_market_value(x.get("fluctuationsRatio"), direction)
    ds = str(x.get("localTradedAt") or x.get("localDate") or NOW.date().isoformat())[:10]
    return {
        "value": value,
        "change_pct": cp,
        "date": ds,
        "fresh": True,
        "source": "Naver Stock majors bond",
    }


def naver_world_index_current_snapshot(reuters_code=".IXIC"):
    payload = req(
        f"https://stock.naver.com/api/polling/worldstock/index?reutersCodes={quote(reuters_code, safe='.')}",
        2,
    ).json()
    x = _find_reuters_item(payload, reuters_code)
    value = n(x.get("closePrice") or x.get("currentPrice"))
    if value is None:
        raise RuntimeError(f"Naver world-index value missing: {reuters_code}")
    direction = x.get("fluctuationsType") or x.get("compareToPreviousPrice")
    cp = _signed_market_value(x.get("fluctuationsRatio"), direction)
    ds = str(x.get("localTradedAt") or x.get("localDate") or NOW.date().isoformat())[:10]
    return {
        "value": value,
        "change_pct": cp,
        "date": ds,
        "fresh": True,
        "source": "Naver Stock world-index polling v2",
    }


def market_series():
    syms = CFG.get("settings", {}).get("market_symbols", {})
    out, errors = {}, []

    for key, symbol in syms.items():
        if key in ("kospi", "kosdaq"):
            index_code = "KOSPI" if key == "kospi" else "KOSDAQ"
            try:
                rows, row_source = _naver_index_rows(index_code)
                snap = naver_domestic_index_snapshot(index_code)
                try:
                    flow = naver_index_flow(index_code)
                except Exception as e:
                    errors.append(f"{key} flow: {e}")
                    flow = {}
                out[key] = {**snap, "rows": rows, "flow": flow, "history_source": row_source}
                continue
            except Exception as e:
                errors.append(f"{key} Naver: {e}")

        if key == "usdkrw":
            try:
                snap = naver_fx_snapshot()
                rows = []
                try:
                    rows, _ = yahoo_chart(symbol)
                except Exception as e:
                    errors.append(f"usdkrw Yahoo history: {e}")
                out[key] = {**snap, "rows": rows}
                continue
            except Exception as e:
                errors.append(f"usdkrw Naver: {e}")

        if key == "us10y":
            try:
                snap = naver_us10y_snapshot()
                rows = []
                try:
                    rows, _ = yahoo_chart(symbol)
                except Exception as e:
                    errors.append(f"us10y Yahoo history: {e}")
                out[key] = {**snap, "rows": rows}
                continue
            except Exception as e:
                errors.append(f"us10y Naver: {e}")

        if key == "nasdaq":
            candidates = []
            for label, fn in (
                ("Naver polling v2", lambda: naver_world_index_current_snapshot(".IXIC")),
                ("Nasdaq official", nasdaq_official_snapshot),
                ("Naver basic", lambda: naver_world_index_basic_snapshot(".IXIC")),
                ("Naver polling", lambda: naver_world_index_polling_snapshot(".IXIC")),
            ):
                try:
                    candidates.append(fn())
                except Exception as e:
                    errors.append(f"nasdaq {label}: {e}")

            yahoo_rows = []
            try:
                yahoo_rows, yahoo_source = yahoo_chart(symbol)
                if yahoo_rows:
                    vals = [x["close"] for x in yahoo_rows]
                    candidates.append({
                        "value": vals[-1],
                        "change_pct": pct(vals[-1], vals[-2]) if len(vals) > 1 else None,
                        "date": yahoo_rows[-1]["date"],
                        "fresh": True,
                        "source": yahoo_source,
                    })
            except Exception as e:
                errors.append(f"nasdaq Yahoo: {e}")

            if candidates:
                priority = {
                    "Naver Stock world-index polling v2": 6,
                    "Nasdaq official COMP historical": 5,
                    "Naver Stock index basic": 4,
                    "Naver Finance world-index polling": 3,
                    "Yahoo Finance": 2,
                }
                snap = max(
                    candidates,
                    key=lambda x: (str(x.get("date") or ""), priority.get(x.get("source"), 1))
                )
                prev_text = PREV.get("text") or {}
                prev_date = str(prev_text.get("nasdaqDataDate") or "")
                prev_value = n(str(prev_text.get("nasdaq") or "").replace(",", ""))
                prev_delta_text = str(prev_text.get("nasdaqDelta") or "")
                prev_delta_match = re.search(r"([+-]?\d+(?:\.\d+)?)%", prev_delta_text)
                prev_change_pct = n(prev_delta_match.group(1)) if prev_delta_match else None
                if prev_date and prev_value is not None and str(snap.get("date") or "") < prev_date:
                    snap = {
                        "value": prev_value,
                        "change_pct": prev_change_pct,
                        "date": prev_date,
                        "fresh": True,
                        "source": "Previous verified Nasdaq close · anti-regression",
                    }
                out[key] = {**snap, "rows": yahoo_rows}
                continue
            out[key] = {"fresh": False}
            continue

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
    """Korea sentiment with medium-term trend dominance and anti-whipsaw gates [AI].

    The old formula over-weighted 1-day rebounds. This version emphasizes
    20D/60D trend, penalizes absolute high volatility, includes KOSPI flow,
    and caps the score when the 20D trend remains deeply negative.
    """
    try:
        k_rows = (market.get("kospi") or {}).get("rows") or []
        q_rows = (market.get("kosdaq") or {}).get("rows") or []
        fx_rows = (market.get("usdkrw") or {}).get("rows") or []
        kc = [x["close"] for x in k_rows]
        qc = [x["close"] for x in q_rows]
        fc = [x["close"] for x in fx_rows]

        if len(kc) < 100 or len(qc) < 100 or len(fc) < 40:
            raise RuntimeError("insufficient history")

        k5 = rolling_returns(kc, 5)
        q5 = rolling_returns(qc, 5)
        k20 = rolling_returns(kc, 20)
        k60 = rolling_returns(kc, 60)
        fx20 = rolling_returns(fc, 20)
        vols = rolling_realized_vol(kc, 20)
        if not (k5 and q5 and k20 and k60 and fx20 and vols):
            raise RuntimeError("indicator series empty")

        # Relative scores.
        k5_score = percentile_rank(k5, k5[-1])
        q5_score = percentile_rank(q5, q5[-1])
        trend20_score = percentile_rank(k20, k20[-1])
        trend60_score = percentile_rank(k60, k60[-1])
        vol_score = 100.0 - percentile_rank(vols, vols[-1])
        fx_score = 100.0 - percentile_rank(fx20, fx20[-1])

        # Absolute volatility guard: a 40% realized-volatility market should not
        # receive a "greed" boost just because recent history was even more volatile.
        current_vol = vols[-1]
        if current_vol >= 35:
            vol_score = min(vol_score, 35.0)
        elif current_vol >= 30:
            vol_score = min(vol_score, 50.0)
        elif current_vol >= 25:
            vol_score = min(vol_score, 65.0)

        # Foreign/institutional KOSPI flow contributes 10%.
        flow = (market.get("kospi") or {}).get("flow") or {}
        foreign = n(flow.get("foreign"))
        institution = n(flow.get("institution"))
        if foreign is None or institution is None:
            flow_score = 50.0
            flow_value = "수급 확인 중"
        else:
            weighted_flow = foreign * 0.60 + institution * 0.40
            flow_score = 50.0 + 40.0 * math.tanh(weighted_flow / 10000.0)
            flow_value = f"외인 {foreign:+,.0f}억 · 기관 {institution:+,.0f}억"

        components = [
            {"key": "trend20", "label": "KOSPI 20D 추세", "score": round(clamp(trend20_score), 1), "weight": 30, "value": signed(k20[-1], 2)},
            {"key": "trend60", "label": "KOSPI 60D 추세", "score": round(clamp(trend60_score), 1), "weight": 15, "value": signed(k60[-1], 2)},
            {"key": "volatility", "label": "변동성 · 20D RV", "score": round(clamp(vol_score), 1), "weight": 20, "value": f"{current_vol:.1f}%"},
            {"key": "momentum5", "label": "KOSPI 5D 모멘텀", "score": round(clamp(k5_score), 1), "weight": 10, "value": signed(k5[-1], 2)},
            {"key": "kosdaq5", "label": "KOSDAQ 5D 모멘텀", "score": round(clamp(q5_score), 1), "weight": 10, "value": signed(q5[-1], 2)},
            {"key": "flow", "label": "외국인·기관 수급", "score": round(clamp(flow_score), 1), "weight": 10, "value": flow_value},
            {"key": "safe_haven", "label": "USD/KRW 20D", "score": round(clamp(fx_score), 1), "weight": 5, "value": signed(fx20[-1], 2)},
        ]

        raw_score = sum(x["score"] * x["weight"] for x in components) / 100.0
        score = raw_score
        gate = "없음"

        # Medium-term regime gates prevent a single rebound day from flipping
        # a deeply damaged market straight into Greed.
        if k20[-1] <= -15:
            score = min(score, 44.0)
            gate = "KOSPI 20D ≤ -15% → 상단 44"
        elif k20[-1] <= -10:
            score = min(score, 54.0)
            gate = "KOSPI 20D ≤ -10% → 상단 54"
        elif k20[-1] <= -5:
            score = min(score, 64.0)
            gate = "KOSPI 20D ≤ -5% → 상단 64"

        if k60[-1] <= -20:
            score = min(score, 49.0)
            gate += " · KOSPI 60D ≤ -20% → 상단 49"

        return {
            "available": True,
            "fresh": True,
            "score": round(clamp(score), 1),
            "raw_score": round(clamp(raw_score), 1),
            "rating": sentiment_label(score),
            "timestamp": NOW.isoformat(),
            "source": "KOSPI·KOSDAQ·USD/KRW·수급 · 자체 정규화 [AI]",
            "method": "KOSPI 20D 추세 30% + 60D 추세 15% + 20D 변동성 20% + KOSPI 5D 10% + KOSDAQ 5D 10% + 수급 10% + FX 5% · 추세 Regime Gate 적용",
            "regime_gate": gate,
            "components": components,
        }
    except Exception as e:
        return {
            "available": False,
            "fresh": False,
            "score": None,
            "rating": "데이터 확인 중",
            "timestamp": NOW.isoformat(),
            "source": "KOSPI·KOSDAQ·USD/KRW·수급 · 자체 정규화 [AI]",
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



def fin_number(text):
    if text is None:
        return None
    t = str(text).strip().replace(",", "").replace(" ", "").replace("원", "")
    if t in ("", "-", "—", "N/A", "nan"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    try:
        v = float(t)
        return -v if neg else v
    except Exception:
        return None


def fmt_fin_100m(v):
    if v is None:
        return "—"
    if abs(v) >= 10000:
        return f"{v/10000:.2f}조"
    return f"{v:,.0f}억"


def quarter_label(raw):
    m = re.match(r"^(\d{4})\.(\d{2})(\(E\))?$", str(raw).strip())
    if not m:
        return str(raw)
    year, month, est = int(m.group(1)), int(m.group(2)), bool(m.group(3))
    q = max(1, min(4, (month - 1) // 3 + 1))
    return f"{year}Q{q}{'E' if est else 'A'}"


def fetch_quarterly_financials(code):
    """Use Naver mobile JSON quarterly financial endpoint."""
    url = f"https://m.stock.naver.com/api/stock/{code}/finance/quarter"
    j = req(url, 2).json()
    info = j.get("financeInfo") or j.get("finance_info") or j
    titles = info.get("trTitleList") or info.get("titleList") or []
    row_list = info.get("rowList") or info.get("rows") or []
    if not titles or not row_list:
        raise RuntimeError(f"quarter finance JSON incomplete: keys={list(info)[:8]}")

    cols = []
    for x in titles:
        key = str(x.get("key") or "").strip()
        title = str(x.get("title") or "").strip().rstrip(".")
        cons = str(x.get("isConsensus") or x.get("consensus") or "N").upper() == "Y"
        if key and title:
            cols.append({"key": key, "title": title, "estimate": cons})
    if len(cols) < 4:
        raise RuntimeError(f"quarter headers insufficient: {cols}")

    rows = {}
    for row in row_list:
        label = str(row.get("title") or row.get("name") or "").replace(" ", "")
        columns = row.get("columns") or {}
        values = {}
        for c in cols:
            cell = columns.get(c["key"])
            if isinstance(cell, dict):
                cell = cell.get("value")
            values[c["key"]] = fin_number(cell)
        if label:
            rows[label] = values

    def series(*names):
        for name in names:
            key = name.replace(" ", "")
            if key in rows:
                return [rows[key].get(c["key"]) for c in cols]
        return [None] * len(cols)

    rev = series("매출액")
    op = series("영업이익")
    net = series("당기순이익", "당기순이익(지배)", "지배주주순이익")
    eps = series("EPS(원)", "EPS")

    est_idxs = [i for i, c in enumerate(cols) if c["estimate"]]
    if est_idxs:
        end_idx = est_idxs[0]
        idxs = list(range(max(0, end_idx - 3), end_idx + 1))
    else:
        idxs = list(range(max(0, len(cols) - 4), len(cols)))
    if len(idxs) < 4:
        idxs = list(range(max(0, len(cols) - 4), len(cols)))
    idxs = idxs[-4:]

    quarters = []
    for i in idxs:
        qoq = None
        if i > 0 and rev[i] is not None and rev[i - 1] not in (None, 0):
            qoq = pct(rev[i], rev[i - 1])
        opm = None if rev[i] in (None, 0) or op[i] is None else op[i] / rev[i] * 100
        raw_title = cols[i]["title"].rstrip(".")
        raw_for_label = raw_title + ("(E)" if cols[i]["estimate"] and "(E)" not in raw_title else "")
        quarters.append({
            "raw_date": raw_title,
            "label": quarter_label(raw_for_label),
            "estimate": cols[i]["estimate"],
            "revenue": rev[i],
            "op": op[i],
            "opm": opm,
            "net": net[i],
            "eps": eps[i],
            "qoq": qoq,
        })

    if len(quarters) != 4:
        raise RuntimeError(f"quarter selection failed: {cols}")
    if not any(q.get("estimate") for q in quarters):
        raise RuntimeError("next-quarter consensus column missing")
    return {
        "quarters": quarters,
        "source": "Naver Stock · FnGuide quarterly",
        "url": url,
        "fetched_at": NOW.isoformat(),
    }


def apply_quarterly_financials(profile, snapshot):
    txt = profile.setdefault("text", {})
    quarters = snapshot.get("quarters") or []
    if len(quarters) != 4:
        return False

    for i, q in enumerate(quarters, 1):
        txt[f"y{i}"] = q["label"]
        txt[f"rev{i}"] = fmt_fin_100m(q.get("revenue"))
        txt[f"op{i}a"] = fmt_fin_100m(q.get("op"))
        txt[f"opm{i}"] = "—" if q.get("opm") is None else f"{q['opm']:.1f}% [AI]"
        txt[f"ni{i}"] = fmt_fin_100m(q.get("net"))
        txt[f"eps{i}"] = "—" if q.get("eps") is None else f"{q['eps']:,.0f}원"
        txt[f"yoy{i}"] = "—" if q.get("qoq") is None else f"매출 QoQ {q['qoq']:+.1f}% [AI]"

    next_est = next((q for q in quarters if q.get("estimate")), None)
    if next_est and next_est.get("eps") is not None:
        txt["valEps"] = f"{next_est['eps']:,.0f}원 · {next_est['label']}"
        fper = fin_number(str(txt.get("fper2", "")).lower().replace("x", ""))
        if fper is not None:
            txt["valTargetPer"] = f"{fper:.2f}x · Forward PER"
            txt["targetPer"] = f"{fper:.2f}x · Forward PER"
            fair = next_est["eps"] * 4 * fper
            txt["fairValueA"] = f"{fmt_price(fair)}원 [AI]"
            txt["fairA"] = f"{fmt_price(fair)}원 [AI]"
            txt["calcA"] = (
                f"{next_est['label']} EPS {next_est['eps']:,.0f}원 × 4 × Forward PER {fper:.2f}x "
                f"= {fmt_price(fair)}원. 분기 EPS 연환산 방식 [AI]"
            )
        else:
            txt["valTargetPer"] = "[검증 필요]"
            txt["targetPer"] = "[검증 필요]"
            txt["fairValueA"] = "[검증 필요]"
            txt["fairA"] = "[검증 필요]"
            txt["calcA"] = "다음 분기 EPS(E)와 Forward PER 검증값이 모두 필요합니다."
    else:
        txt["valEps"] = "[검증 필요]"
        txt["valTargetPer"] = "[검증 필요]"
        txt["targetPer"] = "[검증 필요]"
        txt["fairValueA"] = "[검증 필요]"
        txt["fairA"] = "[검증 필요]"
        txt["calcA"] = "다음 분기 Consensus EPS가 확인되지 않았습니다."

    labels = " · ".join(q["label"] for q in quarters)
    txt["financialNote"] = (
        f"분기 기준: {labels}. 최근 실제 분기와 다음 분기(E) Consensus를 함께 표시합니다. "
        f"출처: {snapshot.get('source')}. OPM·QoQ·연환산 적정주가는 자체계산 [AI]."
    )
    profile["financial_basis"] = "quarterly"
    profile["quarterly_financials"] = snapshot
    return True





def clean_metric_number(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    text = re.sub(r"(배|원|%|백만|천주)$", "", text).strip()
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


def find_nested_value(obj, candidate_keys):
    keys = {str(k).lower() for k in candidate_keys}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in keys and v not in (None, "", "-", "—"):
                return v
        for v in obj.values():
            found = find_nested_value(v, candidate_keys)
            if found not in (None, "", "-", "—"):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_nested_value(v, candidate_keys)
            if found not in (None, "", "-", "—"):
                return found
    return None


def fetch_stock_valuation_snapshot(code):
    url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    j = req(url, 2).json()
    info = {}
    for item in j.get("totalInfos") or []:
        c = str(item.get("code") or "").strip()
        if c:
            info[c] = item.get("value")
    target_raw = find_nested_value(
        j.get("consensusInfo") or {},
        ("targetPrice", "target_price", "consensusTargetPrice", "averageTargetPrice"),
    )
    return {
        "per": clean_metric_number(info.get("per")),
        "forward_per": clean_metric_number(info.get("cnsPer")),
        "pbr": clean_metric_number(info.get("pbr")),
        "eps": clean_metric_number(info.get("eps")),
        "forward_eps": clean_metric_number(info.get("cnsEps")),
        "bps": clean_metric_number(info.get("bps")),
        "target_price": clean_metric_number(target_raw),
        "market_value": info.get("marketValue"),
        "source": "Naver Stock integration",
        "url": url,
    }


def apply_valuation_snapshot(profile, snap):
    txt = profile.setdefault("text", {})
    if snap.get("per") is not None:
        txt["per"] = f"{snap['per']:.2f}x"
    if snap.get("forward_per") is not None:
        txt["fper"] = f"{snap['forward_per']:.2f}x"
        txt["fper2"] = f"{snap['forward_per']:.2f}x"
    if snap.get("pbr") is not None:
        txt["pbr"] = f"{snap['pbr']:.2f}x"
    if snap.get("forward_eps") is not None and snap.get("bps") not in (None, 0):
        roe_proxy = snap["forward_eps"] / snap["bps"] * 100
        txt["roe"] = f"{roe_proxy:.1f}% [AI]"
    if snap.get("target_price") is not None:
        txt["fairPriceText"] = f"{fmt_price(snap['target_price'])}원 Cons."
        txt["fairB"] = f"{fmt_price(snap['target_price'])}원"
        txt["calcB"] = "네이버증권 Consensus 목표가 기준."
        profile.setdefault("inputs", {})["target2"] = str(int(round(snap["target_price"])))
    profile["valuation_snapshot"] = snap



def _theme_pick_maps(d):
    pick_by_code = {}
    theme_by_code = {}
    for card in d.get("theme_cards") or []:
        for pick in card.get("picks") or []:
            code = str(pick.get("ticker") or "").split(".")[0]
            if not code:
                continue
            old = pick_by_code.get(code)
            if old is None or float(pick.get("score") or 0) > float(old.get("score") or 0):
                pick_by_code[code] = pick
                theme_by_code[code] = card
    return pick_by_code, theme_by_code


def _generic_profile(base_profile, stock, card, pick, rank, final_score, fundamental_score, trading_score, market):
    code = str(stock.get("stock_code"))
    name = stock.get("name") or code
    sector_name = (card or {}).get("theme") or stock.get("sector") or market
    catalyst = str((pick or {}).get("reason") or "실적·수급·Catalyst를 확인합니다. [AI]")
    rating = recommendation_rating(final_score)

    base_text_keys = list((base_profile.get("text") or {}).keys())
    base_input_keys = list((base_profile.get("inputs") or {}).keys())
    text = {k: "—" for k in base_text_keys}
    for k in [
        "heroEyebrow","heroTitle","heroDesc","heroQuote","marketRegime","kospi","kospiDelta",
        "kosdaq","kosdaqDelta","nasdaq","nasdaqDelta","fx","fxDelta","us10y","us10yDelta",
        "flowState","flowDesc","marketReason1","marketReason2","marketReason3","marketImpact",
        "sector1","sector1Point","sector1Cycle","sector1Growth","sector2","sector2Point",
        "sector2Cycle","sector2Growth","sector3","sector3Point","sector3Cycle","sector3Growth",
        "sourceKR","sourceUS","sourceTopPick","sourceConsensus","footerDataNote","kospiFlow","kosdaqFlow"
    ]:
        text.pop(k, None)

    text.update({
        "topPickName": name,
        "topPickTicker": f"{code} · {market} · 자동 업데이트",
        "topPickScore": f"{int(round(final_score))} / {rating} [AI]",
        "thesis": (
            f"“{catalyst.replace(' [AI]','')}를 핵심 Catalyst로 보고, "
            f"Fundamental/Catalyst {fundamental_score:.0f}점 × 60% + "
            f"Trading Score {trading_score:.0f}점 × 40%로 선별했습니다.” [AI]"
        ),
        "fairPriceText": "Consensus 목표가 확인 필요",
        "upsideText": "—",
        "fper": "—",
        "roe": "—",
        "topRR": "—",
        "metricSource": "분기 실적: Naver Stock/FnGuide · 가격/기술지표: Naver Stock/Yahoo fallback",
        "business": f"{sector_name} 관련 사업 · 세부 사업구조는 공식 IR 확인",
        "customers": "공식 IR/공시에서 주요 고객 및 매출처 확인",
        "competitors": "동일 업종 국내외 Peer 비교 필요",
        "moat": "기술·수주·시장점유율·진입장벽은 공식 IR 기준 추가 검증",
        "barrier": "고객 인증·기술력·CAPEX·IP 등 업종별 진입장벽 확인",
        "earningsReason": "최근 실제 3개 분기와 다음 분기 Consensus를 자동 연결해 실적 방향을 확인합니다.",
        "per": "—",
        "fper2": "—",
        "pbr": "—",
        "evebitda": "—",
        "peerMultiple": "Consensus 확인 필요",
        "discount": "Peer/과거 밴드 확인 필요",
        "fairB": "Consensus 목표가 확인 필요",
        "calcB": "Consensus 목표가가 검증되면 반영합니다.",
        "histVal": "과거 Valuation Band 추가 검증 필요",
        "domesticPeer": "동일 업종 국내 Peer 비교 필요",
        "globalPeer": "동일 업종 해외 Peer 비교 필요",
        "shortCat": catalyst,
        "midCat": f"{sector_name} 업황·수주·제품 믹스 변화 확인 [AI]",
        "longCat": "중장기 실적 성장과 Valuation 재평가 가능성 확인 [AI]",
        "risk1": "실적 기대치 하향 / Consensus 미달",
        "risk1P": "가능성 M",
        "risk1I": "영향 H",
        "risk2": "Valuation Multiple 압축",
        "risk2P": "가능성 M",
        "risk2I": "영향 H",
        "risk3": "수급·변동성 확대",
        "risk3P": "가능성 M",
        "risk3I": "영향 M",
        "risk4": "핵심 Catalyst 일정 지연",
        "risk4P": "가능성 M",
        "risk4I": "영향 M",
        "disclosureTitle": f"{name} 최근 공시 확인",
        "disclosureDesc": "네이버증권 공시 페이지에서 최신 공시를 직접 확인합니다.",
        "irTitle": f"{name} IR / 실적자료",
        "irDesc": "회사 IR 또는 네이버증권 기업정보에서 최신 자료를 확인합니다.",
        "consTitle": f"{name} Consensus 최신성",
        "consDesc": "Forward 지표·이익 추정치·목표주가 최신성을 확인합니다.",
        "newsTitle": f"{name} 최신 주요기사",
        "newsDesc": "네이버증권 종목 뉴스에서 최신 기사를 확인합니다.",
        "tradePeriod": "1~6개월",
        "tradeOpinion": rating.upper(),
        "finalOpinion": f"{rating.upper()} · {market} TOP5",
        "finalLine": (
            f"최종선정점수 {final_score:.1f}점 = Fundamental/Catalyst {fundamental_score:.0f}×60% "
            f"+ Trading {trading_score:.0f}×40%. [AI]"
        ),
        "selectionScore": f"{final_score:.1f} [AI]",
        "selectionFundamentalScore": f"{fundamental_score:.0f} [AI]",
        "selectionTradingScore": f"{trading_score:.0f} / 100",
    })

    return {
        "rank": rank,
        "name": name,
        "ticker": stock.get("ticker"),
        "stock_code": code,
        "market": market,
        "sector": stock.get("sector"),
        "sector_name": sector_name,
        "score": round(final_score, 2),
        "selection_score": round(final_score, 2),
        "fundamental_score": round(fundamental_score, 2),
        "selection_trading_score": round(trading_score, 2),
        "rating": rating,
        "text": text,
        "inputs": {k: "" for k in base_input_keys},
        "links": {
            "disclosure": f"https://stock.naver.com/domestic/stock/{code}/notice",
            "ir": f"https://finance.naver.com/item/main.naver?code={code}",
            "consensus": f"https://finance.naver.com/item/coinfo.naver?code={code}",
            "news": f"https://stock.naver.com/domestic/stock/{code}/news",
        },
    }


def build_kospi_profiles(d, previous_profiles, live_by_code):
    """Re-screen the full KOSPI watchlist on every update.

    Final score = Fundamental/Catalyst 60% + Trading Score 40%.
    Basic entry gate = Fundamental >= 65 and Trading Score >= 50.
    Trading >= 70 is the priority pool, followed by 60~69 and 50~59.
    """
    stocks = CFG.get("stocks") or []
    stocks_by_code = {str(x.get("stock_code")): x for x in stocks}
    previous_by_code = {str(x.get("stock_code")): x for x in (previous_profiles or [])}
    pick_by_code, theme_by_code = _theme_pick_maps(d)

    eligible = []
    fallback = []
    diagnostics = []

    for code, stock in stocks_by_code.items():
        if not str(stock.get("ticker", "")).endswith(".KS"):
            continue
        live = live_by_code.get(code)
        pick = pick_by_code.get(code) or {}
        card = theme_by_code.get(code) or {}
        if not live:
            diagnostics.append({"code": code, "name": stock.get("name"), "reason": "live data missing"})
            continue

        fundamental_score = float(pick.get("score") or 0)
        trading_score = float((((live.get("tech") or {}).get("trading_score") or {}).get("total")) or 0)
        final_score = fundamental_score * 0.60 + trading_score * 0.40

        if trading_score >= 70:
            priority = 0
            band = "70+"
        elif trading_score >= 60:
            priority = 1
            band = "60~69"
        elif trading_score >= 50:
            priority = 2
            band = "50~59"
        else:
            priority = 3
            band = "<50"

        row = {
            "priority": priority,
            "band": band,
            "final_score": final_score,
            "fundamental_score": fundamental_score,
            "trading_score": trading_score,
            "code": code,
            "stock": stock,
            "pick": pick,
            "card": card,
        }
        diagnostics.append({
            "code": code,
            "name": stock.get("name"),
            "fundamental_score": round(fundamental_score, 2),
            "trading_score": round(trading_score, 2),
            "final_score": round(final_score, 2),
            "eligible": fundamental_score >= 65 and trading_score >= 50,
            "priority": band,
        })

        fallback.append(row)
        if fundamental_score >= 65 and trading_score >= 50:
            eligible.append(row)

    keyfn = lambda x: (
        x["priority"],
        -x["final_score"],
        -x["trading_score"],
        -x["fundamental_score"],
        x["stock"].get("name") or "",
    )
    eligible.sort(key=keyfn)

    # Always publish five names. If the strict gate leaves fewer than five,
    # fill from the next-highest combined scores but mark them as fallback.
    ranked = eligible[:5]
    if len(ranked) < 5:
        used = {x["code"] for x in ranked}
        fallback.sort(key=lambda x: (-x["final_score"], -x["trading_score"], -x["fundamental_score"]))
        for row in fallback:
            if row["code"] in used:
                continue
            ranked.append(row)
            used.add(row["code"])
            if len(ranked) >= 5:
                break

    d["kospi_selection_diagnostics"] = diagnostics
    d["kospi_selection_updated_at"] = NOW.isoformat()

    base_profile = (previous_profiles or [{}])[0] if previous_profiles else {}
    profiles = []
    for rank, item in enumerate(ranked[:5], 1):
        code = item["code"]
        old = previous_by_code.get(code)
        if old:
            profile = copy.deepcopy(old)
            profile["rank"] = rank
            profile["market"] = "KOSPI"
            profile["score"] = round(item["final_score"], 2)
            profile["selection_score"] = round(item["final_score"], 2)
            profile["fundamental_score"] = round(item["fundamental_score"], 2)
            profile["selection_trading_score"] = round(item["trading_score"], 2)
            profile["rating"] = recommendation_rating(item["final_score"])
            txt = profile.setdefault("text", {})
            catalyst = str(item["pick"].get("reason") or "실적·수급·Catalyst 확인 [AI]")
            txt["topPickScore"] = f"{int(round(item['final_score']))} / {profile['rating']} [AI]"
            txt["thesis"] = (
                f"“{catalyst.replace(' [AI]','')}를 핵심 Catalyst로 보고, "
                f"Fundamental/Catalyst {item['fundamental_score']:.0f}점 × 60% + "
                f"Trading Score {item['trading_score']:.0f}점 × 40%로 재선정했습니다.” [AI]"
            )
            txt["finalOpinion"] = f"{profile['rating'].upper()} · KOSPI TOP5"
            txt["finalLine"] = (
                f"최종선정점수 {item['final_score']:.1f}점 = Fundamental/Catalyst "
                f"{item['fundamental_score']:.0f}×60% + Trading {item['trading_score']:.0f}×40%. [AI]"
            )
            txt["selectionScore"] = f"{item['final_score']:.1f} [AI]"
            txt["selectionFundamentalScore"] = f"{item['fundamental_score']:.0f} [AI]"
            txt["selectionTradingScore"] = f"{item['trading_score']:.0f} / 100"
        else:
            profile = _generic_profile(
                base_profile, item["stock"], item["card"], item["pick"], rank,
                item["final_score"], item["fundamental_score"], item["trading_score"], "KOSPI"
            )
        profiles.append(profile)

    for p in profiles:
        txt = p.setdefault("text", {})
        for i, q in enumerate(profiles, 1):
            txt[f"stock{i}"] = q["name"]
            txt[f"score{i}"] = f"{int(round(q['score']))} / 100"
            txt[f"op{i}"] = q["rating"]
            txt[f"period{i}"] = "1~6개월"
            q_pick = pick_by_code.get(q["stock_code"]) or {}
            txt[f"cat{i}"] = str(q_pick.get("reason") or "Catalyst 확인 [AI]")
            txt[f"return{i}"] = "—"
            txt[f"rr{i}"] = "—"

    return profiles


KOSDAQ_IR_LINKS = {
    "189300": {
        "url": "https://intelliantech.com/kr/ir",
        "title": "인텔리안테크 공식 IR",
        "desc": "인텔리안테크 공식 홈페이지의 Investor Relations·Financial Highlights 페이지로 연결합니다.",
        "source": "official",
    },
    "196170": {
        "url": "https://www.alteogen.com/kr/sub/ir/information.php",
        "title": "알테오젠 공식 IR / 투자정보",
        "desc": "알테오젠 공식 홈페이지의 IR 투자정보 페이지로 연결합니다.",
        "source": "official",
    },
    "277810": {
        "url": "https://rainbow-robotics.com/company/ir/",
        "title": "레인보우로보틱스 공식 IR",
        "desc": "레인보우로보틱스 공식 홈페이지의 IR·공시·사업보고서 페이지로 연결합니다.",
        "source": "official",
    },
    "035900": {
        "url": "https://www.jype.com/ko/board/ir-data",
        "title": "JYP Entertainment 공식 IR 자료",
        "desc": "JYP Entertainment 공식 홈페이지의 실적발표·IR 자료실로 연결합니다.",
        "source": "official",
    },
    "108490": {
        "url": "https://m.irgo.co.kr/LC/3665",
        "title": "로보티즈 IR 자료실",
        "desc": "공식 홈페이지에 별도 IR 자료실이 확인되지 않아 IRGO의 로보티즈 IR 자료 페이지로 연결합니다.",
        "source": "IRGO",
    },
    "083650": {
        "url": "https://m.irgo.co.kr/IR-COMP/083650/-IR-PAGE",
        "title": "비에이치아이 IR 자료실",
        "desc": "비에이치아이의 IR 자료·IR 일정이 제공되는 IRGO 페이지로 연결합니다.",
        "source": "IRGO",
    },
}

KOSDAQ_CANDIDATE_CODES = {
    "196170",  # 알테오젠
    "277810",  # 레인보우로보틱스
    "083650",  # 비에이치아이
    "035900",  # JYP Ent.
    "108490",  # 로보티즈
    "189300",  # 인텔리안테크
    "054930",  # 유신
}


def recommendation_rating(score):
    score = float(score or 0)
    if score >= 80:
        return "Buy"
    if score >= 70:
        return "Positive"
    if score >= 60:
        return "Neutral"
    return "Caution"


def build_kosdaq_profiles(d, base_profile, live_by_code):
    """Select KOSDAQ TOP5 for actual trading use.

    Gate:
      - Trading Score < 60: exclude from TOP5
      - Trading Score >= 70: priority pool
      - Trading Score 60~69: secondary pool
    Rank inside each pool:
      - Final selection score = Fundamental/Catalyst 60% + Trading Score 40%
    """
    stocks_by_code = {str(x.get("stock_code")): x for x in (CFG.get("stocks") or [])}
    pick_by_code = {}
    theme_by_code = {}
    for card in d.get("theme_cards") or []:
        for pick in card.get("picks") or []:
            code = str(pick.get("ticker") or "").split(".")[0]
            if code in KOSDAQ_CANDIDATE_CODES:
                old = pick_by_code.get(code)
                if old is None or float(pick.get("score") or 0) > float(old.get("score") or 0):
                    pick_by_code[code] = pick
                    theme_by_code[code] = card

    eligible = []
    diagnostics = []
    for code in KOSDAQ_CANDIDATE_CODES:
        stock = stocks_by_code.get(code)
        pick = pick_by_code.get(code)
        live = live_by_code.get(code)
        if not stock or not str(stock.get("ticker", "")).endswith(".KQ"):
            continue
        if not live:
            diagnostics.append({"code": code, "name": stock.get("name"), "reason": "live data missing"})
            continue

        fundamental_score = float((pick or {}).get("score") or 0)
        trading_score = float((((live.get("tech") or {}).get("trading_score") or {}).get("total")) or 0)
        final_score = fundamental_score * 0.60 + trading_score * 0.40
        priority = 0 if trading_score >= 70 else 1

        diagnostics.append({
            "code": code,
            "name": stock.get("name"),
            "fundamental_score": round(fundamental_score, 2),
            "trading_score": round(trading_score, 2),
            "final_score": round(final_score, 2),
            "eligible": trading_score >= 60,
            "priority": "70+" if trading_score >= 70 else ("60~69" if trading_score >= 60 else "<60 제외"),
        })

        if trading_score < 60:
            continue

        eligible.append({
            "priority": priority,
            "final_score": final_score,
            "fundamental_score": fundamental_score,
            "trading_score": trading_score,
            "code": code,
            "stock": stock,
            "pick": pick or {},
            "card": theme_by_code.get(code) or {},
        })

    eligible.sort(key=lambda x: (
        x["priority"],
        -x["final_score"],
        -x["trading_score"],
        -x["fundamental_score"],
        x["stock"].get("name") or "",
    ))
    ranked = eligible[:5]
    d["kosdaq_selection_diagnostics"] = diagnostics

    # Do not force low-score names into the list just to make five.
    # KOSDAQ recommendations are "up to 5" and only include names that pass
    # the Trading Score >= 60 gate.

    base_text_keys = list((base_profile.get("text") or {}).keys())
    base_input_keys = list((base_profile.get("inputs") or {}).keys())
    profiles = []

    for rank, item in enumerate(ranked, 1):
        final_score = item["final_score"]
        fundamental_score = item["fundamental_score"]
        trading_score = item["trading_score"]
        code = item["code"]
        stock = item["stock"]
        pick = item["pick"]
        card = item["card"]

        name = stock.get("name") or code
        sector_name = card.get("theme") or stock.get("sector") or "KOSDAQ"
        catalyst = str(pick.get("reason") or "섹터 Catalyst와 실적 추이를 확인합니다. [AI]")
        rating = recommendation_rating(final_score)

        text = {k: "—" for k in base_text_keys}
        for k in ["heroEyebrow","heroTitle","heroDesc","heroQuote","marketRegime","kospi","kospiDelta","nasdaq","nasdaqDelta","fx","fxDelta","us10y","us10yDelta","flowState","flowDesc","marketReason1","marketReason2","marketReason3","marketImpact","sector1","sector1Point","sector1Cycle","sector1Growth","sector2","sector2Point","sector2Cycle","sector2Growth","sector3","sector3Point","sector3Cycle","sector3Growth","sourceKR","sourceUS","sourceTopPick","sourceConsensus","footerDataNote","kospiFlow","kosdaqFlow"]:
            text.pop(k, None)
        text.update({
            "topPickName": name,
            "topPickTicker": f"{code} · KOSDAQ · 자동 업데이트",
            "topPickScore": f"{int(round(final_score))} / {rating} [AI]",
            "thesis": (
                f"“{catalyst.replace(' [AI]','')}를 핵심 투자 포인트로 보고, "
                f"Fundamental/Catalyst {fundamental_score:.0f}점 × 60% + "
                f"Trading Score {trading_score:.0f}점 × 40%로 최종 선별했습니다.” [AI]"
            ),
            "fairPriceText": "Consensus 목표가 확인 필요",
            "upsideText": "—",
            "fper": "—",
            "roe": "—",
            "topRR": "—",
            "metricSource": "분기 실적: Naver Stock/FnGuide · 가격/기술지표: Naver Stock/Yahoo fallback",
            "business": f"{sector_name} 관련 사업 · 세부 사업구조는 공식 IR 확인",
            "customers": "공식 IR/공시에서 주요 고객 및 매출처 확인",
            "competitors": "동일 업종 국내외 Peer 비교 필요",
            "moat": "기술·수주·시장점유율·진입장벽은 공식 IR 기준 추가 검증",
            "barrier": "고객 인증·기술력·CAPEX·IP 등 업종별 진입장벽 확인",
            "earningsReason": "최근 실제 3개 분기와 다음 분기 Consensus를 자동 연결해 실적 방향을 확인합니다.",
            "per": "—",
            "fper2": "—",
            "pbr": "—",
            "evebitda": "—",
            "peerMultiple": "Consensus 확인 필요",
            "discount": "Peer/과거 밴드 확인 필요",
            "fairB": "Consensus 목표가 확인 필요",
            "calcB": "Consensus 목표가가 검증되면 반영합니다.",
            "histVal": "과거 Valuation Band 추가 검증 필요",
            "domesticPeer": "동일 업종 국내 Peer 비교 필요",
            "globalPeer": "동일 업종 해외 Peer 비교 필요",
            "shortCat": catalyst,
            "midCat": f"{sector_name} 업황·수주·제품 믹스 변화 확인 [AI]",
            "longCat": "중장기 실적 성장과 Valuation 재평가 가능성 확인 [AI]",
            "risk1": "실적 기대치 하향 / Consensus 미달",
            "risk1P": "가능성 M",
            "risk1I": "영향 H",
            "risk2": "Valuation Multiple 압축",
            "risk2P": "가능성 M",
            "risk2I": "영향 H",
            "risk3": "수급·변동성 확대",
            "risk3P": "가능성 M",
            "risk3I": "영향 M",
            "risk4": "핵심 Catalyst 일정 지연",
            "risk4P": "가능성 M",
            "risk4I": "영향 M",
            "disclosureTitle": f"{name} 최근 공시 확인",
            "disclosureDesc": "네이버증권 공시 페이지에서 최신 공시를 직접 확인합니다.",
            "irTitle": (KOSDAQ_IR_LINKS.get(code) or {}).get("title", f"{name} IR / 실적자료"),
            "irDesc": (KOSDAQ_IR_LINKS.get(code) or {}).get("desc", "IR 자료 페이지를 확인합니다."),
            "consTitle": f"{name} Consensus 최신성",
            "consDesc": "Forward 지표·이익 추정치·목표주가 최신성을 확인합니다.",
            "newsTitle": f"{name} 최신 주요기사",
            "newsDesc": "네이버증권 종목 뉴스에서 최신 기사를 확인합니다.",
            "tradePeriod": "1~6개월",
            "tradeOpinion": rating.upper(),
            "finalOpinion": f"{rating.upper()} · KOSDAQ TOP5",
            "finalLine": (
                f"최종선정점수 {final_score:.1f}점 = Fundamental/Catalyst {fundamental_score:.0f}×60% "
                f"+ Trading {trading_score:.0f}×40%. Trading Score 60 미만은 TOP5에서 제외합니다. [AI]"
            ),
            "kosdaqSelectionScore": f"{final_score:.1f} [AI]",
            "kosdaqFundamentalScore": f"{fundamental_score:.0f} [AI]",
            "kosdaqTradingGate": f"{trading_score:.0f} / 100",
        })

        inputs = {k: "" for k in base_input_keys}
        profile = {
            "rank": rank,
            "name": name,
            "ticker": stock.get("ticker"),
            "stock_code": code,
            "market": "KOSDAQ",
            "sector": stock.get("sector"),
            "sector_name": sector_name,
            "score": round(final_score, 2),
            "selection_score": round(final_score, 2),
            "fundamental_score": round(fundamental_score, 2),
            "selection_trading_score": round(trading_score, 2),
            "selection_priority": "70+" if trading_score >= 70 else "60~69",
            "rating": rating,
            "text": text,
            "inputs": inputs,
            "links": {
                "disclosure": f"https://stock.naver.com/domestic/stock/{code}/notice",
                "ir": (KOSDAQ_IR_LINKS.get(code) or {}).get("url", f"https://finance.naver.com/item/main.naver?code={code}"),
                "consensus": f"https://finance.naver.com/item/coinfo.naver?code={code}",
                "news": f"https://stock.naver.com/domestic/stock/{code}/news",
            },
        }
        profiles.append(profile)

    for p in profiles:
        txt = p["text"]
        for i, q in enumerate(profiles, 1):
            txt[f"stock{i}"] = q["name"]
            txt[f"score{i}"] = f"{int(round(q['score']))} / 100"
            txt[f"op{i}"] = q["rating"]
            txt[f"period{i}"] = "1~6개월"
            q_pick = pick_by_code.get(q["stock_code"]) or {}
            txt[f"cat{i}"] = str(q_pick.get("reason") or "Catalyst 확인 [AI]")
            txt[f"return{i}"] = "—"
            txt[f"rr{i}"] = "—"

    return profiles



def synchronize_profile_group_rows(profiles):
    """Populate the visible TOP5 comparison table from each profile itself."""
    rows = []
    for q in profiles[:5]:
        txt = q.setdefault("text", {})
        inp = q.setdefault("inputs", {})
        current = n(inp.get("currentPrice"))
        target = n(inp.get("target2"))
        stop = n(inp.get("stopPrice"))
        if current is not None and target is not None:
            upside = pct(target, current)
            txt["upsideText"] = f"{upside:+.1f}% [AI]"
        if current is not None and target is not None and stop is not None and current > stop:
            rr = (target - current) / (current - stop)
            txt["topRR"] = f"{rr:.2f} : 1 [AI]"
        rows.append({
            "name": q.get("name") or "—",
            "score": int(round(n(q.get("score")) or 0)),
            "price": "—" if current is None else f"{fmt_price(current)}원",
            "target": txt.get("fairPriceText") or ("—" if target is None else f"{fmt_price(target)}원 [AI]"),
            "return": txt.get("upsideText") or "—",
            "rr": txt.get("topRR") or "—",
            "period": txt.get("tradePeriod") or "1~6개월",
            "cat": txt.get("shortCat") or "Catalyst 확인 [AI]",
            "op": q.get("rating") or "Neutral",
        })
    for p in profiles:
        txt = p.setdefault("text", {})
        for i, row in enumerate(rows, 1):
            txt[f"stock{i}"] = row["name"]
            txt[f"score{i}"] = f"{row['score']} / 100"
            txt[f"price{i}"] = row["price"]
            txt[f"target{i}Display"] = row["target"]
            txt[f"return{i}"] = row["return"]
            txt[f"rr{i}"] = row["rr"]
            txt[f"period{i}"] = row["period"]
            txt[f"cat{i}"] = row["cat"]
            txt[f"op{i}"] = row["op"]


def reset_profile_scores(profiles):
    """Keep market sentiment separate from stock recommendation scores."""
    for p in profiles:
        p.pop("adjusted_score", None)
        p.pop("sentiment_modifier", None)
        p.setdefault("text", {})["topPickScore"] = f"{int(n(p.get('score')) or 0)} / {p.get('rating','')} [AI]"
    for p in profiles:
        txt = p.setdefault("text", {})
        for i, q in enumerate(profiles[:5], 1):
            txt[f"score{i}"] = f"{int(n(q.get('score')) or 0)} / 100"


def update_market_text(d, market):
    t = d.setdefault("text", {})
    equity_dates = []

    def set_market(key, text_key, delta_key):
        x = market.get(key) or {}
        if x.get("value") is None:
            return
        v = x["value"]
        if key == "us10y":
            t[text_key] = f"{v:.1f}%"
        else:
            t[text_key] = f"{int(round(v)):,}"
        cp = x.get("change_pct")
        date_text = f" · {x.get('date','')[-5:]}" if x.get("date") else ""
        t[delta_key] = (signed(cp, 2) if cp is not None else "—") + date_text
        if key in ("kospi", "kosdaq", "nasdaq") and x.get("date"):
            equity_dates.append(x["date"])
        if key == "nasdaq" and x.get("date"):
            t["nasdaqDataDate"] = x["date"]

    def flow_text(index_key):
        flow = (market.get(index_key) or {}).get("flow") or {}
        foreign = flow.get("foreign")
        institution = flow.get("institution")
        if foreign is None or institution is None:
            return "수급 최신값 확인 중"
        return f"외국인 {foreign:+,.0f}억 · 기관 {institution:+,.0f}억"

    set_market("kospi", "kospi", "kospiDelta")
    set_market("kosdaq", "kosdaq", "kosdaqDelta")
    set_market("nasdaq", "nasdaq", "nasdaqDelta")
    set_market("usdkrw", "fx", "fxDelta")
    set_market("us10y", "us10y", "us10yDelta")

    t["kospiFlow"] = flow_text("kospi")
    t["kosdaqFlow"] = flow_text("kosdaq")

    kf = ((market.get("kospi") or {}).get("flow") or {})
    if kf.get("foreign") is not None and kf.get("institution") is not None:
        foreign = kf["foreign"]
        institution = kf["institution"]
        foreign_state = "외인 매수" if foreign > 0 else ("외인 매도" if foreign < 0 else "외인 중립")
        inst_state = "기관 매수" if institution > 0 else ("기관 매도" if institution < 0 else "기관 중립")
        t["flowState"] = f"{inst_state} / {foreign_state}"
        t["flowDesc"] = f"기관 {institution:+,.0f}억 · 외인 {foreign:+,.0f}억"
    else:
        t["flowState"] = "수급 데이터 확인 중"
        t["flowDesc"] = "가격 데이터는 최신 반영 · 외인/기관 수급 확인 중"

    md = max(equity_dates) if equity_dates else d.get("meta", {}).get("market_date")
    if md:
        t["sourceMarketDate"] = f"{md} 종가"
        t["heroDesc"] = (
            f"{md} 최신 종가 기준으로 시장 환경부터 산업 사이클, 실적·밸류에이션·Catalyst·"
            "기술적 위치까지 연결해 현재 가격의 기대수익과 하락위험을 판단합니다."
        )

    t["sourceKR"] = f"KOSPI {t.get('kospi','—')} · KOSDAQ {t.get('kosdaq','—')} · USD/KRW {t.get('fx','—')}"
    t["sourceUS"] = f"Nasdaq {t.get('nasdaq','—')} · US10Y {t.get('us10y','—')}"
    t["marketReason1"] = (
        f"KOSPI {t.get('kospi','—')} ({t.get('kospiDelta','—')}), "
        f"KOSDAQ {t.get('kosdaq','—')} ({t.get('kosdaqDelta','—')}), "
        f"Nasdaq {t.get('nasdaq','—')} ({t.get('nasdaqDelta','—')})로 최신 종가를 반영했습니다."
    )
    t["marketReason2"] = (
        f"USD/KRW {t.get('fx','—')} ({t.get('fxDelta','—')}), "
        f"미 10년물 {t.get('us10y','—')} ({t.get('us10yDelta','—')}). "
        f"KOSPI 수급은 {t.get('kospiFlow','확인 중')}입니다."
    )
    t["marketReason3"] = (
        "급변 장세에서는 지수 반등 자체보다 실적 추정치 변화, 외국인·기관 수급, "
        "주요 지지·저항과 변동성 축소 여부를 함께 확인합니다."
    )
    t["marketImpact"] = (
        "시장 방향성이 빠르게 바뀌는 구간이므로 지수 추격보다 종목별 실적·Catalyst와 "
        "진입 구조가 동시에 확인되는 후보를 우선합니다."
    )
    return md



def scenario_probabilities(score):
    s = float(score or 0)
    if s >= 80:
        return 40, 45, 15
    if s >= 70:
        return 35, 45, 20
    if s >= 60:
        return 30, 45, 25
    if s >= 50:
        return 25, 45, 30
    if s >= 40:
        return 20, 40, 40
    return 15, 35, 50


def first_valid_above(price, *values):
    vals = [n(v) for v in values]
    vals = [v for v in vals if v is not None and v > price]
    return min(vals) if vals else None


def first_valid_below(price, *values):
    vals = [n(v) for v in values]
    vals = [v for v in vals if v is not None and v < price]
    return max(vals) if vals else None


def apply_kosdaq_scenario(profile, tech):
    if profile.get("market") != "KOSDAQ":
        return
    txt = profile.setdefault("text", {})
    price = n(tech.get("price"))
    if price is None:
        return
    strategy = tech.get("price_strategy") or {}
    score = ((tech.get("trading_score") or {}).get("total")) or 0
    bull_p, base_p, bear_p = scenario_probabilities(score)

    bull = first_valid_above(
        price,
        (strategy.get("target2") or {}).get("price"),
        tech.get("high52"),
    )
    base = first_valid_above(
        price,
        (strategy.get("target1") or {}).get("price"),
        tech.get("resistance"),
    )
    bear = first_valid_below(
        price,
        (strategy.get("stop") or {}).get("price"),
        tech.get("support"),
        tech.get("m60"),
        tech.get("low20"),
    )

    atr14 = n(tech.get("atr14"))
    if bull is None and atr14 is not None:
        bull = price + 2.0 * atr14
    if base is None and atr14 is not None:
        base = price + 1.0 * atr14
    if bear is None and atr14 is not None:
        bear = max(0, price - 1.5 * atr14)

    def set_case(prefix, value, prob, cond):
        txt[f"{prefix}Price"] = "—" if value is None else f"{fmt_price(value)}원 [AI]"
        txt[f"{prefix}Prob"] = f"{prob}% [AI]"
        txt[f"{prefix}Return"] = "—" if value is None else f"{pct(value, price):+.1f}% [AI]"
        txt[f"{prefix}Cond"] = cond

    set_case(
        "bull",
        bull,
        bull_p,
        "2차 기술목표 또는 52주 고점 돌파와 MACD·MA 상승 구조 유지 시나리오 [AI]",
    )
    set_case(
        "base",
        base,
        base_p,
        "1차 기술목표/저항 테스트와 현재 추세 유지 시나리오 [AI]",
    )
    set_case(
        "bear",
        bear,
        bear_p,
        "기술적 손절가·지지선·MA60 중 유효 지지 이탈 시나리오 [AI]",
    )
    txt["finalOpinion"] = f"{(tech.get('trading_score') or {}).get('grade_label','Neutral').upper()} · {(tech.get('trading_score') or {}).get('action','관망')}"
    txt["finalLine"] = (
        f"Trading Score {score}, {(tech.get('trading_score') or {}).get('trend','확인 필요')} 추세 기준으로 "
        f"Bull/Base/Bear를 기술적 목표·지지·ATR에 연결했습니다. 시나리오 확률은 Trading Score 구간 기반 자체 휴리스틱 [AI]."
    )


def profile_live_update(p, live):
    if not live:
        return
    tech = live["tech"]
    price = tech["price"]
    score = tech.get("trading_score") or {}
    comps = score.get("components") or {}
    strategy = tech.get("price_strategy") or {}
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
    m5, m20, m60, m120 = tech.get("m5"), tech.get("m20"), tech.get("m60"), tech.get("m120")

    txt["technicalState"] = tech.get("state") or "관망"
    txt["rsiValue"] = "—" if rv is None else f"{rv:.1f}"
    txt["rsiSignal"] = (comps.get("rsi") or {}).get("state", "데이터 확인 중")

    if sk is None or sd is None:
        txt["stochValue"] = "—"
    else:
        txt["stochValue"] = f"%K {sk:.1f} / %D {sd:.1f}"
    txt["stochSignal"] = (comps.get("stochastic") or {}).get("state", "데이터 확인 중")

    if macd is None or sig is None:
        txt["macdValue"] = "—"
    else:
        txt["macdValue"] = f"MACD {macd:,.0f} / Signal {sig:,.0f} / Hist {hist:,.0f}"
    txt["macdSignal"] = (comps.get("macd") or {}).get("state", "데이터 확인 중")

    if m5 is None or m20 is None or m60 is None:
        txt["maValue"] = "—"
    else:
        txt["maValue"] = (
            f"5D {fmt_price(m5)} / 20D {fmt_price(m20)} / 60D {fmt_price(m60)}"
            + (f" / 120D {fmt_price(m120)}" if m120 is not None else "")
        )
    txt["maSignal"] = (comps.get("ma") or {}).get("state", "데이터 확인 중")

    # 100-point Trading Score.
    total = score.get("total")
    grade = score.get("grade")
    grade_label = score.get("grade_label")
    txt["tradingScore"] = "—" if total is None else f"{total}"
    txt["tradingGrade"] = "—" if total is None else f"{grade} / {grade_label}"
    txt["tradingTrend"] = score.get("trend") or "확인 필요"
    txt["tradingAction"] = score.get("action") or "관망"
    txt["tradingOverheat"] = score.get("overheat") or "확인 필요"
    txt["macdScore"] = f"{(comps.get('macd') or {}).get('score','—')} / 30"
    txt["maScore"] = f"{(comps.get('ma') or {}).get('score','—')} / 30"
    txt["rsiScore"] = f"{(comps.get('rsi') or {}).get('score','—')} / 20"
    txt["stochScore"] = f"{(comps.get('stochastic') or {}).get('score','—')} / 20"
    txt["macdScoreState"] = (comps.get("macd") or {}).get("state", "확인 필요")
    txt["maScoreState"] = (comps.get("ma") or {}).get("state", "확인 필요")
    txt["rsiScoreState"] = (comps.get("rsi") or {}).get("state", "확인 필요")
    txt["stochScoreState"] = (comps.get("stochastic") or {}).get("state", "확인 필요")

    gap20, gap60 = tech.get("gap20"), tech.get("gap60")
    txt["maGapInfo"] = (
        f"MA20 괴리 {'—' if gap20 is None else f'{gap20:+.1f}%'} · "
        f"MA60 괴리 {'—' if gap60 is None else f'{gap60:+.1f}%'} [AI]"
    )
    txt["volumeInfo"] = (
        "—" if tech.get("volume_ratio") is None
        else f"20D 평균 대비 {tech['volume_ratio']:.2f}배 [AI]"
    )
    txt["atrInfo"] = "—" if tech.get("atr14") is None else f"ATR14 {fmt_price(tech['atr14'])}원 [AI]"
    txt["gapInfo"] = "—" if tech.get("gap_pct") is None else f"시가 Gap {tech['gap_pct']:+.2f}% [AI]"
    txt["turnoverInfo"] = format_amount(tech.get("turnover"))
    txt["high52Info"] = (
        ("52주 신고가" if tech.get("new_high") else f"52주 고점 {fmt_price(tech.get('high52'))}원")
        + " [AI]"
    )
    divs = score.get("divergence") or []
    txt["divergence"] = " · ".join(divs) + " [AI]" if divs else "뚜렷한 RSI/MACD Divergence 없음 [AI]"
    confirmations = score.get("confirmation") or []
    txt["technicalConfirmation"] = " · ".join(confirmations) + " [AI]" if confirmations else "4개 지표 동조 신호 추가 확인 필요 [AI]"

    # Price strategy uses only MA/support/resistance/high-low/ATR-derived values.
    strategy_map = {
        "current": ("tpCurrent",),
        "entry1": ("tpEntry1",),
        "entry2": ("tpEntry2",),
        "add": ("tpAdd",),
        "target1": ("tpTarget1",),
        "target2": ("tpTarget2",),
        "stop": ("tpStop",),
    }
    for key, (text_key,) in strategy_map.items():
        item = strategy.get(key) or {}
        val = item.get("price")
        reason = item.get("reason") or "확인 필요"
        txt[text_key] = "—" if val is None else f"{fmt_price(val)}원 [AI]"
        txt[text_key + "Basis"] = reason + (" [AI]" if reason != "현재 종가" else "")

    if "Consensus 목표가 확인 필요" in str(txt.get("fairPriceText", "")):
        tech_target = (strategy.get("target2") or {}).get("price")
        if tech_target is not None:
            txt["fairPriceText"] = f"{fmt_price(tech_target)}원 기술목표 [AI]"
            inp["target2"] = str(int(round(tech_target)))

    # Keep the read-only Trade Plan synchronized with the new technical strategy.
    def set_input(input_key, strat_key):
        item = strategy.get(strat_key) or {}
        if item.get("price") is not None:
            inp[input_key] = str(int(round(item["price"])))

    set_input("buy1", "entry1")
    set_input("buy2", "entry2")
    set_input("stopPrice", "stop")
    set_input("target1", "target1")
    set_input("target2", "target2")

    r20, r60 = tech.get("r20"), tech.get("r60")
    txt["technicalComment"] = (
        f"Trading Score {total}/100 ({grade}/{grade_label}), 추세 {score.get('trend')}, "
        f"과열도 {score.get('overheat')}. 현재가 {fmt_price(price)}원, "
        f"20일 {signed(r20)}, 60일 {signed(r60)}, RSI14 "
        f"{'—' if rv is None else f'{rv:.1f}'}, 지지 {fmt_price(sup)}원 / 저항 {fmt_price(res)}원. [AI]"
    )
    txt["tradingConclusion"] = (
        f"Trading Score {total}/100 | 추세: {score.get('trend')} | "
        f"모멘텀: {(comps.get('macd') or {}).get('state','확인 필요')} | "
        f"과열도: {score.get('overheat')} | 전략: {score.get('action')} [AI]"
    )
    if gap20 is not None and gap20 > 8:
        txt["tradingEntryCondition"] = "현재 추격매수보다 MA20 괴리 축소 후 Stochastic 재골든크로스 확인 시 진입 우위. [AI]"
    elif score.get("action") == "신규진입":
        txt["tradingEntryCondition"] = "MA20 지지 유지와 MACD Histogram 개선이 이어질 때 분할 진입 우위. [AI]"
    else:
        txt["tradingEntryCondition"] = "MA20 방향과 MACD·Stochastic 동조가 확인될 때까지 신규 진입은 보수적으로 접근. [AI]"

    apply_kosdaq_scenario(p, tech)

    live_date = tech.get("date") or NOW.date().isoformat()
    txt["sourceMarketDate"] = f"{live_date} 종가"
    txt["topPickTicker"] = f"{p.get('stock_code','')} · {p.get('market','KOSPI')} · 기준일 {live_date}"
    txt["footerDataNote"] = f"데이터 기준: {live_date} 종가. 최신 반영형 대시보드이며 자체 점수·계산·매매전략은 참고용입니다. 투자 권유가 아닙니다."
    txt["sourceTopPick"] = f"{p.get('name','')} · 선택 종목별 상세 분석"
    txt["metricSource"] = f"네이버증권 연계 FnGuide · Forward PER/EV·EBITDA · 가격 기준 {live_date}"


def update_theme_cards(d, live_by_code, refresh_selection=False, selection_as_of=None):
    """Refresh theme prices daily, but re-score/re-rank recommendations monthly.

    Theme candidate membership remains the curated watchlist for each theme.
    At the monthly refresh, the three candidates are re-ranked using:
      base Fundamental/Catalyst 50% + Trading Score 35% + 20D/60D momentum 15%.
    Daily runs only update price, r20/r60 and trend state so the monthly ranking
    is not churned by short-term noise.
    """
    cards = d.get("theme_cards") or []
    for card in cards:
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

            if refresh_selection:
                base_score = float(p.get("base_score") or p.get("score") or 50)
                p["base_score"] = round(base_score, 2)
                trading_score = float((((tech.get("trading_score") or {}).get("total")) or 50))
                r20 = float(tech.get("r20") or 0)
                r60 = float(tech.get("r60") or 0)
                momentum_score = clamp(50.0 + 1.2 * r20 + 0.4 * r60, 0.0, 100.0)
                monthly_score = (
                    base_score * 0.50
                    + trading_score * 0.35
                    + momentum_score * 0.15
                )
                p["score"] = round(monthly_score, 1)
                p["monthly_trading_score"] = round(trading_score, 1)
                p["monthly_momentum_score"] = round(momentum_score, 1)
                p["selection_as_of"] = selection_as_of or tech.get("date") or NOW.date().isoformat()

        if refresh_selection:
            card["picks"] = sorted(
                card.get("picks") or [],
                key=lambda x: (
                    -float(x.get("score") or 0),
                    str(x.get("name") or ""),
                ),
            )[:3]
            scores = [float(p.get("score") or 0) for p in card.get("picks") or []]
            if scores:
                card["theme_score"] = round(statistics.mean(scores), 1)
            card["selection_as_of"] = selection_as_of or NOW.date().isoformat()
            card["selection_cycle"] = "MONTHLY_CLOSE"
        else:
            # Preserve the monthly recommendation score/order between month-end refreshes.
            # Only live price/momentum/trend fields move every day.
            if card.get("selection_as_of"):
                card["selection_cycle"] = "MONTHLY_CLOSE"




def _sector_clamp(v, lo=0.0, hi=100.0):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return 50.0


def _median(values, default=None):
    vals = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return statistics.median(vals) if vals else default


def _signed_score(value, scale=1.0, base=50.0):
    if value is None:
        return base
    return _sector_clamp(base + float(value) * scale)


def _qualitative_score(text, positives, negatives, base=50.0, step=8.0):
    s = str(text or "").lower()
    pos = sum(1 for token in positives if token in s)
    neg = sum(1 for token in negatives if token in s)
    return _sector_clamp(base + (pos - neg) * step)


def _fetch_kosai_qualitative(code):
    url = f"https://raw.githubusercontent.com/kosairesearch/kosairesearch.github.io/main/data/reports_v2/{code}.json"
    try:
        j = req(url, 1).json()
    except Exception:
        return {"text": "", "source": None}
    parts = [
        str((j.get("business") or {}).get("ko") or ""),
        str((j.get("industry") or {}).get("ko") or ""),
        str((j.get("outlook") or {}).get("ko") or ""),
        str((j.get("valuation_comment") or {}).get("ko") or ""),
    ]
    for row in j.get("keypoints") or []:
        parts.append(str((row or {}).get("ko") or ""))
    for row in j.get("risks") or []:
        parts.append(str(((row or {}).get("cat") or {}).get("ko") or ""))
        parts.append(str(((row or {}).get("body") or {}).get("ko") or ""))
    return {"text": " ".join(parts), "source": url}


def _sector_member_snapshot(stock):
    code = str(stock.get("code") or "").zfill(6)
    out = {
        "code": code,
        "name": stock.get("name") or code,
        "marketcap": stock.get("marketcap") or 0,
        "r20": None, "r60": None,
        "rev_qoq": None, "fwd_rev_growth": None,
        "opm": None, "opm_delta": None, "fwd_opm_delta": None,
        "forward_eps": None, "eps_growth": None,
        "fper": None, "pbr": None,
        "qual_text": "",
    }
    try:
        rows, _ = naver_stock_chart(code, 120)
        tech = technicals(rows)
        out["r20"], out["r60"] = tech.get("r20"), tech.get("r60")
    except Exception:
        pass
    try:
        q = fetch_quarterly_financials(code).get("quarters") or []
        actual = [x for x in q if not x.get("estimate")]
        latest = actual[-1] if actual else None
        prev = actual[-2] if len(actual) >= 2 else None
        est = next((x for x in q if x.get("estimate")), None)
        if latest:
            out["rev_qoq"] = latest.get("qoq")
            out["opm"] = latest.get("opm")
        if latest and prev and latest.get("opm") is not None and prev.get("opm") is not None:
            out["opm_delta"] = latest["opm"] - prev["opm"]
        if latest and est:
            if latest.get("revenue") not in (None, 0) and est.get("revenue") is not None:
                out["fwd_rev_growth"] = pct(est["revenue"], latest["revenue"])
            if latest.get("opm") is not None and est.get("opm") is not None:
                out["fwd_opm_delta"] = est["opm"] - latest["opm"]
            if latest.get("eps") not in (None, 0) and est.get("eps") is not None:
                out["eps_growth"] = pct(est["eps"], latest["eps"])
    except Exception:
        pass
    try:
        val = fetch_stock_valuation_snapshot(code)
        out["forward_eps"] = val.get("forward_eps")
        out["fper"] = val.get("forward_per") or val.get("per")
        out["pbr"] = val.get("pbr")
    except Exception:
        pass
    qual = _fetch_kosai_qualitative(code)
    out["qual_text"] = qual.get("text") or ""
    return out


def _sector_cycle_label(score):
    score = float(score or 0)
    if score >= 88:
        return "최상위 성장"
    if score >= 78:
        return "성장"
    if score >= 68:
        return "우호적 확장"
    if score >= 55:
        return "중립"
    if score >= 42:
        return "둔화"
    if score >= 28:
        return "하강"
    return "침체"


def _sector_actual_cycle(level, momentum, peak_warning=False, early_recovery=False):
    if early_recovery:
        return "초기회복"
    if peak_warning:
        return "Peak"
    if level >= 75 and momentum >= 68:
        return "호황"
    if level < 48 and momentum >= 56:
        return "초기회복"
    if momentum >= 55:
        return "성장"
    if level >= 48 and momentum < 48:
        return "둔화"
    if level < 45 and momentum < 45:
        return "침체"
    return "성장"


def _valuation_score(theme_key, members):
    fpers = [x.get("fper") for x in members if x.get("fper") is not None and x.get("fper") > 0]
    pbrs = [x.get("pbr") for x in members if x.get("pbr") is not None and x.get("pbr") > 0]
    if theme_key in ("finance", "insurance"):
        pbr = _median(pbrs)
        if pbr is None:
            return 50.0, None
        score = 92 if pbr <= 0.6 else 82 if pbr <= 0.9 else 70 if pbr <= 1.2 else 58 if pbr <= 1.6 else 42 if pbr <= 2.2 else 28
        return float(score), pbr
    fper = _median(fpers)
    if fper is None:
        return 50.0, None
    score = 92 if fper <= 10 else 84 if fper <= 15 else 74 if fper <= 20 else 64 if fper <= 25 else 54 if fper <= 35 else 42 if fper <= 50 else 28
    return float(score), fper


def build_sector_cycle_snapshot(universe_payload, previous=None):
    cfg = json.loads(THEME_CONFIG_PATH.read_text(encoding="utf-8"))
    themes = cfg.get("sectors") or cfg.get("themes") or []
    universe = (universe_payload or {}).get("items") or []
    by_code = {str(x.get("code") or "").zfill(6): x for x in universe}
    previous = previous or {}
    prev_sectors = {x.get("key"): x for x in previous.get("sectors") or [] if x.get("key")}
    sector_rows = []

    for theme in themes:
        key = str(theme.get("key") or "")
        codes = [str(x).zfill(6) for x in theme.get("codes") or []]
        candidates = [by_code[c] for c in codes if c in by_code]
        candidates.sort(key=lambda x: (x.get("marketcap") or 0), reverse=True)
        reps = candidates[:3]

        members = []
        if reps:
            with ThreadPoolExecutor(max_workers=min(3, len(reps))) as ex:
                futs = [ex.submit(_sector_member_snapshot, x) for x in reps]
                for fut in as_completed(futs):
                    try:
                        members.append(fut.result())
                    except Exception:
                        pass

        text = " ".join(x.get("qual_text") or "" for x in members).lower()
        rev_qoq = _median([x.get("rev_qoq") for x in members])
        fwd_rev = _median([x.get("fwd_rev_growth") for x in members])
        opm = _median([x.get("opm") for x in members])
        opm_delta = _median([x.get("opm_delta") for x in members])
        fwd_opm_delta = _median([x.get("fwd_opm_delta") for x in members])
        r20 = _median([x.get("r20") for x in members])
        r60 = _median([x.get("r60") for x in members])

        demand_level = _signed_score(rev_qoq, 1.1)
        demand_momentum = _signed_score(fwd_rev if fwd_rev is not None else r20, 1.15)

        supply_level = _qualitative_score(
            text,
            ["재고 감소", "재고 정상화", "공급 부족", "가동률 상승", "감산", "공급 타이트"],
            ["재고 증가", "공급 과잉", "가동률 하락", "과잉 공급", "증설 급증"],
            50, 7,
        )
        supply_momentum = _sector_clamp((supply_level * 0.55) + (_signed_score(opm_delta, 8) * 0.45))

        price_level = _sector_clamp(50 + ((opm if opm is not None else 10) - 10) * 2.0)
        price_momentum = _signed_score(fwd_opm_delta if fwd_opm_delta is not None else opm_delta, 9)

        capex_level = _qualitative_score(
            text,
            ["수주잔고", "backlog", "capex 확대", "설비투자 확대", "증설", "장기계약", "신규수주", "수주 증가"],
            ["수주 감소", "수주 공백", "capex 축소", "투자 지연", "증설 부담"],
            52, 6,
        )
        capex_momentum = _qualitative_score(
            text,
            ["수주 증가", "수주 확대", "신규수주", "증설", "투자 확대", "계약 확대"],
            ["수주 둔화", "수주 감소", "투자 지연", "capex 축소"],
            capex_level, 7,
        )

        prev = prev_sectors.get(key) or {}
        prev_eps = prev.get("raw", {}).get("member_forward_eps") or {}
        revisions = []
        current_eps = {}
        eps_growth_proxy = []
        for m in members:
            code = m.get("code")
            feps = m.get("forward_eps")
            if feps is not None:
                current_eps[code] = feps
                old = prev_eps.get(code)
                if old not in (None, 0):
                    revisions.append(pct(feps, old))
            if m.get("eps_growth") is not None:
                eps_growth_proxy.append(m["eps_growth"])
        revision_pct = _median(revisions)
        if revision_pct is None:
            revision_pct = _median(eps_growth_proxy)
        revision_level = _signed_score(_median(eps_growth_proxy), 0.7)
        revision_momentum = _signed_score(revision_pct, 2.0)

        structural_level = float(theme.get("structural_base") or 70)
        structural_momentum = _sector_clamp(structural_level * 0.75 + _signed_score(r60, 0.5) * 0.25)

        catalyst_level = _qualitative_score(
            text,
            ["신제품", "신규 고객", "수주", "계약", "양산", "승인", "정책 지원", "수출", "점유율 확대", "국산화"],
            ["규제", "소송", "계약 지연", "승인 지연", "고객 이탈", "정책 불확실"],
            55, 6,
        )
        catalyst_momentum = _sector_clamp(catalyst_level * 0.7 + _signed_score(r20, 0.5) * 0.3)

        factors = {
            "demand": {"level": demand_level, "momentum": demand_momentum},
            "supply": {"level": supply_level, "momentum": supply_momentum},
            "price": {"level": price_level, "momentum": price_momentum},
            "capex": {"level": capex_level, "momentum": capex_momentum},
            "revision": {"level": revision_level, "momentum": revision_momentum},
            "structural": {"level": structural_level, "momentum": structural_momentum},
            "catalyst": {"level": catalyst_level, "momentum": catalyst_momentum},
        }
        factor_weights = {"demand": 20, "supply": 15, "price": 15, "capex": 15, "revision": 15, "structural": 10, "catalyst": 10}
        scored = {}
        cycle_score = 0.0
        level_total = 0.0
        momentum_total = 0.0
        for k, w in factor_weights.items():
            level = _sector_clamp(factors[k]["level"])
            momentum = _sector_clamp(factors[k]["momentum"])
            score = level * 0.4 + momentum * 0.6
            scored[k] = {"level": round(level, 1), "momentum": round(momentum, 1), "score": round(score, 1)}
            cycle_score += score * w / 100.0
            level_total += level * w / 100.0
            momentum_total += momentum * w / 100.0

        peak_signals = []
        if demand_level - demand_momentum >= 12: peak_signals.append("산업 성장/수요 Momentum 둔화")
        if capex_level - capex_momentum >= 12: peak_signals.append("수주/CAPEX Momentum 둔화")
        if price_level - price_momentum >= 12: peak_signals.append("ASP/Margin Momentum 둔화")
        if supply_momentum <= 42: peak_signals.append("재고/가동률 Balance 악화")
        if revision_momentum <= 42: peak_signals.append("EPS Revision 둔화/하향")
        if r20 is not None and r20 >= 20 and revision_momentum < 55: peak_signals.append("주가 Momentum이 이익 Revision을 상회")

        recovery_signals = []
        if supply_momentum >= 58: recovery_signals.append("재고/공급 Balance 개선")
        if price_momentum >= 58: recovery_signals.append("가격/Margin 바닥 통과")
        if capex_momentum >= 58: recovery_signals.append("수주/CAPEX 회복")
        if demand_momentum >= 58: recovery_signals.append("수요 Momentum 회복")
        if revision_momentum >= 58: recovery_signals.append("EPS Revision 상향")

        peak_warning = len(peak_signals) >= 3
        early_recovery = level_total < 55 and len(recovery_signals) >= 3
        actual_cycle = _sector_actual_cycle(level_total, momentum_total, peak_warning, early_recovery)
        cycle_score = round(_sector_clamp(cycle_score), 1)
        cycle_label = _sector_cycle_label(cycle_score)

        valuation_score, valuation_raw = _valuation_score(key, members)
        relative_strength = _sector_clamp(50 + (r20 or 0) * 0.8 + (r60 or 0) * 0.25)
        catalyst_score = scored["catalyst"]["score"]
        investment_score = round(_sector_clamp(cycle_score * 0.65 + valuation_score * 0.15 + relative_strength * 0.10 + catalyst_score * 0.10), 1)

        strongest = sorted(scored.items(), key=lambda kv: kv[1]["score"], reverse=True)[:2]
        factor_names = {"demand":"수요","supply":"공급/재고","price":"ASP/Margin","capex":"CAPEX/수주","revision":"Revision","structural":"구조적 성장","catalyst":"Catalyst"}
        reason = " · ".join(f"{factor_names[k]} {v['score']:.0f}" for k, v in strongest)
        if members:
            reason += " | 대표: " + ", ".join(x.get("name") or x.get("code") for x in sorted(members, key=lambda x: x.get("marketcap") or 0, reverse=True))

        sector_rows.append({
            "key": key,
            "name": theme.get("label"),
            "actual_cycle": actual_cycle,
            "cycle_attractiveness": cycle_label,
            "cycle_score": cycle_score,
            "investment_score": investment_score,
            "valuation_score": round(valuation_score, 1),
            "relative_strength_score": round(relative_strength, 1),
            "catalyst_score": round(catalyst_score, 1),
            "factors": scored,
            "peak_warning": peak_warning,
            "peak_signals": peak_signals,
            "early_recovery": early_recovery,
            "recovery_signals": recovery_signals,
            "reason": reason or "대표 종목 데이터 추가 확인",
            "growth": theme.get("growth") or "구조적 성장동력 확인",
            "subsectors": theme.get("subsectors") or [],
            "themes": theme.get("themes") or [],
            "representatives": [{"code": x.get("code"), "name": x.get("name")} for x in members],
            "raw": {
                "member_forward_eps": current_eps,
                "median_fper_or_pbr": valuation_raw,
                "median_r20": r20,
                "median_r60": r60,
                "median_rev_qoq": rev_qoq,
                "median_fwd_rev_growth": fwd_rev,
                "median_opm": opm,
                "median_opm_delta": opm_delta,
                "median_revision_pct": revision_pct,
            },
        })

    sector_rows.sort(key=lambda x: x.get("investment_score") or 0, reverse=True)
    return {
        "as_of": (universe_payload or {}).get("as_of") or NOW.date().isoformat(),
        "generated_at": NOW.isoformat(),
        "sector_count": len(themes),
        "theme_count": len(themes),
        "method": f"{len(themes)} Sector Universe · Sector > Sub-sector > Theme · representative stocks daily refresh · Level 40% + Momentum 60%",
        "source_note": "Naver Stock price/quarterly/valuation + KOSAI qualitative evidence. 직접 산업 재고·ASP·수주 데이터가 없는 항목은 중립값에서 확인 가능한 실적/정성 Evidence만 반영.",
        "sectors": sector_rows,
    }


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

    universe_meta = None
    try:
        universe_meta = refresh_stock_universe()
    except Exception as e:
        errors.append(f"stock universe refresh: {e}")
        try:
            universe_meta = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
        except Exception:
            universe_meta = None

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

    try:
        d["sector_cycle"] = build_sector_cycle_snapshot(universe_meta, d.get("sector_cycle") or {})
    except Exception as e:
        errors.append(f"sector cycle refresh: {e}")
        old_sector = d.get("sector_cycle") or {}
        if old_sector:
            old_sector["refreshed_this_run"] = False
            old_sector["refresh_error"] = str(e)
            d["sector_cycle"] = old_sector

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

    previous_profiles = d.get("stock_profiles") or []
    if not previous_profiles:
        print(json.dumps({"status": "rejected", "reason": "KOSPI profile template missing"}, ensure_ascii=False))
        return 4

    profiles = build_kospi_profiles(d, previous_profiles, live_by_code)
    if len(profiles) < 5:
        print(json.dumps({"status": "rejected", "reason": "KOSPI dynamic TOP5 insufficient"}, ensure_ascii=False))
        return 4

    d["stock_profiles"] = profiles
    kosdaq_profiles = build_kosdaq_profiles(d, profiles[0], live_by_code)
    # Zero qualified KOSDAQ names is a valid market state.
    # Keep updating KOSPI/market data and publish KOSDAQ 0/5 instead of aborting the whole site.

    all_profiles = profiles[:5] + kosdaq_profiles

    quarterly_ok = 0
    for p in all_profiles:
        try:
            snap = fetch_quarterly_financials(p.get("stock_code"))
            if apply_quarterly_financials(p, snap):
                quarterly_ok += 1
        except Exception as e:
            errors.append(f"{p.get('name')} quarterly: {e}")

    # Dynamic TOP5 must keep rotating even if one newly selected name temporarily
    # lacks a next-quarter consensus column. Record the gap instead of freezing
    # the entire recommendation list.
    if quarterly_ok < 5:
        errors.append(f"KOSPI TOP5 quarterly finance partial: {quarterly_ok}/5")

    for p in all_profiles:
        try:
            snap = fetch_stock_valuation_snapshot(p.get("stock_code"))
            apply_valuation_snapshot(p, snap)
        except Exception as e:
            errors.append(f"{p.get('name')} valuation: {e}")
        profile_live_update(p, live_by_code.get(p.get("stock_code")))

    reset_profile_scores(profiles)
    reset_profile_scores(kosdaq_profiles)
    synchronize_profile_group_rows(profiles)
    synchronize_profile_group_rows(kosdaq_profiles)
    d["stock_profile_groups"] = {"KOSPI": profiles[:5], "KOSDAQ": kosdaq_profiles}
    d["stock_profiles_kosdaq"] = kosdaq_profiles

    prev_theme_month = str((d.get("meta") or {}).get("theme_selection_month") or "")
    force_theme_refresh = str(os.getenv("THEME_MONTHLY_REFRESH", "0")).lower() in ("1", "true", "yes")
    initialize_theme_refresh = not prev_theme_month
    refresh_theme_selection = force_theme_refresh or initialize_theme_refresh
    update_theme_cards(
        d,
        live_by_code,
        refresh_selection=refresh_theme_selection,
        selection_as_of=md,
    )

    # Refresh TOP5 summary cards/bind without touching verified financial snapshot fields.
    bind = d.setdefault("bind", {})
    for i, p in enumerate(profiles[:5], 1):
        live = live_by_code.get(p.get("stock_code"))
        if live:
            bind[f"stock{i}Price"] = fmt_price(live["tech"]["price"])
        target = n((p.get("inputs") or {}).get("target2"))
        if target:
            bind[f"stock{i}Target"] = f"{fmt_price(target)} Cons."

    # Keep initial page synchronized with TOP1 profile, then restore the global
    # market snapshot so stale per-profile market fields can never overwrite it.
    if profiles:
        d["text"].update(profiles[0].get("text") or {})
        d["inputs"].update(profiles[0].get("inputs") or {})

    md = update_market_text(d, market)
    if sentiment.get("regime"):
        d.setdefault("text", {})["marketRegime"] = sentiment["regime"]

    market_text_keys = (
        "heroDesc", "marketRegime", "kospi", "kospiDelta", "kosdaq", "kosdaqDelta",
        "nasdaq", "nasdaqDelta", "nasdaqDataDate", "fx", "fxDelta", "us10y", "us10yDelta",
        "flowState", "flowDesc", "kospiFlow", "kosdaqFlow", "marketReason1", "marketReason2",
        "marketReason3", "marketImpact", "sourceMarketDate", "sourceKR", "sourceUS",
    )
    for p in all_profiles:
        ptxt = p.setdefault("text", {})
        for key in market_text_keys:
            if key in d.get("text", {}):
                ptxt[key] = d["text"][key]

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
        "financial_snapshot_date": NOW.date().isoformat(),
        "status": "ok",
        "ui_version": "v19-dynamic-kospi-sentiment-regime",
        "profile_count": 5 + len(kosdaq_profiles),
        "profile_groups": {"KOSPI": 5, "KOSDAQ": len(kosdaq_profiles)},
        "financial_snapshot_note": "TOP5 최근 3개 실제 분기 + 다음 분기(E) Consensus 자동 연결.",
        "financial_policy": "Naver Stock/FnGuide quarterly JSON · 다음 분기 Consensus 필수 · OPM/QoQ 자체계산 [AI].",
        "technical_policy": "MACD 30 + MA/괴리율 30 + RSI 20 + Stochastic 20 = Trading Score 100 [AI].",
        "stock_sentiment_policy": "시장 공포탐욕지수는 종목 투자점수에 반영하지 않음.",
        "kosdaq_selection_policy": "Trading Score 60 미만 제외 · 70 이상 우선 · Fundamental/Catalyst 60% + Trading Score 40% [AI].",
        "kospi_selection_policy": "Watchlist 전체 재스크리닝 · Fundamental/Catalyst 60% + Trading Score 40% · Trading 50 미만 기본 제외 · 70 이상 우선 [AI].",
        "korea_sentiment_policy": "20D/60D 추세 우선 · 절대 변동성 패널티 · 외인/기관 수급 포함 · 20D 급락 Regime Gate [AI].",
        "theme_selection_policy": "테마 추천 3종목은 월 종가 확정 후 월 1회 재평가. Base Fundamental/Catalyst 50% + Trading Score 35% + 20D/60D Momentum 15% [AI].",
        "theme_selection_month": (md[:7] if refresh_theme_selection and md else prev_theme_month),
        "theme_selection_as_of": (md if refresh_theme_selection and md else (d.get("meta") or {}).get("theme_selection_as_of")),
        "theme_selection_updated_at": (NOW.isoformat() if refresh_theme_selection else (d.get("meta") or {}).get("theme_selection_updated_at")),
        "theme_selection_refreshed_this_run": bool(refresh_theme_selection),
        "stock_universe": {
            "as_of": (universe_meta or {}).get("as_of"),
            "generated_at": (universe_meta or {}).get("generated_at"),
            "count": (universe_meta or {}).get("count"),
            "source": (universe_meta or {}).get("source"),
        },
        "sector_cycle": {
            "as_of": (d.get("sector_cycle") or {}).get("as_of"),
            "generated_at": (d.get("sector_cycle") or {}).get("generated_at"),
            "sector_count": (d.get("sector_cycle") or {}).get("sector_count"),
            "theme_count": (d.get("sector_cycle") or {}).get("theme_count"),
            "method": (d.get("sector_cycle") or {}).get("method"),
        },
        "coverage": {
            "stocks_fresh": len(live_by_code),
            "stocks_total": len(stocks),
            "stock_fresh_ratio": round(stock_ratio, 4),
            "market_fresh": market_fresh,
            "market_total": len(market),
            "market_fresh_ratio": round(market_ratio, 4),
        },
        "errors": errors[:20],
        "note": "실시간 가격/기술지표·Trading Score 갱신. TOP5 실적은 최근 분기 + 다음 분기(E) Consensus 기준입니다.",
    })

    meta.pop("nasdaq_refresh_note", None)
    meta["market_sources"] = {
        key: {
            "value": (market.get(key) or {}).get("value"),
            "change_pct": (market.get(key) or {}).get("change_pct"),
            "date": (market.get(key) or {}).get("date"),
            "source": (market.get(key) or {}).get("source"),
        }
        for key in ("kospi", "kosdaq", "nasdaq", "usdkrw", "us10y")
    }

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
