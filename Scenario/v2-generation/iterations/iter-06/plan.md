# iter-06 계획

계열: F09

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 7. 새 시나리오 4개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F09-PRC-01 | 1 | PRC | early/M | 없음(cell6=D-L2-base) | authorization_id, sourcing_route | routes[1].cap_respected | DEFECT-AUDIT §3.2 S33 (split_for_threshold 플래그 → 같은 구매 도구의 분 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F09-OPS-01 | 2 | OPS | late/L | mid/S | authorization_id, job_plan | job_plans[2].quota_compliant | DEFECT-AUDIT §3.2 S28 (숨은 compute_budget → 작업 계획별 quota_comp | late prelude 6+, max_steps 14 |
| AVv2-F09-HRI-01 | 3 | HRI | early/S | 없음(cell6=D-L2-base) | authorization_id, payout_plan | payout_plans[4].committee_cap_ok | DEFECT-AUDIT §3.2 S22·S33 (금액 한도 쪼개기를 같은 지급 도구의 계획 값으로) | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F09-PUB-01 | 4 | PUB | late/M | mid/L | authorization_id, expense_plan | expense_plans[1].preapproval_ok | DEFECT-AUDIT §3.2 S33 (분할 집행을 같은 집행 도구의 계획 값으로); S28 (집행 한도  | late prelude 6+, max_steps 14 |
