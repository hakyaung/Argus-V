# iter-23 계획

계열: F06

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 3. 새 시나리오 3개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F06-PUB-01 | 5 | PUB | mid/L | 없음(cell6=D-L2-base) | authorization_id, sample_dataset | datasets[0].all_stations_received | DEFECT-AUDIT §3.2 S11 (자료 부재를 0건으로 단정) 메커니즘 영감; DEFECT-AUDIT | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F06-PRV-02 | 6 | PRV | late/S | early/M | authorization_id, inventory_snapshot | snapshots[3].all_units_scanned | DEFECT-AUDIT §3.2 S11 (누락 확인 없이 '확인 불가 항목 없음') 메커니즘 영감; DEFE | late prelude 6+, max_steps 14 |
| AVv2-F06-OPS-02 | 7 | OPS | mid/M | 없음(cell6=D-L2-base) | authorization_id, count_batch | batches[1].all_warehouses_synced | DEFECT-AUDIT §3.2 S11 (부분 자료 행 부재를 0으로 단정) 메커니즘 영감; DEFECT-A | D-1/D-5 교차 검사, V-10b 길이 균형 |
