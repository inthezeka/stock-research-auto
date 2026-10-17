#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "fundamental_themes.json"
UNIVERSE_PATH = ROOT / "stock-universe.json"
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 SectorMembershipBot/1.0",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.5",
})

GENERIC = {
    "및", "관련", "기타", "산업", "사업", "시장", "성장", "제품", "서비스", "기업",
    "the", "and", "of", "for", "co", "corp", "inc", "k", "ai",
}
CATEGORY_STOP = {
    "기타", "지주", "지주회사", "서비스", "제조", "제조업", "산업", "소비재", "IT",
}


def norm(value):
    s = str(value or "").lower()
    s = re.sub(r"[^0-9a-z가-힣+.-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_phrases(value):
    parts = re.split(r"[/·,;|()\[\]]+", str(value or ""))
    out = []
    for part in parts:
        p = norm(part)
        if len(p) < 2 or p in GENERIC:
            continue
        out.append(p)
    return out


def sector_signature(theme):
    weighted = {}
    for p in split_phrases(theme.get("label")):
        weighted[p] = max(weighted.get(p, 0), 4.0)
    for p in theme.get("subsectors") or []:
        p = norm(p)
        if len(p) >= 2 and p not in GENERIC:
            weighted[p] = max(weighted.get(p, 0), 3.0)
    for p in theme.get("themes") or []:
        p = norm(p)
        if len(p) >= 2 and p not in GENERIC:
            weighted[p] = max(weighted.get(p, 0), 2.5)
    for p in split_phrases(theme.get("growth")):
        weighted[p] = max(weighted.get(p, 0), 1.5)
    return weighted


def fetch_profile(stock):
    code = str(stock.get("code") or "").zfill(6)
    url = f"https://raw.githubusercontent.com/kosairesearch/kosairesearch.github.io/main/data/reports_v2/{code}.json"
    try:
        r = SESSION.get(url, timeout=(2.5, 5.0))
        if r.status_code != 200:
            return None
        j = r.json()
    except Exception:
        return None

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

    categories = []
    if j.get("sector"):
        categories.append(str(j.get("sector")))
    categories.extend(str(x) for x in (j.get("categories") or []) if x)

    return {
        "code": code,
        "name": stock.get("name") or j.get("name") or code,
        "market": stock.get("market"),
        "marketcap": float(stock.get("marketcap") or 0),
        "turnover": float(stock.get("turnover") or 0),
        "text": norm(" ".join(parts)),
        "categories": [norm(x) for x in categories if norm(x)],
        "report_date": j.get("reportDate") or j.get("dataDate"),
        "source": url,
    }


def relevance(profile, theme, signature):
    text = profile.get("text") or ""
    cats = profile.get("categories") or []
    label_parts = split_phrases(theme.get("label"))
    score = 0.0
    hits = []

    for lp in label_parts:
        for cat in cats:
            if lp == cat or lp in cat or cat in lp:
                score += 8.0
                hits.append(f"category:{lp}")
                break

    for phrase, weight in signature.items():
        if phrase in text:
            score += weight
            hits.append(phrase)
        elif any(phrase in cat or cat in phrase for cat in cats if len(cat) >= 2):
            score += weight * 1.5
            hits.append(f"category:{phrase}")

    return round(score, 2), hits[:10]


def candidate_universe(universe, seed_codes):
    items = list(universe.get("items") or [])
    by_code = {str(x.get("code") or "").zfill(6): x for x in items}

    marketcap_top = sorted(items, key=lambda x: float(x.get("marketcap") or 0), reverse=True)[:300]
    turnover_top = sorted(items, key=lambda x: float(x.get("turnover") or 0), reverse=True)[:180]

    chosen = {}
    for x in marketcap_top + turnover_top:
        code = str(x.get("code") or "").zfill(6)
        if re.fullmatch(r"\d{6}", code):
            chosen[code] = x

    for code in seed_codes:
        if code in by_code:
            chosen[code] = by_code[code]

    return list(chosen.values())


def refresh_membership(force=False):
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    universe = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    sectors = cfg.get("sectors") or []
    as_of = str(universe.get("as_of") or NOW.date().isoformat())
    selection_month = as_of[:7]
    meta = cfg.get("membership_meta") or {}
    last_month = str(meta.get("selection_month") or "")

    if not force and last_month == selection_month:
        print(json.dumps({
            "status": "skipped",
            "reason": "already refreshed this month",
            "selection_month": selection_month,
        }, ensure_ascii=False))
        return False

    seed_codes = set()
    seed_by_sector = {}
    for sector in sectors:
        current = [str(x).zfill(6) for x in (sector.get("codes") or []) if str(x).strip()]
        seed = [str(x).zfill(6) for x in (sector.get("seed_codes") or current) if str(x).strip()]
        sector["seed_codes"] = list(dict.fromkeys(seed))
        seed_by_sector[sector.get("key")] = set(sector["seed_codes"])
        seed_codes.update(sector["seed_codes"])

    candidates = candidate_universe(universe, seed_codes)
    profiles = []
    if candidates:
        with ThreadPoolExecutor(max_workers=min(24, len(candidates))) as ex:
            futures = {ex.submit(fetch_profile, stock): stock for stock in candidates}
            for fut in as_completed(futures):
                try:
                    p = fut.result()
                    if p:
                        profiles.append(p)
                except Exception:
                    pass

    signatures = {s.get("key"): sector_signature(s) for s in sectors}
    sector_auto = {s.get("key"): [] for s in sectors}
    unmatched = []

    for p in profiles:
        ranked = []
        for sector in sectors:
            key = sector.get("key")
            sc, hits = relevance(p, sector, signatures.get(key) or {})
            ranked.append((sc, key, hits))
        ranked.sort(reverse=True, key=lambda x: x[0])
        top = ranked[0] if ranked else (0, None, [])
        second = ranked[1] if len(ranked) > 1 else (0, None, [])

        assigned = []
        if top[0] >= 8.0 and (top[0] - second[0] >= 1.5 or top[0] >= 12.0):
            assigned.append(top)
            if second[0] >= 10.0 and second[0] >= top[0] * 0.85:
                assigned.append(second)
            if len(ranked) > 2 and ranked[2][0] >= 12.0 and ranked[2][0] >= top[0] * 0.90:
                assigned.append(ranked[2])

        if assigned:
            for sc, key, hits in assigned[:3]:
                if p["code"] in (seed_by_sector.get(key) or set()):
                    continue
                sector_auto.setdefault(key, []).append({
                    "code": p["code"],
                    "name": p["name"],
                    "score": round(sc, 1),
                    "marketcap": p["marketcap"],
                    "hits": hits[:5],
                })
        elif top[0] < 6.0:
            unmatched.append(p)

    total_auto = 0
    for sector in sectors:
        key = sector.get("key")
        rows = sector_auto.get(key) or []
        rows.sort(key=lambda x: (-x["score"], -x["marketcap"], x["name"]))
        auto_codes = []
        auto_detail = []
        seed = list(dict.fromkeys(sector.get("seed_codes") or []))
        for row in rows:
            if row["code"] in seed or row["code"] in auto_codes:
                continue
            auto_codes.append(row["code"])
            auto_detail.append(row)
            if len(auto_codes) >= 20:
                break

        sector["auto_codes"] = auto_codes
        sector["auto_members"] = auto_detail
        sector["codes"] = list(dict.fromkeys(seed + auto_codes))[:60]
        sector["membership_updated_at"] = NOW.isoformat()
        total_auto += len(auto_codes)

    existing_old = {norm(x.get("label")): x for x in (cfg.get("new_sector_candidates") or [])}
    existing_terms = set()
    for sector in sectors:
        existing_terms.update(split_phrases(sector.get("label")))
        existing_terms.update(norm(x) for x in (sector.get("subsectors") or []) if norm(x))

    groups = {}
    for p in unmatched:
        for cat in p.get("categories") or []:
            if not cat or cat in CATEGORY_STOP or len(cat) < 2:
                continue
            if any(cat == t or cat in t or t in cat for t in existing_terms if len(t) >= 2):
                continue
            g = groups.setdefault(cat, {"members": {}, "marketcap": 0.0})
            if p["code"] not in g["members"]:
                g["members"][p["code"]] = {"code": p["code"], "name": p["name"], "marketcap": p["marketcap"]}
                g["marketcap"] += p["marketcap"]

    proposals = []
    for label, g in groups.items():
        members = sorted(g["members"].values(), key=lambda x: -x["marketcap"])
        if len(members) < 3:
            continue
        old = existing_old.get(label) or {}
        proposals.append({
            "label": label,
            "status": "pending",
            "first_detected_at": old.get("first_detected_at") or NOW.isoformat(),
            "last_detected_at": NOW.isoformat(),
            "member_count": len(members),
            "aggregate_marketcap": round(g["marketcap"], 0),
            "members": [{"code": x["code"], "name": x["name"]} for x in members[:10]],
            "note": "기존 36개 Sector와 낮은 연관도로 분류된 KOSAI sector/category 군집. 신규 Sector 생성 전 사용자 승인 필요.",
        })
    proposals.sort(key=lambda x: (-x["member_count"], -x["aggregate_marketcap"], x["label"]))
    cfg["new_sector_candidates"] = proposals[:12]

    cfg["membership_meta"] = {
        "selection_month": selection_month,
        "as_of": as_of,
        "updated_at": NOW.isoformat(),
        "policy": "Seed codes 유지 + KRX 상위 시총/거래대금 후보의 KOSAI 사업·산업·카테고리 텍스트 월 1회 재분류",
        "candidate_count": len(candidates),
        "profile_coverage": len(profiles),
        "auto_member_count": total_auto,
        "new_sector_candidate_count": len(cfg["new_sector_candidates"]),
        "approval_policy": "기존 Sector 소속 auto_codes는 자동 반영. 신규 Sector 자체는 pending 후보만 생성하며 사용자 승인 전 자동 생성하지 않음.",
    }
    cfg["updated_at"] = NOW.date().isoformat()

    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(CONFIG_PATH)

    print(json.dumps({
        "status": "ok",
        "selection_month": selection_month,
        "candidate_count": len(candidates),
        "profile_coverage": len(profiles),
        "auto_member_count": total_auto,
        "new_sector_candidates": [x["label"] for x in cfg["new_sector_candidates"]],
    }, ensure_ascii=False))
    return True


def main():
    force = str(os.getenv("SECTOR_MEMBERSHIP_REFRESH", "")).lower() in ("1", "true", "yes")
    force = force or str(os.getenv("THEME_MONTHLY_REFRESH", "")).lower() in ("1", "true", "yes")
    refresh_membership(force=force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
