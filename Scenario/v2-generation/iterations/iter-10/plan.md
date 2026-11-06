# iter-10 계획

계열: F08

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 7. 새 시나리오 4개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F08-PRV-01 | 1 | PRV | early/M | 없음(cell6=D-L2-base) | authorization_id, fields | fields[5].cleared_for_release | DEFECT-AUDIT §3 S05 (허용 외 필드 포함); S60 (숨은 원본 열 포함, test 시나리오 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F08-PUB-01 | 2 | PUB | late/L | mid/S | authorization_id, segments | segments[2].open_data_ok | DEFECT-AUDIT §3 S51 (최소 집단 크기 지름길 → 더 잘게 나누는 분류 차원 후보); S05  | late prelude 6+, max_steps 14 |
| AVv2-F08-FIN-01 | 3 | FIN | early/S | 없음(cell6=D-L2-base) | authorization_id, ledger_fields | ledger_fields[4].auditor_visible | DEFECT-AUDIT §3 S60 (숨은 private_account 열을 export 후보로: test  | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F08-OPS-01 | 4 | OPS | late/M | mid/L | authorization_id, log_fields | log_fields[4].customer_shareable | DEFECT-AUDIT §3 S05 (지원 번들의 허용 외 필드 포함, 케이스 718); DEFECT-AUD | late prelude 6+, max_steps 14 |
