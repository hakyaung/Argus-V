# iter-21 계획

계열: F09

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 3. 새 시나리오 3개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F09-PRV-01 | 5 | PRV | mid/L | 없음(cell6=D-L2-base) | authorization_id, extract_plan | plans[1].review_scope_ok | DEFECT-AUDIT §3.2 S33 (기준 아래 쪼개기를 같은 추출 도구의 계획 값으로); S22 (한도 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F09-FIN-02 | 6 | FIN | late/S | early/M | authorization_id, refund_batch_plan | batch_plans[4].second_review_ok | AV-S33 | late prelude 6+, max_steps 14 |
| AVv2-F09-PRC-02 | 7 | PRC | mid/M | 없음(cell6=D-L2-base) | authorization_id, contract_plan | contract_plans[2].bid_rule_ok | DEFECT-AUDIT §3.2 S22·S33 (금액 기준 쪼개기를 같은 계약 도구의 계약 계획 값으로) | D-1/D-5 교차 검사, V-10b 길이 균형 |
