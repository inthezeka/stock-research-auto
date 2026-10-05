# AI STOCK RESEARCH WORKFLOW — v12 TOP5 Financial Data Edition

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