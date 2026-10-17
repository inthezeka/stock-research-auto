#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "analyst-research-cache.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
BASE = "https://stock.naver.com/api/stockSecurity/researches/v2/company"
PAGE_SIZE = 100
MAX_PAGES = 80

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 stock-research-auto/1.0",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://stock.naver.com/research/company",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
})

def pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None

def nested_name(v):
    if isinstance(v, dict):
        return pick(v, "name", "brokerName", "companyName", "title")
    return v

def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s else None
    except Exception:
        return None

def code_of(x):
    candidates = [
        pick(x, "itemCode", "itemcode", "stockCode", "stock_code", "code", "securityCode", "cmpCd", "cmp_cd"),
    ]
    for k in ("item", "stock", "security", "company"):
        if isinstance(x.get(k), dict):
            candidates.append(pick(x[k], "itemCode", "itemcode", "stockCode", "code", "securityCode", "cmpCd"))
    for v in candidates:
        if v is None:
            continue
        s = re.sub(r"[^0-9A-Za-z]", "", str(v)).upper()
        if len(s) >= 6:
            s = s[-6:]
        if len(s) == 6:
            return s
    return None

def target_of(x):
    for k in ("targetPrice","goalPrice","priceTarget","target_price","goal_price","target","tp"):
        n = num(x.get(k))
        if n and n > 0:
            return n
    for k, v in x.items():
        if re.search(r"(target|goal).*(price)|price.*(target|goal)|목표.*주가", str(k), re.I):
            n = num(v)
            if n and n > 0:
                return n
    return None

def normalize(x):
    code = code_of(x)
    if not code:
        return None
    broker = nested_name(pick(x, "brokerName","broker","securitiesCompanyName","securitiesFirm","companyName","publisherName","bnm")) or "증권사 확인 필요"
    title = pick(x, "title","researchTitle","reportTitle","subject","tit","summary","description") or "리포트 내용 확인 필요"
    date = pick(x, "writeDate","publishDate","publishedAt","researchDate","regDate","createdAt","date","wdt") or ""
    analyst = nested_name(pick(x, "analystName","analyst","writerName","writer")) or ""
    return {
        "nid": pick(x, "nid","researchId","id"),
        "itemCode": code,
        "date": str(date),
        "broker": str(broker),
        "analyst": str(analyst),
        "title": re.sub(r"\s+", " ", str(title)).strip(),
        "targetPrice": target_of(x),
    }

def main():
    end = NOW.date()
    start = end - timedelta(days=93)
    all_items = []
    sample_keys = []
    has_next = True

    for index in range(MAX_PAGES):
        if not has_next:
            break
        params = {
            "index": index,
            "size": PAGE_SIZE,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        }
        r = session.get(BASE, params=params, timeout=(5, 20))
        r.raise_for_status()
        payload = r.json()
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise RuntimeError("research payload items is not a list")
        if items and len(sample_keys) < 3:
            sample_keys.extend([sorted(list(x.keys()))[:80] for x in items[:3] if isinstance(x, dict)])
        all_items.extend(items)
        has_next = bool(payload.get("hasNext"))
        if not items:
            break
        time.sleep(0.25)

    grouped = defaultdict(list)
    normalized = 0
    for raw in all_items:
        if not isinstance(raw, dict):
            continue
        row = normalize(raw)
        if not row:
            continue
        grouped[row["itemCode"]].append(row)
        normalized += 1

    for code, rows in grouped.items():
        rows.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
        grouped[code] = rows[:12]

    out = {
        "meta": {
            "updated_at": NOW.isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "source": "Naver Stock public research v2 company list",
            "raw_count": len(all_items),
            "normalized_count": normalized,
            "stock_count": len(grouped),
            "sample_keys": sample_keys,
        },
        "items": dict(grouped),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(OUT)
    print(json.dumps(out["meta"], ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
