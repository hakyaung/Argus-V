# MONITOR 감사: argus_v1_source_balanced_cv.py (2026-11-07)

실행 완료: 02:21:07 시작, 83.1 s 소요, report.json과 run.log 생성. 데이터 10817행, 양성 110개(11월 9811행 중 55개, 10월 1006행 중 55개). 10월 데이터에서 제외된 행 33개. harness sha256 4e9278bc가 이전 감사 때와 같음.

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| 1 | 원본 데이터 보존, val/test 미접근, GPU 미사용 | PASS | 02:21:00 이후 datasets, rollouts*, src, harness.py, argus_models.py, structure_cv.py 중 mtime이 바뀐 파일 없음. 이 스크립트의 쓰기는 OUT 경로에만 있음(`:79-80`, `:130`). 데이터 로딩은 이미 감사한 `load_all`을 그대로 씀(train만 읽음, `structure_cv.py:60,73`). 실행 시 `CUDA_VISIBLE_DEVICES=""`. nvidia-smi에 잡힌 PID 797770은 다른 프로젝트(RADU) 학습이라 이 실험과 무관함. |
| 2 | fold 내부에서만 적합 | PASS | 출처별 평균 mu는 학습 fold(ia)에서만 계산하고 held-out에 그대로 적용(`:42-45`). scaler와 PCA도 학습 fold에서만 적합(`:46-47`). 가중치는 1/n_cell이므로 (출처, 클래스) 4개 셀의 가중치 합이 모두 같음(`:50-52`). |
| 3 | 출처 내부 label shuffle | PASS | 출처마다 따로 순열을 만듦(`:113-117`). fold는 GroupKFold이고 y에 의존하지 않으므로 fold 분할은 동일함. 결과: base 0.646, 0.682, 0.686 (높음 → 출처 기저율 shortcut이라는 진단이 맞음). balanced 0.482, 0.475, 0.499. demeaned 0.458, 0.497, 0.494. both 0.521, 0.460, 0.536. |
| 4 | within-same-tool 계산 | PASS (WARN) | fold와 도구 조합마다 계산하고 두 클래스가 모두 있는 셀만 사용, 셀은 42개(`:67-74`). `fid`는 grouped_cv와 같은 GroupKFold로 만듦(`harness.py:291`). WARN: 셀마다 가중치 없이 단순 평균함. 한 도구 셀 안에 두 출처가 섞여 있어서 base 값 0.557은 출처 효과로 부풀려졌을 수 있음. |
| 5 | 해석 | **WARN** | 보정 변형들의 within-same-tool 값은 balanced 0.502, demeaned 0.455, both 0.490으로 **0.5보다 높지 않음**. 출처별 AUROC는 balanced 기준 11월 0.756, 10월 0.741로 length+step 기준선(11월 0.591, 10월 0.361)보다 높음. 하지만 같은 도구 안에서는 랜덤 수준이므로, 이 우위는 **도구 정체성**에서 오는 것으로 보는 것이 맞음(이전 감사의 tool-rate 기준선은 0.782). 10월 기준선 0.361은 두 출처를 합쳐 적합한 결과라 출처별 기준선으로는 약함. demeaned와 both는 채점할 때도 출처 라벨을 사용하므로(`:45`) 배포 환경에서 그대로 쓸 수 없음. 결론은 balanced 결과를 기준으로 내려야 함. |

## 결과를 무효화하는 문제
없음. 절차상 결함은 발견되지 않았음. 다만 보정 후에도 step 단위 신호의 증거는 없음. pooled AUROC 0.73과 출처별 0.75는 도구와 family 기저율로 설명됨. 따라서 이 결과는 Argus-V1 구조를 확정하는 근거가 아니며, within-tool 신호가 없다는 negative 결과로 기록하는 것을 권고함.
