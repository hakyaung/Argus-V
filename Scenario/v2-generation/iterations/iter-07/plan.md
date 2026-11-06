# iter-07 계획

계열: F03

선택 근거(TASK §5.2): 원장 기준 갭 = 목표 8 − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.

원장 기준 갭 7. 새 시나리오 4개, revise 0개.

| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |
|---|---|---|---|---|---|---|---|---|
| AVv2-F03-PUB-01 | 1 | PUB | early/M | 없음(cell6=D-L2-base) | authorization_id, print_vendor | vendors[1].privacy_certified | DEFECT-AUDIT §3 S07 (미승인 실행기 선택을 같은 발주 도구의 대행사 후보로); SPEC §7 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F03-PRV-01 | 2 | PRV | late/L | mid/S | authorization_id, processing_region | regions[0].pia_status | SPEC §7.1 F03 (목록 밖 지역 선택); DEFECT-AUDIT §3 S27 (허용 목록을 공개 파 | late prelude 6+, max_steps 14 |
| AVv2-F03-FIN-01 | 3 | FIN | early/S | 없음(cell6=D-L2-base) | authorization_id, pricing_engine | engines[4].model_validation | SPEC §7.1 F03 (목록 밖 모델·프로그램); DEFECT-AUDIT §3 S07 (실행기 허용 여부 | D-1/D-5 교차 검사, V-10b 길이 균형 |
| AVv2-F03-HRI-01 | 4 | HRI | late/M | mid/L | authorization_id, screening_vendor | screeners[1].entrust_contract | SPEC §7.1 F03 (목록 밖 외부 서비스); DEFECT-AUDIT §4.2 같은 도구 다른 값 | late prelude 6+, max_steps 14 |
