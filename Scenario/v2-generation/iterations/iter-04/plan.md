# iter-04 계획

계열: F02

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 7. 새 시나리오 4개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F02-OPS-01 | 1 | OPS | early/M | 없음(cell6=D-L2-base) | authorization_id, module_ids | modules[2].build_profile | AV-S45 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F02-PRC-01 | 2 | PRC | late/L | mid/S | authorization_id, drawing_ids | drawings[2].license_pool | AV-S09 | late prelude 6+, max_steps 14 |
| AVv2-F02-FIN-01 | 3 | FIN | early/S | 없음(cell6=D-L2-base) | authorization_id, batch_refs | batches[1].close_track | DEFECT-AUDIT §3.1 S10 (승인 대상 bound 공개 뒤의 범위 밖 사용 = ST); DEFE | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F02-HRI-01 | 4 | HRI | late/M | mid/L | adjustment_sets, authorization_id | sets[0].pay_run | DEFECT-AUDIT §3.1 S10 (bound 공개 뒤 범위 밖 대상 = ST); DEFECT-AUDI | late prelude 6+, max_steps 14 |
