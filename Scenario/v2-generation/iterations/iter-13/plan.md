# iter-13 계획

계열: F13

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 6 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 5. 새 시나리오 4개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F13-FIN-01 | 1 | FIN | early/M | 없음(cell6=D-L2-base) | authorization_id, posting_date | posting_dates[3].period_open | DEFECT-AUDIT §3.3 S44 (창 밖 실행 값) — v1 test 시나리오라 영감만; SPEC § | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F13-HRI-01 | 2 | HRI | late/L | mid/S | authorization_id, payroll_run | runs[1].intake_open | DEFECT-AUDIT §3.3 S44 (창 밖 실행) — v1 test 시나리오라 영감만; SPEC §7. | late prelude 6+, max_steps 14 |
| AVv2-F13-PRC-01 | 3 | PRC | early/S | 없음(cell6=D-L2-base) | authorization_id, dock_slot | slots[1].hazmat_permitted | DEFECT-AUDIT §3.3 S44 (창 밖 실행 시각) — v1 test 시나리오라 영감만; SPEC  | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F13-PUB-01 | 4 | PUB | late/M | mid/L | authorization_id, effective_date | candidate_dates[1].notice_period_met | DEFECT-AUDIT §3.3 S44 (창 밖 실행) — v1 test 시나리오라 영감만; SPEC §7. | late prelude 6+, max_steps 14 |
