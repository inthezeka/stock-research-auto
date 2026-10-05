# QA REPORT — v12

- ui_version: `v12-top5-financials`
- market_date: `2026-10-02`
- TOP5 financial QA: **PASS**

## TOP5 Financial Table Check

- 삼성전기: PASS · 2024A / 2025A / 2026E / 2027E
- SK하이닉스: PASS · 2024A / 2025A / 2026E / 2027E
- LS ELECTRIC: PASS · 2024A / 2025A / 2026E / 2027E
- 삼성전자: PASS · 2024A / 2025A / 2026E / 2027E
- 한화에어로스페이스: PASS · 2024A / 2025A / 2026E / 2027E

## Automated Validation

- `python scripts/validate_project.py`: PASS
- `python scripts/smoke_test.py`: PASS
- JavaScript syntax check: PASS (validator)
- Embedded fallback sync: PASS

## Release rule

TOP5 재무 테이블에서 매출·영업이익·OPM·순이익·EPS·YoY 중 하나라도 누락되거나 `[검증 필요]`가 남으면 배포를 차단합니다.