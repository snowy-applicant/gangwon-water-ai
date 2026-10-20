# 수조 2개·호스 4개·지역 용기 3개의 PINN

이 저장소의 **하드웨어용 핵심 모델**은 `waterai/rig_pinn.py`와 `models/rig-pinn.json`이다. 실제로 학습한 신경망 가중치를 저장하며 서버에서는 NumPy로 동일한 신경망을 추론한다. 기존 `waterai/pinn.py`/`models/pinn.json`의 단일 압력 관로 벤치마크는 비교용으로 유지한다.

현재 제공 가중치는 **합성 치수로 사전 학습한 연구 모델**이다. 장치 치수와 PASCO 원시 측정값은 아직 확보하지 않았으므로 실물 성능을 검증한 모델이 아니다. `hardware/rig.json`의 미측정 치수 `null`을 임의로 덮어쓰지 않는다. 강원도 시군 공급망에 이 탁상 모델을 자동 적용하지 않는다.

## 신경망이 계산하는 것

- 입력 11개: 시간, A/B 초기 물량, H1~H4 출구 높이, H1~H4 공급 설정(0/1).
- 구조: 11 → tanh 48 → tanh 48 → 8개 출력. 학습은 PyTorch Adam, 기본 3,200 epoch.
- 출력: 호스별 누적 공급량 4개 `C1~C4`와 순간 유량 4개 `q1~q4`.
- 공급량 초기조건은 `C(t)=t/T × softplus(NN_C) × gate`로 정확하게 0을 만족한다. 유량도 softplus와 gate를 적용한다. 초기 수면보다 출구가 높거나 공급 설정이 0이면 해당 호스는 0이다.
- 두 수조의 잔량과 세 용기의 물량은 **동일한 누적 공급량**으로 계산하므로 전체 물수지는 구조적으로 보존된다. 순간 유량은 별도 신경망 출력이며 아래 물리 손실로 시간 미분과 일치하도록 학습한다.

```text
VA = VA0 − C1 − C2          VB = VB0 − C3 − C4
VR1 = C1                   VR3 = C2 + C3          VR2 = C4
dCi/dt = qi
q_i = gate_i × k_i × sqrt(max(z_tank + V_tank/A_tank − z_outlet_i, 0))
```

마지막 식은 물이 채워진 호스의 준정상 유동을 유효 계수 `k`로 근사한 모형이다. 계수는 단면적·중력·마찰 및 국부 손실을 묶은 값으로 단위는 m^(5/2)/s이다. 단순한 보편 상수나 서보 각도와 같지 않다. 호스가 비거나 공기가 들어가는 상황, 출구의 잠김, 펌프, 관성·수격, 호스 변형과 누수는 모델링하지 않는다. 예측 중 수조 보충·용기 배수·출구 높이 변경은 없다고 가정한다. 설정이 바뀌면 실험 구간을 나누고 새로운 초기조건과 실측 높이를 제공해야 한다.

이것은 연결된 저장량·유량의 **0차원 그래프 ODE-PINN**이다. 관 안의 모든 위치에 대한 Navier–Stokes PDE나 수격을 푸는 시공간 PINN이라고 주장하지 않는다.

## 실제 학습 손실

유량 10mL/s, 체적 1,000mL, 시간 60초를 기준으로 무차원화한다. 시간 범위는 설정 파일에서 읽는다.

```text
L = L_reference_volume + 0.3 L_reference_flow
  + 2 L_(dC/dt=q) + 2 L_hose_discharge + 10 L_negative_storage
  + L_measured_receiver (실측 자료를 제공한 경우)
```

`dC/dt`는 PyTorch autograd로 구하고, 이 미분을 포함한 손실을 모든 층으로 역전파한다. 물리 잔차를 학습 후 표시만 하는 방식이 아니다. 매 epoch에 새로운 초기 물량·출구 높이·공급 설정의 collocation 표본 256개를 생성해 물리 손실을 적용한다. 기준해의 라벨이 없는 조건도 물리 제약으로 학습한다.

기준 라벨은 별도 `waterai/rig_twin.py`의 SciPy `solve_ivp` 적분으로 생성한다. **예측 API는 ODE 적분을 호출해 그 값을 신경망 결과처럼 반환하지 않는다.** 저장된 가중치의 순전파로 값을 계산하고, 별도 물리식은 출력 잔차 점검에 사용한다. NumPy 시간 미분은 가중치와 tanh/softplus의 연쇄법칙으로 계산한다.

## 현재 사전 학습 모델의 범위와 검증

기하·계수는 `data/rig_reference.json`에 가상임을 명시했다. 실제 `hardware/rig.json`과 별도 파일이다.

| 항목 | 범위 또는 결과 |
|---|---|
| 시간 | 0~60초 |
| A/B 초기 물량 | 각각 1,200~2,400mL |
| H1~H4 출구 높이 | 각각 0.10~0.45m, 가상 공통 기준면 |
| 각 호스 공급 설정 | 0 또는 1, 예측 구간 동안 일정 |
| 라벨 학습 | 160조건 × 13시점, seed 42 |
| 물리 collocation | 매 epoch 256개 새 조건 |
| 개발 검증 | 32조건 × 17시점, seed 1042 |
| 별도 시험 | 48조건 × 17시점, seed 19042 |
| 시험 용기 물량 RMSE | 약 1.573mL |
| 시험 수조 물량 RMSE | 약 2.000mL |
| 시험 호스 유량 RMSE | 약 0.0447mL/s |
| 시험 `dC/dt − q` RMSE | 약 0.0217mL/s |
| 시험 준정상 유량식 잔차 RMSE | 약 0.0540mL/s |
| 시험 전체 물수지 최대 오차 | 약 8.7×10⁻¹³mL, 구조상 보존·부동소수점 오차 |

별도 시험 조건은 라벨 학습과 분리된 난수로 생성했다. 실험실 센서를 대상으로 얻은 오차가 아니다. 모델 개발 중 사용한 이전 시험 seed 9042의 결과를 보고 collocation 구성을 수정했으며, 최종 보고 시험은 새 seed 19042를 사용했다. 정확한 수치·학습 이력·가중치·라벨 해시는 모델 JSON에 저장된다.

API는 학습 범위 밖 입력을 거절한다. 범위 안에서도 음의 수조 물량, 감소하는 누적 공급량 또는 0.25mL/s를 넘는 물수지 미분/유량식 잔차가 있으면 `valid=false`로 표시한다. 수치를 그럴듯하게 잘라 보정하지 않는다. 이 표시는 위 축약 모형의 수치 점검이며 실물 공급 보장이 아니다.

## 실행과 재학습

```sh
pip install -r requirements.txt
pip install -r hardware/pinn-requirements.txt
python -m waterai.rig_pinn
python -m waterai.build
python -m unittest discover -s tests -v
```

학습에는 PyTorch가 필요하지만 저장된 모델의 서버 추론에는 기본 NumPy만 필요하다. 메인 Python 서버의 관리자 토큰으로 연결한 뒤 **하드웨어 · 실측 → 실험 장치용 PINN**에서 예측할 수 있다. 정적 Sites 호스팅은 모델 설명은 표시하지만 Python 예측 API가 없다. 모델 예측은 서보 명령을 생성하거나 장치를 작동시키지 않는다.

관리자 API:

```text
GET  /api/rig-pinn           모델 범위·학습·검증 메타데이터
POST /api/rig-pinn/predict   저장된 신경망 추론
```

POST 본문은 아래 네 필드만 받으며 시간·물량·높이 단위는 각각 s/mL/m이다.

```json
{"initial_volume_ml":[1800,1800],"outlet_height_m":[0.15,0.15,0.15,0.15],"open_mask":[1,1,1,1],"times_s":[0,10,30,60]}
```

## 실제 힘 센서 자료를 쓰는 절차

1. PASCO 인터페이스와 측정 어댑터를 통해 원본 힘(N)을 수집한다. [장치 연결 안내](HARDWARE.md)의 `/api/hardware/export` 자료를 사용한다. 현재 저장소에는 PASCO 전용 통신 드라이버가 없으므로 실제 인터페이스 모델에 맞는 취득 과정이 필요하다.
2. 수조 단면적·기준 높이·초기 물량, 호스 출구의 실제 높이, 용기의 영점·하중 방향·밀도를 측정한다. 서보 명령 각도를 실측 출구 높이로 대체하지 않는다.
3. `hardware/experiment.template.json`을 `runtime/experiment.json`으로 복사하고 모든 `null`, 시각, 장치 ID를 실제 값으로 채운다. 호스 계수는 초기 추정값이며 다음 단계에서 보정한다. 원본 힘 파일도 `runtime/observations.json`처럼 Git 제외 위치에 둔다.
4. H1·H2·H3·H4의 **독립 공급 실험**을 기록한다. F3의 H2+H3 합계만으로 두 계수를 동시에 식별했다고 주장하지 않는다. 보정 코드는 독립 공급과 충분한 실제 물량 변화가 없으면 거절한다. 원시 자료를 잘라 내보낸 경우에도 거절한다.
5. 실제 힘과 물리 모형으로 4개 호스 계수를 보정한다.

   ```sh
   python -m waterai.rig_calibrate --observations runtime/observations.json --experiment runtime/experiment.json --output runtime/rig-calibrated.json
   ```

6. 실측 보정 계수와 수신 용기 관측 손실을 함께 사용해 PINN을 다시 학습한다.

   ```sh
   python -m waterai.rig_pinn --observations runtime/observations.json --experiment runtime/experiment.json --output runtime/rig-pinn-observed.json
   ```

   이 경로는 먼저 계수를 보정하고, 그 계수의 기준 라벨·물리 손실과 **실제 세 용기 체적 관측 손실**을 신경망 학습에 함께 적용한다. 기본 사전 학습 모델과 구분하기 위해 결과는 `runtime/`에 저장한다. 검토 후 모델을 교체하고 build해야 서비스에 반영된다.

보정에 사용한 실험의 적합 오차는 별도 검증 성능이 아니다. 현재 명령은 독립 실험 검증을 자동 수행했다고 표시하지 않으며 `validated_on_observations=false`를 유지한다. 학습/보정에서 제외한 수위·높이·공급 조합에서 힘 센서의 물량과 유량을 비교해야 한다.

## 검증 코드

테스트는 닫힌/수면 위 출구의 0유량, 전체 물수지, 중앙 용기 합계, 학습 범위 거절, 가중치 변경에 따른 예측 변화, 예측 중 ODE 미호출, NumPy와 PyTorch 추론 일치, 시간 미분의 수치 미분 비교, **실제 학습 물리 손실의 파라미터 미분 비교**, 힘 관측에서 계수 복원, 식별 불가능한 실험 거절, 실측 관측 손실 경로를 확인한다. 합성 테스트 자료를 실측으로 재분류하지 않는다.

PINN의 물리 잔차 학습 개념은 [Raissi 외 원 논문](https://www.sciencedirect.com/science/article/pii/S0021999118307125)을 참고한다. 이 저장소의 수조·호스 모형과 실험 범위는 위에 명시한 별도 구현이다.
