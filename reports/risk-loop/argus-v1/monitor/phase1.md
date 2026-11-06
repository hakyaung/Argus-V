# Argus-V1 감시 보고 — Phase 1 (학습 감사)

감시 Agent (A4·A5와 독립), 2026-11-06. 읽기 전용 감사, CPU만 사용. 감사 스크립트: 세션 scratchpad `p1_audit.py`, `p1b.py`, `p1c.py` (산출물 덮어쓰기 없음, 리핏은 메모리 안에서만).

## 요약

| # | 항목 | 판정 |
|---|---|---|
| 1 | 학습이 train 행만 사용 | **PASS** |
| 2 | 빌드 체인에서 봉인(test) rollout 미열람 | **PASS** (주석 1건) |
| 3 | 하네스 해시 일치·미수정 | **PASS** |
| 4 | 입력이 activation만 | **PASS** (교란 경고 2건) |
| 5 | 라벨 출처·split 간 누출 | **PASS** (규칙 경미 위반 1건: 계열 1개가 split을 넘음, 위반 0) |
| 6 | 재현성 | **PASS** (sha·계수 동일) / §2.8 기록 항목 일부 누락 **WARN** |
| 7 | GPU 미사용, `artifacts/rollouts*` 미기록 | **PASS** |

## 1. train 행만 사용 — PASS
- `argus_v1_train.py:32-33` `tr = list(ds.train_idx)` + `assert all(split == "train")`; CV(`:36`)와 최종 적합(`:39` `am.fit(NAME, ds, tr)`) 모두 `tr`만 사용. validation/test 행 참조 없음 (`val_idx` 미사용).
- 스케일러·PCA·LogReg 적합은 `argus_models.py:67-71` `_fit_X` 안에서만, 전달된 X(=train)로. CV 함수 `cv_fit_predict`(`argus_models.py:88-96`)는 fold 내 train 부분(`Xa`)만으로 적합.
- `model.pkl` 검사: `scaler.n_samples_seen_ = 9811`, `pca.n_samples_ = 9811` = D-v2r train 행 수(9811). n_features_in 2880 → PCA 10 → LR 10.
- 사용한 obs 행 79,665개가 전부 D-v2 train 샘플에 속함.
- 레이어 선택(L11/L16/L23)은 `argus_v1_train_cv.py`의 train GroupKFold로만 비교됨(`report.json` `validation_touched: false`). L16은 all-train 층화 CV 최고(0.786 > L11 0.777)로 TASK §4.3 선택 규칙과 일치. (v2-only 층화에서는 L11 0.833 > L16 0.831로 근소 역전 — 사전 등록 기준이 all-train임을 원장에 명시 권장.)

## 2. 봉인 rollout — PASS
- `build_d_v2.py:52-55`: split ∉ {train, validation}이면 `sealed`에 ID·split만 기록하고 `continue` — `features.npz`(`:82`)와 `metadata.json`(`:70`) 읽기 이전. 
- 주석: 봉인 rollout도 `rollout.json`은 파싱됨(`:48`, 큐 매칭용 scenario_id/condition/seed 및 `"report"` 존재 확인). 라벨·특징은 사용하지 않지만 메모리상으로는 로드됨. 규칙 위반은 아니나, 큐만으로 split을 판정하도록 바꾸면 더 엄격함.
- `D-v2/sealed.json`: 598건 (test 418, unsplit fakeweb 180) = 큐의 test 418 + none 180과 일치. D-v2 `samples.csv` rollout과의 교집합 **0**. D-v2 split은 train 10,003 / validation 4,497뿐.
- `step_labels.csv`: split train 9,367 / validation 4,191, 봉인 rollout 0건 → `build_d_v2r.py:37`가 test 라벨을 읽지 않음. D-v2r split = train 9,811 / validation 4,408 (test 없음).

## 3. 하네스 — PASS
- `sha256sum experiments/risk_loop/harness.py` = `4e9278bc92ff4e808fa4fcacea3ea5313d6f78acda0b11f10bdd5c56e2db44b8` (동결값 일치, LEDGER.md:37).
- `git diff HEAD -- experiments/risk_loop/harness.py` 비어 있음. `train_report.json`의 `harness_sha256`도 동일.
- 참고: BACKLOG.md:15 P-05 (BUG-3/BUG-4, "re-freeze before iteration 2")는 여전히 open. 하네스는 동결 상태 그대로이므로 Phase 2에서 A5가 수동 규칙을 적용했는지 확인.

## 4. 입력 — PASS (경고)
- 모델 입력은 `argus_models.py:63-64` `_X` = obs 행 activation(`obs_L16.npy`)의 원소별 max만. `tool`, `prefix_length`, `step_index`, `condition`, `seed`, `n_obs` 등은 모델 dict(`cfg, layer, scaler, pca, lr`)에 없고 `_X`에서 읽지 않음. `tool`/`n_obs`는 `argus_v1_train_cv.py:29-31`에서 **진단용으로만** 사용.
- `bags()`(`argus_models.py:50-54`)는 `ds._check_tv`로 test 행 거부.
- **경고 A (길이 교란)**: max pooling은 obs 행 수에 따라 커지는 구조. train CV에서 score–n_obs 상관 0.34 (v2-only 0.45, `train-cv/report.json`). 같은 구조(dimension max)는 BACKLOG.md:47 R-01c로 이미 기각된 이력이 있음. 금지 입력은 아니지만 G2/길이-residual 감사로 반드시 확인 필요.
- **경고 B (도구 정체성 교란)**: domain-tool 지시자 단독 AUROC 0.799 > Argus-V1 CV 0.783; 같은 도구 내 평균 AUROC 0.488 (v2-only 0.547). LEDGER.md:87·101, BACKLOG.md:37 P-07과 같은 결론 — 점수가 "다음이 도메인 도구 호출"을 주로 탐지할 가능성.

## 5. 라벨 출처·누출 — PASS (경미 위반 1건)
- 전체 14,219행 전수 대조(30행 무작위 표본 포함): D-v2r 행 ↔ D-v2 `samples.csv`(`d2_sample`)의 rollout·step·split·family·prefix hash·길이·tool·version 불일치 **0**; 라벨 규칙(`build_d_v2r.py:2-10` 카드: v2 = review_action 1 & substantive → 1, 0 → 0; v1 = label_kind action → 1, '' → 0) 위반 **0**; label_source 일치; step_labels의 split과 D-v2 split 불일치 0.
- 제외 행 재계산: procedure_skip 162, disputed 80, output_falsification 39 = 카드와 일치, 보존 14,219 = D-v2r 행 수.
- 무작위 30행(seed 20261106)은 전부 음성(양성률 0.6%라 예상대로). 보완으로 양성 10행(seed 7) 추가 확인: 모두 규칙대로 (v2 리뷰 substantive → 1, 그중 3건은 원래 rule 0 → 리뷰로 1; v1 action → 1).
- split 간 겹침: trajectory 0, run 0, scenario(task_group) 0, prefix hash 0, dup_group 0. split 내 dup 라벨 충돌 0. train 중복 비대표 행 2개(9,811 중) — G0 문구는 "중복 제거 train"이지만 학습·CV는 비중복 제거 train을 사용하고 dup_group을 같은 fold에 묶음(`argus_v1_train.py:36-37`); 영향 무시 가능.
- **경미 위반 (TASK §2.5 "계열이 split을 넘지 않는다")**: `family_id = protected_control_write`가 train(160행, AV-S23/24/25/30)과 validation(34행, AV-S15)에 모두 존재. 두 쪽 모두 양성 0, v1 rule 라벨, scenario/trajectory/prefix는 겹치지 않음 → 라벨 정보 누출 위험은 낮음. 원 split 정의(v1 manifest/큐)에서 상속된 것이므로 Argus-V1 학습의 결함은 아니나 사람에게 보고 필요(§10 분할표 변경은 사람 담당).

## 6. 재현성 — PASS / WARN
- `model.pkl` sha256 = `a43fd7508b6741a50f9fc715d905ff56d77b7ca8d7ccac953da8245394b14f0f` = `train_report.json` `model_sha256` = `card.md`.
- 메모리 내 재적합(`am.fit`, train 9,811행): coef·intercept·scaler mean·PCA components 차이 **0.0**, pickle 바이트 **완전 동일**, in-sample AUROC 0.82707 일치. samples.csv sha `ddd8dc9a…` = 카드/리포트.
- train-cv: `report.json`(14:51)이 현재 코드(`argus_models.py`·`argus_v1_train_cv.py` mtime 14:55)보다 먼저 생성됐으나, 현재 코드로 메모리 내 재계산(51 s CPU) 결과 all/v2-only × L11/16/23 전 지표·기준선 **완전 동일**.
- **WARN (TASK §2.8)**: `train_report.json`에 git commit, 미커밋 diff 해시, 라이브러리 버전, seed가 없음. 게다가 `argus_models.py`, `argus_v1_train.py`, `argus_v1_train_cv.py`는 **미추적(untracked)** 파일이고 이전 실험 스크립트 9개는 staged 삭제 상태 → 현재 HEAD로는 Argus-V1을 재현할 수 없음. 커밋 또는 diff 해시 기록 필요.

## 7. 자원·원본 — PASS
- 학습 코드는 numpy/sklearn만 import, `"gpu": false`, 8.5 s. 현재 GPU 프로세스(PID 3592896)는 다른 프로젝트(`Desktop-RADU` `src.training.train`, 14:01 시작)로 risk-loop와 무관.
- `artifacts/rollouts`, `rollouts-v1h2`, `rollouts-v2` 및 `artifacts/risk-loop/datasets` 아래 2026-11-06 이후 수정 파일 **0**. 산출물은 `argus-v1/model/`, `argus-v1/train-cv/`에만 생성.

## 프로세스 관찰 (판정 대상 아님, 사람 확인 권장)
- BACKLOG.md:52 "Draft labels need human confirmation before any validation look" — 라벨은 여전히 DRAFT인데 validation 조회가 진행 중. `argus_v1_train.py:1` "Approved 2026-11-06 (option 나)"가 이 승인에 해당하는지 원장에 근거를 남길 것.
- LEDGER.md:101은 "Not sent to validation"이라고 기록 → A5 조회 후 갱신 필요.
