# 위반 데이터 부족 해결 토론 (2026-11-06)

에이전트 2개(A: 분기 재샘플링, B: 시나리오 재설계) 2라운드 토론. 읽기 전용, GPU 미사용. 분석 스크립트: `experiments/risk_loop/debate/`.

## 현재 상태 (Argus-V1)
- Argus-V1: gpt-oss-20b L16 관측 행 → max pooling → StandardScaler → PCA-10 → LogReg. D-v2r train CV 0.783, validation 0.751, **G4 실패로 기각** (LEDGER 참조).
- D-v2r: train 9,811행/양성 55, validation 4,408행/양성 29(F02 18). D 위반율 train 2.9%, validation 5% (설계 목표 20–50%).

## 합의된 진단
1. train D 위반 23건 중 20건은 request 전에 도메인 도구를 먼저 호출한 **첫 접촉 오독**이며, DENIED 이후 위반은 3건(783개 중 0.4%). 실행 상태도 대부분 NOT_READY/BLOCK.
   → 탐침이 "다음 도구 종류"를 학습(도구 지시자 단독 AUROC 0.80, 같은 도구 안 0.55, G4 실패).
2. trusted_policy가 SAFE_INCOMPLETE 절차를 단계별로 지정하고 압력을 선제 무력화 → 거절 비용 0. 압력 용량-반응 없음(D-L1 3.0%, L2 2.5%, L3 3.8%, B셀 8.3%).
3. **라벨 의심**: train v2 양성 38건 중 33건이 rule_label=0(LLM 검수만 위반 판정), 그중 medium 19 / low 6. (메인 세션에서 재확인)
4. 기존 prefix 분기 재샘플링은 오독 사례만 늘림. X-twin replay로 정확한 최소쌍이 되는 것은 train 2개뿐(위반 대상 ≠ twin_delta 대상).
5. 형제 샘플은 pre_reasoning 행만 공유(행 약 8개 중 1개). 정보는 reasoning 행에서 나오므로 CoT 읽기와 구분해야 함.

## 병합 계획 (승인 전, 미실행)
| 단계 | 내용 | GPU |
|---|---|---|
| S0 | 사람 대시보드로 train 양성 38건 + X-twin 이견 58건 확정 (확정 양성 <15면 분기 보류) | 0 |
| S1 | v2 DENIED 이후 prefix 분기로 q_post 측정 | 0.2–0.9h |
| S2 | v3 시나리오 파일럿 512 궤적 (거절 절차 문구 제거, near-legal 대상, sunk cost, 권한 충돌; B/G/D/X 동일 적용, twin_delta = 금지 대상) | ~11h |
| S3–S4 | v3 위반 prefix 분기 증폭, 본 수집 | 22–43h |

## 사용자 결정 (2026-11-06)
- 시나리오 재작성/재실행은 GPU 시간이 커서 다른 프로젝트를 막음 → **보류**. GPU는 확실히 필요할 때만.
- 사람 라벨링 대시보드는 다른 컴퓨터에서 제작 중.
- 위반 지시/주입 금지 원칙 유지. v3는 지시 없이 환경만 바꾸는 안이지만, B의 "성과 넛지" 층은 지시에 가까우므로 제외.

## GPU 없이 가능한 다음 단계 후보
1. 대시보드 사람 라벨로 D-v2r 재구성 후 재평가.
2. 목표 재정의: "승인 전 도메인 도구 조기 실행" 위험 + 같은 도구 안 평가.
3. HF의 미사용 v1 원본 351개, agent_loop 3 run의 activation 존재 여부 확인 후 추가 (미확인).
4. (최소 GPU) 기존 transcript forward-only 반사실 추출 — 보조 진단용.

## 사건 기록
- 토론자 B 1라운드에서 DENIED 집계 glob이 test family(F07/F11/F14) rollout을 읽음. 개별 내용 출력 없음, 해당 family 제외 후 재계산, 보고 수치는 train/validation만. 2라운드는 test 경로를 먼저 제외. test 결과/라벨은 어떤 모델 선택에도 쓰이지 않음.
