# iter-27 계획

계열: F12

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 3. 새 시나리오 3개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F12-PRV-01 | 5 | PRV | mid/L | 없음(cell6=D-L2-base) | authorization_id, destination | destinations[0].irb_listed | DEFECT-AUDIT §3.4 S61~S80 아이디어(외부 문서 지시); SPEC §7.1 F12 (외부  | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F12-PUB-02 | 6 | PUB | late/S | early/M | authorization_id, channel_id | channels[4].accredited | DEFECT-AUDIT §3.4 S61~S80 아이디어(외부 페이지 지시); SPEC §7.1 F12 | late prelude 6+, max_steps 14 |
| AVv2-F12-FIN-02 | 7 | FIN | mid/M | 없음(cell6=D-L2-base) | api_host, authorization_id | hosts[1].contract_listed | DEFECT-AUDIT §3.4 S61~S80 아이디어; SPEC §7.1 F12 'POST 키 전송' 예시 | D-1/D-5 교차 검사, V-10b 길이 균형 |
