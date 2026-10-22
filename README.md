# MY Stock Room · Korea + United States


주식 리서치 대시보드의 **데이터 연결 안정성**과 **배포 전 자체 검수**를 강화한 버전입니다.

## v12 핵심 변경

- **TOP5 전 종목**에 2024A·2025A·2026E·2027E 매출/영업이익/OPM/순이익/EPS/YoY를 연결했습니다.
- 자동 실행 시 CompanyWise/FnGuide 연간 데이터를 먼저 읽고, 동적 테이블 미노출·네트워크 장애 시에도 검증된 스냅샷을 유지하여 실적표가 `[검증 필요]`로 비지 않도록 했습니다.
- TOP5 실적표 28개 필드 중 하나라도 비거나 `[검증 필요]`이면 QA를 **FAIL** 처리하여 배포를 차단합니다.

- 국내 종목/지수는 **네이버증권 공개 시세 경로를 1순위**로 사용하고, 실패 시 Yahoo Finance를 보조 소스로 사용합니다.
- USD/KRW, 미국 10년물도 네이버 시장지수 데이터를 우선 사용합니다.
- KOSPI/KOSDAQ 외국인·기관 수급은 네이버 시장요약에서 수집하며 실패 시 직전 검증값을 유지합니다.
- TOP5 PER/PBR/Forward PER/ROE 등은 네이버증권 Integration + FnGuide/CompanyWise를 순차 확인합니다.
- 개별 소스 장애가 발생해도 화면 전체가 빈 값으로 교체되지 않도록 **마지막 검증 데이터(last verified)** 를 보존합니다.
- 실시간 후보종목의 **80% 이상**이 정상 수집되지 않으면 새 `market-data.json`을 배포하지 않습니다.
- 시장 핵심 데이터는 최소 **60% 이상** 실시간 연결을 요구하고, 품질 검수 결과를 기록합니다.
- GitHub Actions에서 데이터 생성 → fallback 동기화 → smoke test → 구조/JS 검수 순서가 모두 통과한 경우에만 commit/deploy 합니다.
- 생성된 `index.html`/`market-data.json` 커밋은 workflow를 다시 호출하지 않도록 재귀 실행을 차단했습니다.

## 데이터 연결 구조

```text
네이버증권 국내 종목/지수 ─┐
네이버 환율/미국채 ─────────┼─> update_data.py
Yahoo Finance fallback ──────┤
FnGuide/CompanyWise ─────────┤
OpenDART(선택) ──────────────┘
                             ↓
                      품질 조건 검사
                             ↓
                 data/market-data.json
                             ↓
                    index.html fallback 동기화
                             ↓
                   smoke test + JS/JSON 검수
                             ↓
                       GitHub commit
                             ↓
                       Netlify deploy
```

## 자동 업데이트

- 예약: **매일 오전 07:00 KST**
- 수동: GitHub Actions → `Daily stock research update` → `Run workflow`
- `config/`, `scripts/`, workflow 등 소스 변경을 push하면 즉시 한 번 실행됩니다.
- 자동 생성되는 `index.html`/`market-data.json` commit은 다시 workflow를 호출하지 않습니다.

> HTML 파일을 PC에서 단독으로 더블클릭한 경우 GitHub Actions는 실행되지 않습니다. 이때는 HTML에 내장된 **마지막 검증 데이터**가 표시됩니다. 자동 최신화는 GitHub 저장소 + Actions + Netlify 배포 환경에서 동작합니다.

## 자체 검수(QA)

배포 전 다음 검사가 자동 실행됩니다.

### `scripts/smoke_test.py`
- 네이버 차트 응답 파서
- RSI / Stochastic / MACD / MA / Support / Resistance 계산
- 19개 Theme / 54개 후보종목 구성
- 중복 종목코드
- 현재 데이터 payload 기본 schema
- TOP5 5개 종목 × 4개년 실적 필드 완전성

### `scripts/validate_project.py`
- `market-data.json`, `watchlist.json` JSON 유효성
- KOSPI/KOSDAQ/NASDAQ/USD-KRW/US10Y 존재 여부
- TOP5 정확히 5종목
- TOP5 전 종목 2024A/2025A/2026E/2027E 실적표에 빈 값·`[검증 필요]`가 없는지 검사
- 종목별 뉴스/공시/Consensus HTTPS 링크
- 19개 테마 및 각 3종목
- 불완전 상태 문구 노출 여부
- HTML 내장 fallback JSON 일치 여부
- Node.js 기반 inline JavaScript syntax check
- live run의 데이터 fresh coverage 검사

하나라도 필수 검사가 실패하면 workflow가 중단되며 **직전 정상 데이터가 그대로 유지**됩니다.

## 프로젝트 구조

```text
/
├─ index.html
├─ data/
│  └─ market-data.json
├─ config/
│  └─ watchlist.json
├─ scripts/
│  ├─ update_data.py
│  ├─ embed_fallback.py
│  ├─ smoke_test.py
│  ├─ validate_project.py
│  └─ requirements.txt
├─ .github/workflows/
│  └─ daily-update.yml
└─ netlify.toml
```

## GitHub / Netlify 최초 설정

1. 프로젝트 전체를 GitHub Repository에 업로드합니다.
2. `Settings → Actions → General → Workflow permissions`에서 **Read and write permissions**를 허용합니다.
3. Netlify에서 해당 GitHub Repository를 연결합니다.
4. Build command는 비워도 되고 Publish directory는 `.`로 설정합니다.
5. OpenDART 상세 공시 자동수집을 사용하려면 GitHub Secret `DART_API_KEY`를 등록합니다.
6. 필요하면 Netlify Build Hook을 `NETLIFY_BUILD_HOOK` Secret으로 등록합니다.

## 로컬 검수

```bash
pip install -r scripts/requirements.txt
python scripts/smoke_test.py
python scripts/validate_project.py
```

로컬 웹 화면 확인:

```bash
python -m http.server 8080
```

브라우저에서 `http://localhost:8080`으로 접속합니다.

## 데이터 원칙

- 연결 실패를 숫자 0이나 임의 추정치로 대체하지 않습니다.
- 최신값을 가져오지 못하면 직전 검증값을 유지합니다.
- 실적/Consensus처럼 신뢰할 수 있는 무료 자동 소스가 제한적인 값은 검증일을 함께 관리합니다.
- `[AI]`는 자체 계산·해석이며 외부 확정값과 구분합니다.

## MY Trading 최근 5거래일(5D) 수급

- MY Trading TOP10에는 5거래일 외국인·기관계·개인 **순매수 수량(주)**만 간결하게 표시합니다.
- 종목 상세의 별도 수급 섹션은 주체별 합계 카드와 최근 5거래일 누적 추이 그래프를 보여줍니다.
- 20D·60D는 일시 중단했습니다. 공식 KIS / 사용자 제공 KRX CSV 백필 모듈은 향후 확장을 위해 보관하지만 **실행하지 않습니다.**
- 기존 5D 수집은 매일 자동 갱신하고 자료가 부족한 종목은 수치를 만들지 않습니다.
- 과거 장기 수급 연동 기록은 [보관된 KIS·KRX 설정 가이드](docs/INVESTOR_FLOW_60D_SETUP.md)를 참고하세요. 현재는 연결할 필요가 없습니다.
- 사이트: https://inthezeka.github.io/stock-research-auto/


## 미국장 MASTER v2 · 44개 독립 투자섹터

- 시장선택: [markets.html](markets.html)
- 기존 국내장: [index.html?market=kr](index.html?market=kr) · 국내 36개 Sector 코드/점수/수집기 보존
- 신규 미국장: [us.html](us.html) · 미국 전용 44개 US Investment Sector, 32개 Theme, 12개 업종형 Fundamental 100점 모델
- 분류 기준: `config/us_investment_sectors.json` / `scripts/us_investment.py`
- SEC XBRL/업종별 Fundamental: `scripts/us_fundamental.py`
- 미국 데이터 수집: `scripts/update_us_market.py` → `data/us-market.json`
- 화면: `scripts/us-market-ui.js` · 국내 데이터/코드는 변경하지 않음

### 미국장 데이터 범위

NASDAQ-100 / S&P 500 / Dow 30 구성종목을 중복 제거하여 수집합니다. NasdaqTrader Symbol Directory로 NASDAQ/NYSE/NYSE American 상장 보통주 후보를 발굴하고 Nasdaq 공개 Stock Screener의 sector·industry와 SEC SIC를 산업 분류 보조 자료로 활용합니다. 공개 screener는 공식 GICS 데이터가 아닙니다. 기업별 표기 `Internal Sector Classification`은 내부 투자섹터로 분류되었다는 뜻이며, 공식 GICS 인증이 아닙니다.

시장 전체 후보 종목의 가격과 재무가 매일 100% 수집되는 것은 아닙니다. 3대 지수 우선 + 나머지 거래소 상장 후보 로테이션이며 `US_MAX_CHARTS=950` 기본값을 사용합니다. 실제 검증된 비율과 `meta.status=partial`을 명확히 표시합니다. 미국 종가는 Yahoo Finance 일봉 OHLC/ET 거래일을 검증하나 제2 공급자 독립 교차검증은 아직 없습니다.

### SEC 연동 (운영자 설정 필요)

SEC Company Facts / Submissions는 API Key를 요구하지 않지만, Fair Access 정책에 따라 실제 요청 주체와 연락처가 담긴 User-Agent가 필요합니다.

1. GitHub → Settings → Secrets and variables → Actions → New repository secret
2. 이름: `US_SEC_USER_AGENT`
3. 값: `OrganizationName ContactEmail@example.com` (가상 이메일이 아니라 실제 관리 가능한 연락처)
4. GitHub Actions → US market close update + Pages → Run workflow

SEC 미설정은 `meta.sec.status=not_configured`로 표시하며 Fundamental 점수는 임의 생성하지 않습니다. US SEC requests는 순차 수집으로 제한하고 `US_SEC_LIMIT=120` 종목씩 증분 갱신합니다. 20-F/IFRS/ADR은 별도 지원과 증권 유형 검증 없이는 US GAAP 점수로 처리하지 않습니다.

### US MASTER 100점 점수

Fundamental = G(Forward Growth) + Q(Quality) + B(Balance/FCF) + V(Valuation) + K(Sector KPI) + R(Revision).

미국 44개 Sector는 사용자 정의 12개 업종별 배점표 중 하나를 사용합니다. 재무/Valuation 증거가 SEC FY 실제 값에만 존재하면 `HISTORICAL ACTUAL`로 표시하며 역사적 성장률을 Forward EPS로 표기하지 않습니다. Consensus가 없으면 `NO_CONSENSUS_PROFILE`로 R 배점을 G/K에 절반씩 재배분합니다. 검증되지 않은 산업 KPIs, Guidance, Analyst Consensus 또는 계약매출을 생성하지 않습니다.

Evidence Coverage ≥85% HIGH, ≥70% MEDIUM, ≥50% LOW (점수 상한 75점), <50% INSUFFICIENT (점수 미발행). 중립적으로 대체한 배점의 근거는 상세페이지에 공개합니다. Fundamental TOP20에는 차트·RSI·거래량·M's Trading 점수를 사용하지 않습니다.

### 화면별 역할

- Sector Cycle TOP10: 수요/공급/가격/CAPEX/Guidance/구조적 성장/Macro 7요인을 근거로 분석. 사업 정보가 부족하면 Cycle Score·실제 Phase·매력도 미발행.
- Theme Popularity TOP10: 거래대금/상대수익률/시장확산/뉴스/실적 Catalyst 등을 별도로 평가. 근거가 없다면 인기 점수 미발행.
- Fundamental TOP20: HIGH/MEDIUM 우선. LOW / INSUFFICIENT 기업은 추가 분석 필요 목록에서만 표시.
- Sector TOP3 및 Theme TOP3: 검증 안 된 종목은 `후보`로 표시하며 Verified Revenue를 추정하지 않음.
- Investment BEST5: Fundamental60 + Valuation/RR20 + Catalyst10 + Technical5 + Liquidity5. 근거 없으면 발행하지 않음.
- M's Trading TOP10: Long Only 차트 기반 스크리닝, MSS/FVG/Order Block 확정 진입신호와 구별.
- 미국 5D 수급: 5D 가격변화/OBV/상대거래량이며 국내의 외국인/기관/개인별 순매수 지표와 다른 자료입니다.
- 종목 Deep Dive: 업종·테마·SEC 연간 재무·KPI·결측/신뢰도·6개 배점 구성과 차트를 분리 표기.

### 자동 검증

- `scripts/test_us_fundamental.py`: SEC XBRL 샘플과 업종별 배점, Confidence, NO_CONSENSUS 동작 검사
- `scripts/validate_us_market.py`: 44개 Sector, 출처/거래일/점수/차트/페이지 구조 검사
- `.github/workflows/us-market-update.yml`: 미국장 데이터 독립 실행
- `.github/workflows/daily-update.yml`: 기존 국내장 업데이트 독립 실행

**잔여 한계:** SEC User-Agent가 없으면 Fundamental Score 0개가 정상입니다. US 상장 종목 전체의 완전한 일봉·재무 커버리지, 회사별 Backlog·ARR·NIM·CET1 등 핵심 KPI, 증권사 3개월 리포트/목표주가, 검증된 실제 Sector Cycle/Theme Popularity/Investment BEST5, ADR IFRS, 완전 자동 ICT MSS/FVG/OB는 아직 완료되지 않았습니다.
