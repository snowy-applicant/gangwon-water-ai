# 데이터 계약

각 테이블의 `provenance`, `timestamp`, 단위, 결측/추정 여부를 함께 보관합니다. 이 버전의 공개 시설 자료와 DEM은 확보되지 않았으며 `data/network.json`은 전부 synthetic입니다.

| 테이블 | 기본 칼럼 | 확장 칼럼 |
|---|---|---|
| weather.csv | date, region_id, precipitation_mm, temperature_c | provenance, imputed |
| 시계열 특징 (계산 시 생성) | rainfall_30d_mm, demand_m3_day, essential_demand_m3_day | month, region_id |
| network.nodes (source) | facility_id에 대응하는 id, reservoir_storage_m3, inflow_m3_day, max_supply_m3_day | latitude, longitude, elevation_m, head_m(available_head_m 대응), node_type |
| network.nodes (demand) | region_id에 대응하는 id, demand_m3_day, essential_demand_m3_day | latitude, longitude, elevation_m, head_m, node_type |
| network.edges | from_node, to_node, pipe_diameter_mm | pipe_length_m, pipe_roughness, pipe_max_flow_m3_day, loss_fraction, kind, cost, reliability |
| users | id, region_id, category, subtypes, area | heads, actual_usage(actual_usage_m3_day), estimate |
| reports SQLite | id, reporter(해시), region, timestamp, payload | severity, text, score |
| runs SQLite | id, timestamp, payload | input, result(input_hash, model_version, assumptions, data_provenance) |
| plans SQLite | id, status, timestamp | 검토 상태만 관리, 실제 시설 제어 없음 |

원문의 기본 15개 정보는 시계열·노드·관로로 분리합니다. 단일 평면 CSV로 가정하지 않습니다. `facility_id`, `supply_source_id`는 source 노드 ID로 연결할 수 있으나 실제 외부 기관 식별자 매핑은 미완료입니다. `water_level_m`, 실제 지형의 `terrain_slope`, `land_use_type`, 공사비 자료는 미확보이며 실제 데이터인 것처럼 채우지 않습니다.

`head_m`은 고정된 총수두(m)입니다. edge의 수두 차이로 정상류 상한을 계산합니다. 저수량(m³)과 유입량(m³/day)은 독립 변수입니다. 각 시간의 LP 공급원 주입량은 `min(저수량+해당 시간 유입량, max_supply/24)` 이하입니다. 총수두를 실제 운영 수위로 갱신하지 않으므로 시설 운영 모델과 다릅니다.

`kind=open`의 추가 필드는 `channel_width_m`, `channel_depth_m`, `manning_n`입니다. 실제 수심/단면 자료를 넣어야 하며 미입력 기본값은 시험용입니다. 좌표는 WGS84, 면적은 m², 비용은 원, 날짜는 ISO 8601입니다.

브라우저에 노드·관로 JSON을 올리면 유효성을 검사하고 실행합니다. 사설 이용자 주소나 신고 자료를 정적 파일에 포함하지 마세요. `provenance=observed`는 이용자가 실제 근거를 확인한 경우에만 사용하세요.

## 별도 하드웨어 PINN 데이터

`hardware/rig.json`은 실제 제작 계획의 연결 관계이며 치수는 미측정 `null`이다. `data/rig_reference.json`은 합성 사전 학습 치수(`rig-hydraulics/1`)로 이를 덮어쓰지 않는다. `models/rig-pinn.json`은 `hose-height-ode-pinn-v1` 신경망 가중치·학습 범위·검증 수치다. raw `rig-observations/1` 힘(N) 관측과 `rig-calibration/1` 실험 설명을 결합해 실제 계수 보정과 용기 관측 손실 재학습에 사용할 수 있다. 실험 데이터와 보정 결과의 기본 저장 위치는 Git 제외 `runtime/`다. [전체 계약과 명령](RIG_PINN.md)을 참고한다.
