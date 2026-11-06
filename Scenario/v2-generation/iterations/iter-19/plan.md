# iter-19 계획

계열: F02

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 3. 새 시나리오 3개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F02-PUB-01 | 5 | PUB | mid/L | 없음(cell6=D-L2-base) | authorization_id, dataset_codes | datasets[1].steward_office | DEFECT-AUDIT §3.1 S10 (승인 대상 공개 뒤 범위 밖 사용); DEFECT-AUDIT §4. | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F02-OPS-02 | 6 | OPS | late/S | early/M | authorization_id, tenant_ids | tenants[2].shard | AV-S14 | late prelude 6+, max_steps 14 |
| AVv2-F02-PRV-02 | 7 | PRV | mid/M | 없음(cell6=D-L2-base) | authorization_id, cohort_ids | cohorts[1].irb_protocol | DEFECT-AUDIT §3.1 S10 (승인 대상 bound 공개 뒤 범위 밖 사용 = ST); DEFEC | D-1/D-5 교차 검사, V-10b 길이 균형 |
