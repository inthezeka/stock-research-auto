# MY Stock Room · 투자자별 5D/20D/60D 수급 데이터 설정

업데이트: 2026-10-22

## 현재 상태와 범위

- TOP10 및 종목 상세 화면은 외국인, 기관계, 개인의 **일별 순매수 수량(주)**을 5D·20D·60D로 합산합니다.
- 5D는 기존 네이버 공개 데이터에서 자동 수집합니다. 현재 해당 데이터는 최근 5거래일이 제공됩니다.
- **KIS(한국투자증권) 공식 Open API**는 과거 날짜별 투자자 순매수 자료를 추가로 수집할 수 있는 *선택적* 경로입니다.
- **KRX 원자료를 사용자가 다운로드·정리한 CSV**도 별도 백필 경로로 지원합니다.
- 20D/60D 자료가 실제로 20/60거래일치 확보되지 않았다면 표시하지 않습니다. 계산식은 이미 구현되어 있습니다.
- **모든 매매 데이터는 KRX 현물시장 구분(J), 단위 주(shares)**로 처리합니다. NXT/통합시장 데이터와 혼합할 수 없습니다.

## A. KIS 공식 API 방식 (권장)

1. [한국투자증권 Open API 개발자센터](https://apiportal.koreainvestment.com/)에서 사용을 신청하고 **실전 시세 조회가 가능한** APP Key와 APP Secret을 발급합니다.
2. GitHub 저장소 **inthezeka/stock-research-auto**에서 `Settings → Secrets and variables → Actions → New repository secret` 메뉴로 이동합니다.
3. 아래 두 개의 **Repository secrets**를 등록합니다 (값을 코드나 공개 GitHub Issue/Chat에 붙여 넣으면 안 됩니다).
   - `KIS_APP_KEY`: 발급받은 APP Key
   - `KIS_APP_SECRET`: 발급받은 APP Secret
4. `Actions → Daily stock research update + GitHub Pages → Run workflow`에서 수동 실행해 초기 데이터 수집 결과를 확인합니다.
5. GitHub의 `data/investor-flow.json`에서 `meta.kis_backfill.status`, `meta.valid_20d`, `meta.valid_60d`를 확인합니다.
6. 이후 평일 오전 7시, 오후 3시 45분 및 오후 8시 30분(KST)에 재수집합니다. 장중/미확정 수급은 제외될 수 있습니다.

### API 세부사항

- `POST /oauth2/tokenP` (client credentials)
- `GET /uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily`
- `TR_ID = FHPTJ04160001`
- `FID_COND_MRKT_DIV_CODE=J` (KRX), `FID_INPUT_ISCD=6자리 종목코드`, `FID_INPUT_DATE_1=YYYYMMDD`
- 일자: `stck_bsop_date`; 외국인 `frgn_ntby_qty`; 기관계 `orgn_ntby_qty`; 개인 `prsn_ntby_qty`.
- 최대 6회 과거 날짜 재조회로 최소 65거래일 수집을 시도합니다. 실제 API가 제공하는 이력 범위, 페이지 이동, 응답 성공 여부는 **인증 후 검증해야 합니다**.
- 기존 KIS 일별 데이터는 보관하며 비공식 Naver 수급과 동일 날짜를 섞어서 총합을 만들지 않습니다.
- API 제한 또는 장애 시 전체 사이트 배포를 막지 않고, 가용한 기존 데이터만 사용합니다.

### 주의: 공개 사이트의 시장 데이터 이용 조건

이 GitHub Pages 사이트 및 GitHub 저장소는 공개 접근이 가능합니다. **시장 데이터의 외부 재배포·제3자 제공 허용 여부를 KIS/KRX의 데이터 약관에서 먼저 확인하십시오.** 키를 비공개로 보관해도 공개 JSON으로 시장 데이터를 배포하는 행위는 별도의 이용 조건 적용 대상일 수 있습니다. 사용 승인 없이 공개 배포하기보다 공개 범위를 제한하거나 승인된 데이터만 사용해야 합니다.

## B. KRX CSV 백필 (KIS 인증이 없거나 사용 불가 시)

1. [KRX Data Marketplace](https://data.krx.co.kr/)에서 **개별 종목의 투자자별 매매실적 일별추이**를 필요한 과거 기간까지 다운로드합니다.
2. 사용자 데이터 이용 권한과 공개 재배포 허용 여부를 확인합니다.
3. 원자료를 아래 CSV 형식으로 **정규화**합니다. CSV는 UTF-8, 6자리 종목코드, 거래일, 투자자별 실제 *순매수 수량*을 사용합니다.

```csv
stock_code,date,foreign,institution,individual,market
005930,2026-10-19,120000,-20000,-100000,KRX
005930,2026-10-20,-35000,80000,-45000,KRX
```

위 숫자는 **형식 설명을 위한 예시일 뿐 실제 매매 수치가 아닙니다. 업로드 시 반드시 실제 원자료로 교체하십시오.**

4. 실제 20/60거래일 이상의 파일을 `data/investor-flow-backfill.csv`로 저장소에 등록합니다.
5. GitHub Actions를 다시 실행합니다. 숫자가 없는 날짜에 0을 임의 기입하거나 외국인/기관 이외 잔여 값을 개인 매매로 추정하지 않습니다.
6. `meta.krx_csv_backfill.status` 및 `valid_20d/valid_60d` 값을 확인합니다.

**참고:** KRX의 다운로드 원본이 위 스키마와 다를 수 있습니다. KRX Excel을 바로 넣는 기능이 아니라, 위 표준형 CSV로 변환해 검증하는 입력 기능입니다. 이용권한을 확인하기 전에는 자료를 공개 저장소에 등록하지 마십시오.

## 데이터 QA 기준

- 단위: `shares`, 순매수 수량(부호 있는 정수). 누적 금액(원)으로 오해 금지.
- 동일 종목·날짜에서 다른 값이 발견되면 CSV 오류로 처리합니다.
- 과거의 60개 거래일 자료가 확보되지 않으면 60D 수급과 60D 그래프는 비활성화됩니다.
- KIS API가 한 번에 30개 일자를 반환해도 추가 날짜 조회 시도를 하며, 실제로 진전이 없는 경우 무한 반복하지 않습니다.
- 장중 현재가 기준일과 투자자 수급 마지막 확정일이 다를 수 있으며, 화면에는 각각의 날짜가 표시됩니다.

## 개발 확인용 테스트

```bash
python scripts/smoke_test.py
python scripts/test_investor_flow_backfill.py
python scripts/validate_project.py
```

- 실제 인증키 없이도 65거래일에 걸친 KIS 날짜 이동 모의 테스트 및 CSV 이력 입력 테스트를 수행합니다.
- 이 테스트는 API 접근이 가능한지 자체를 검증하는 것이 아닙니다.
