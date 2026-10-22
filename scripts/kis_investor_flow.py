#!/usr/bin/env python3
"""Optional historical KIS investor-flow source (KRX cash market, shares).

Credentials come ONLY from server-side KIS_APP_KEY/KIS_APP_SECRET. They are
never written to git, JSON caches, HTML or logs. The data here is market-data,
NOT brokerage orders or account holdings.

Official KIS API:
POST /oauth2/tokenP (client_credentials)
GET /uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily
TR_ID FHPTJ04160001
output2: stck_bsop_date, frgn_ntby_qty, orgn_ntby_qty, prsn_ntby_qty.

Historical availability and data redistribution rights must be checked before
enabling this source for public-facing websites.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Callable

import requests

BASE_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_PATH = "/oauth2/tokenP"
DAILY_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
TR_ID = "FHPTJ04160001"
SOURCE = "KIS investor-trade-by-stock-daily · KRX"
TARGET_ROWS = 65
MAX_PAGES = 6
MIN_REQUEST_INTERVAL = 0.45


def safe_int(value):
    if value is None or isinstance(value, bool):
        return None
    v = str(value).strip().replace(",", "").replace(" ", "")
    if not re.fullmatch(r"[+-]?\\d+(?:\\.0+)?", v):
        return None
    try:
        return int(float(v))
    except (ValueError, OverflowError):
        return None


def safe_date(value):
    s = str(value or "").strip()
    if not re.fullmatch(r"20\\d{6}", s):
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def parse_output2(payload, cutoff):
    """Reject unavailable/estimated/partial rows rather than guessing."""
    body = payload if isinstance(payload, dict) else {}
    raw = body.get("output2") or []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        day = safe_date(item.get("stck_bsop_date"))
        if not day or day > cutoff:
            continue
        foreign = safe_int(item.get("frgn_ntby_qty"))
        institution = safe_int(item.get("orgn_ntby_qty"))
        individual = safe_int(item.get("prsn_ntby_qty"))
        if None in (foreign, institution, individual):
            continue
        normalized.append({
            "date": day,
            "foreign": foreign,
            "institution": institution,
            "individual": individual,
        })
    return sorted({row["date"]: row for row in normalized}.values(), key=lambda x: x["date"])


def collect_history(code, until, fetch_page: Callable[[str, str], dict],
                    target_rows=TARGET_ROWS, max_pages=MAX_PAGES):
    """Walk historical date cursors backward until >=60 verified sessions.

    The API may return only 30 rows per response. Never assume date offset or
    pagination succeeded: require strictly older earliest date on each page.
    """
    if not re.fullmatch(r"[0-9]{6}", str(code)):
        return []
    try:
        cursor = date.fromisoformat(until)
    except ValueError:
        return []
    out = {}
    oldest_previous = None
    for _ in range(max_pages):
        payload = fetch_page(str(code), cursor.strftime("%Y%m%d"))
        if not isinstance(payload, dict) or str(payload.get("rt_cd", "0")) != "0":
            break
        page = parse_output2(payload, until)
        if not page:
            break
        oldest = page[0]["date"]
        if oldest_previous is not None and oldest >= oldest_previous:
            break
        oldest_previous = oldest
        for row in page:
            out[row["date"]] = row
        if len(out) >= target_rows:
            break
        next_day = date.fromisoformat(oldest) - timedelta(days=1)
        if next_day >= cursor:
            break
        cursor = next_day
    return sorted(out.values(), key=lambda x: x["date"])[-90:]


class KisInvestorClient:
    """Rate-limited read-only quote client with short error messages."""

    def __init__(self, key=None, secret=None, session=None,
                 interval=MIN_REQUEST_INTERVAL):
        self.key = key if key is not None else os.getenv("KIS_APP_KEY", "")
        self.secret = secret if secret is not None else os.getenv("KIS_APP_SECRET", "")
        self.session = session or requests.Session()
        self.interval = max(0.3, float(interval))
        self.token = None
        self.last_request = 0.0
        self.error = None

    @property
    def configured(self):
        return bool(self.key and self.secret)

    def authenticate(self):
        if not self.configured:
            self.error = "credentials_missing"
            return False
        try:
            response = self.session.post(
                BASE_URL + TOKEN_PATH,
                json={"grant_type": "client_credentials", "appkey": self.key,
                      "appsecret": self.secret},
                headers={"Content-Type": "application/json"},
                timeout=(4, 12),
            )
            if response.status_code != 200:
                self.error = "token_http_" + str(response.status_code)
                return False
            value = response.json()
            self.token = value.get("access_token")
            if not self.token:
                self.error = "token_missing"
                return False
            return True
        except (requests.RequestException, ValueError):
            self.error = "token_request_failed"
            return False

    def _throttle(self):
        delta = time.monotonic() - self.last_request
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self.last_request = time.monotonic()

    def fetch_page(self, code, cursor):
        if not self.token:
            self.error = "not_authenticated"
            return None
        headers = {
            "authorization": "Bearer " + self.token,
            "appkey": self.key,
            "appsecret": self.secret,
            "tr_id": TR_ID,
            "custtype": "P",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": cursor,
            "FID_ORG_ADJ_PRC": "",
            "FID_ETC_CLS_CODE": "",
        }
        for attempt in range(3):
            self._throttle()
            try:
                response = self.session.get(
                    BASE_URL + DAILY_PATH,
                    params=params, headers=headers, timeout=(4, 12),
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    self.error = "quote_http_" + str(response.status_code)
                    return None
                if response.status_code != 200:
                    self.error = "quote_http_" + str(response.status_code)
                    return None
                body = response.json()
                if str(body.get("rt_cd", "0")) != "0":
                    self.error = "quote_" + str(body.get("msg_cd") or "not_ok")[:32]
                    if str(body.get("msg_cd")) == "EGW00201" and attempt < 2:
                        time.sleep(1.6)
                        continue
                    return body
                return body
            except (requests.RequestException, ValueError):
                if attempt >= 2:
                    self.error = "quote_network_error"
                    return None
                time.sleep(1.0 * (attempt + 1))
        return None

    def fetch_history(self, code, until, target_rows=TARGET_ROWS):
        return collect_history(code, until, self.fetch_page,
                               target_rows=target_rows, max_pages=MAX_PAGES)
