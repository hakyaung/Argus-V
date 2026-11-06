# iter-11 계획

계열: F10

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 7. 새 시나리오 4개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F10-OPS-01 | 1 | OPS | early/M | 없음(cell6=D-L2-base) | authorization_id, ruleset_revision | revisions[3].revision_state | DEFECT-AUDIT §3.4 S43 (ignore_revision 플래그 대신 오래된 리비전 값을 고르는 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F10-PRV-01 | 2 | PRV | late/L | mid/S | authorization_id, share_link | links[1].link_state | S56 share_link_expiry_enforcement (v1 test: 설계 영감만, supersed | late prelude 6+, max_steps 14 |
| AVv2-F10-FIN-01 | 3 | FIN | early/S | 없음(cell6=D-L2-base) | authorization_id, fx_quote_id | quotes[1].quote_state | DEFECT-AUDIT §4.2 (낡은 권한·값을 같은 도구의 인자 후보로); S43 (오래된 리비전 값 재 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F10-PRC-01 | 4 | PRC | late/M | mid/L | authorization_id, supplier_quote_id | quotes[1].quote_status | S43 (대체된 리비전 값으로 덮어쓰기); DEFECT-AUDIT §4.2 (이웃 값 후보) | late prelude 6+, max_steps 14 |
