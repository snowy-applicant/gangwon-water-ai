# AquaPlan · 강원특별자치도 AI 수자원 공급망

Python AI 모델과 실행 가능한 Python 알고리즘 웹 앱을 하나로 통합한 연구용 시스템입니다.

**실제 운영에 사용하도록 검증된 시스템은 아닙니다.** NASA POWER 과거 기상 격자 추정 자료로 학습한 강수 부족 모델을 포함하며, 시설·관로·지형·사업장 원단위는 명시적인 가상 데이터입니다. 실제 공급 보장·수리 설계·시공 승인 기능은 없습니다.

## 두 가지 결과물

1. **AI 모델**: `waterai/train.py`, `models/risk.json` (7·30·90일 보정 로지스틱 확률), `waterai/pinn.py`, `models/pinn.json` (물리 잔차 학습 신경망).
2. **Python 실행 웹 앱**: `web/`에서 Pyodide + SciPy로 동일한 `waterai/core.py`를 브라우저에서 직접 실행. `waterai/server.py`는 관리자 인증·SQLite 신고·실행 이력·계획 상태 API를 제공합니다.

사이트: https://gangwon-water-ai.pearlystoat128.chatgpt.site (기본 본인 전용 접근, 정적 실행 모드).

## 실행

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m waterai.build
python -m waterai.runtime  # 공식 Pyodide·NumPy·SciPy 최초 다운로드
python -m waterai.server
# http://127.0.0.1:8000
```

관리자 API는 `WATERAI_ADMIN_TOKEN` 환경변수가 설정되어 있을 때만 열립니다. 24자 이상 임의 토큰을 사용하고 HTTPS 역방향 프록시 뒤에서 운영하세요. 토큰은 화면에서 입력하면 메모리에만 보관됩니다. DB 경로는 `WATERAI_DB`, 주소는 `HOST`, 포트는 `PORT`로 설정합니다. 기본 DB는 git에서 제외되는 `runtime/`입니다.

```bash
python -m unittest discover -s tests -v
python -m waterai.train --fetch    # NASA 공개 자료 다시 수집 + 학습
python -m waterai.train --csv data/weather.csv
python -m waterai.train --csv data/weather.csv.gz  # GitHub 제공 압축 자료
pip install torch
python -m waterai.pinn
python -m waterai.build           # 모델/엔진을 웹 자산에 동기화
```

`--synthetic`은 명시적으로 합성 기상 자료를 생성하는 개발 옵션입니다. 실제 자료 수집 실패 시 자동 대체하지 않습니다. 모델 학습 후 `build`를 실행해야 사이트와 Python 모델이 일치합니다.

## 계산 구조

- **수요**: 농업/축산업/공업/일반, 복수 분류 면적 균등 분배. 축산 마릿수·실제 사용량 선택 입력. 시험용 원단위와 ±범위 표시 (통계적으로 보정한 신뢰구간 아님).
- **위험**: 다음 7/30/90일 평균 강수량 <0.8 mm/day라는 연구용 라벨. 공식 기상·농업·수문 가뭄 기준 또는 실제 공급 부족 확률이 아닙니다. 서로 다른 기간의 평균 사건이므로 확률 단조 증가를 강제하지 않습니다.
- **배분**: 손실 있는 전역 네트워크 선형계획. 필수 수요 충족 → 총 공급량 → 비용의 3단계 사전식 최적화. 공유 공급원 제약을 동시에 반영합니다.
- **디지털 트윈**: 48개 시간 단계에서 저수량·유입량·시설 용량·관로 용량·물 손실을 재계산. 누적 필수 충족률과 최초 부족 시간을 분리합니다.
- **물리**: 압력 관로 Hazen–Williams / 개방형 수로 Manning. 고정 노드 수두를 가정하며 비선형 압력 관망 해석, 펌프 운영, 충수 시간, 수격은 포함하지 않습니다. 따라서 48시간 결과는 물량 가용성 평가이며 도착 시간 보장이 아닙니다.
- **PINN**: 정상 관로 무차원 수두 손실에 대해 `dh/dx + q^1.852 = 0`과 경계조건을 학습. 해석식이 정확하고 더 저렴하므로 운영 계산은 해석식 우선. 저수지/개방형 수로에 압력 관로 PINN을 적용하지 않습니다.
- **보강**: 필수 수요 부족 상위 3개 지역 × 가상 공급원 후보. 가상 GIS의 A* 격자 경로를 구한 뒤 후보를 추가해 전체 48시간 재최적화. 전체 부족 감소가 없는 후보 제외. 후보는 개별 평가이며 효과 합산 불가. 현재 GIS 격자는 실제 지형에 정합되지 않았으므로 실제 경로 설계가 아닙니다.
- **긴급 신고**: 위험 확률과 독립된 대응 점수. 동일 신고자·지역 24시간 중복 방지. 정적 사이트는 기기 로컬 데모이며 관리자에게 전송되지 않습니다. Python 서버 모드는 중앙 저장과 관리자 조회를 지원합니다.

## 데이터와 성능

NASA POWER 2015–2025 춘천·강릉·태백 격자 자료를 사용했습니다. 원자료는 `data/weather.csv`, 조회 URL·회수 시각·해상도는 `data/weather_metadata.json`에 있습니다. 지점 관측값이 아닌 격자 추정 자료입니다. 시군 좌표는 시군 중심의 근삿값이며 상세 주소 지오코딩이 아닙니다. 미지원 지역 기상은 춘천 과거 입력을 대체 적용합니다. 현재 기상·예보 API 연결은 없습니다.

시간 분리: 학습 ~2022-10-02, 확률 보정 2023-01-01~10-02, 시험 2024-01-01~2025-10-02. 90일 라벨 창이 다음 분할에 걸치지 않도록 경계 구간을 제거합니다. 각 기간 라벨을 학습 입력으로 쓰지 않습니다. 시간 창 중첩과 공간 상관이 있어 표본을 독립 사건 수로 해석하면 안 됩니다.

| 기간 | Brier ↓ | 상수 기준선 ↓ | F1 |
|---|---:|---:|---:|
| 7일 | 0.1766 | 0.2253 | 0.6253 |
| 30일 | 0.1413 | 0.1179 | 0.5728 |
| 90일 | 0.0609 | 0.0754 | 0.7182 |

**30일 모델은 시험 자료에서 기준선보다 나쁩니다.** 이 모델의 숫자를 운영 의사결정용으로 사용하면 안 됩니다. 확률 보정 곡선·정밀도·재현율은 `models/metrics.json`. 별도 관측 수요 학습 자료가 없어 수요 AI 모델은 구현하지 않고 원단위 추정으로 구분합니다. 공급 부족 확률·저수량 예측·농업/수문 가뭄 검증도 미완료입니다.

PINN의 해석식 대비 무차원 RMSE 0.00795, MAE 0.00567입니다. 가상 물리 데이터에 대한 값이며 실측 검증 성능이 아닙니다.

## 저장소 구성과 배포

`waterai/` Python 모델·엔진·서버, `models/` 학습 산출물, `data/` 분리 데이터,
`web/` 브라우저 실행 화면, `tests/` 질량 보존·공유 공급원·우선순위·재현성 검증,
`docs/` 범위·스키마·요구사항 추적입니다.

정적 호스팅은 `python -m waterai.runtime` 후 `web/`를 배포하면 됩니다. 공식 배포 파일을 사이트와 같은 출처에서 제공하며 NumPy·SciPy wheel SHA256을 공식 lock과 검증합니다. 모델 학습은 서버/로컬 CPython에서 수행합니다. 정적 호스팅에는 서버 API가 없으므로 중앙 신고·관리자 접근 제어·이력 공유는 Python 서버 배포가 필요합니다. Dockerfile을 제공합니다. Sites 배포용 `.openai/hosting.json`은 사이트 식별자이며 비밀값이 아닙니다. GitHub 코드 수정이 Sites에 자동 배포되지는 않습니다. 큰 런타임 배포 파일은 GitHub 소스에서 제외되며 위 명령으로 재현합니다.

**운영 전 남은 일**: 상세 주소 API, 관로·수두·시설 운영 자료, 실제 DEM/토지 이용 제약, 원단위 근거, 실제 수요 검증, 관망 비선형 수리 해석, 계정별 이용자 인증과 신고자 검증, 요청 제한·백업·HTTPS, 보안 점검. 연구 서버의 신고자 ID는 클라이언트 식별자이므로 조직 운영용 부정 신고 방지 수단이 아닙니다.

출처: [NASA POWER API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) · [Pyodide](https://pyodide.org/en/stable/usage/quickstart.html) · [기상청](https://data.kma.go.kr/) · [국가가뭄정보포털](https://www.drought.go.kr/). 후자의 두 기관 자료는 이 버전에 직접 연동하지 않았습니다.
