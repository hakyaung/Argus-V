# 위험 탐지 루프 결과 공유본

`artifacts/risk-loop/`(git 제외)에서 팀 공유용으로 복사한 기록·평가 결과입니다.

- 포함: LEDGER/BACKLOG/TASK.v0, 학습·CV 리포트(json/log), A5 평가 결과·감사(audit.md), 감시 에이전트 보고(monitor/), 평가 스크립트, 데이터셋 카드(card.md)
- 제외(무거움/원본 데이터): 모델 파일(`*.pkl`), 점수·특징 배열(`*.npy`), 데이터셋 samples.csv·activation, D-v2/sealed.json(test 봉인 정보)
- 최신 모델: Argus-V1 → `argus-v1/` (model/card.md, eval/audit.md, eval/Argus-V1.json). 결과: validation AUROC 0.751, G4 실패로 기각
- 이전 기록(iter-00~02, C-0x 이름)은 Argus-V0 이전 이력입니다.
- 라벨은 LLM 검수 초안(DRAFT)이며 사람 확인 전입니다.
- 토론 정리: `docs/risk-data-debate-20261106.md`

갱신 시 원본은 항상 `artifacts/risk-loop/`이며 이 폴더는 복사본입니다.
