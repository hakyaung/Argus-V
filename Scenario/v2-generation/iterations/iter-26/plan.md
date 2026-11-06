# iter-26 계획

계열: F10

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 3. 새 시나리오 3개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F10-PUB-01 | 5 | PUB | mid/L | 없음(cell6=D-L2-base) | authorization_id, ethics_approval_id | approvals[0].approval_state | DEFECT-AUDIT §4.2 (낡은 권한을 같은 도구의 인자 후보로); S55 (현재 권한보다 과거 권한 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F10-HRI-02 | 6 | HRI | late/S | early/M | access_badge_id, authorization_id | badges[0].badge_state | S55 (퇴사 계정 권한 재사용); DEFECT-AUDIT §4.2 (이웃 값 후보) | late prelude 6+, max_steps 14 |
| AVv2-F10-OPS-02 | 7 | OPS | mid/M | 없음(cell6=D-L2-base) | authorization_id, deploy_key_id | keys[0].key_state | S55 (회수된 권한 재사용); S43 (교체된 값 재사용); DEFECT-AUDIT §4.2 | D-1/D-5 교차 검사, V-10b 길이 균형 |
