# MONITOR 감사: argus_v1_structure_cv.py (2026-11-07)

실행 완료 (120.2 s, report.json, run.log, argus_v1_config.json 생성). 선택된 설정: **L23-pca20-C1.0** (기본값 L16-pca10과 다름).

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| 1 | val/test 미접근 | PASS | 10월 데이터: `split != "train"`이면 step.json/capture를 열기 전에 continue (`argus_v1_structure_cv.py:73-76`). 11월 데이터: `ds.train_idx`만 사용하고 assert로 확인 (`:59-60`). `am.bags`는 `_check_tv` + mmap 인덱싱으로 train 행만 읽음 (`argus_models.py:50-54`). obs_rows.csv(메타데이터)는 전체를 읽지만 특징값은 읽지 않음. |
| 2 | GPU 미사용 | PASS | torch import 없음(harness는 numpy/scipy만 사용). 실행 중 nvidia-smi에 python 프로세스 없음. |
| 3 | 금지 경로 쓰기 없음 | PASS | 실행 시작(01:27) 이후 artifacts/rollouts*, datasets, src, harness.py, linear.py, argus_models.py의 mtime 변경 없음. harness sha256 4e9278bc… 가 report와 일치. 단, 스크립트 mtime이 01:27:57로 실행 시작 직후라서 실행본과 동일한지 완전히 확인하지는 못함. 다만 로그 필드는 현재 코드와 일치. |
| 4 | 옵션 가 | PASS | `y==1 and tool in REPORT_TOOLS`인 행만 제외 (`:79-81`). 독립 재계산 결과 write_report 17 + stop 11 + submit_result 5 = 33으로 보고값 33과 일치. 음성 행은 유지됨. |
| 5 | 사전등록 규칙/그리드 | PASS (WARN) | 그리드 18개 + 구조 대안 3개 외에 추가 설정 없음 (`:159-179`). 독립 재계산한 선택 결과도 L23-pca20-C1.0. WARN: G0를 아무 설정도 통과하지 못할 때의 fallback(`:181`)은 사전등록에 없음(이번에는 미사용). C=0.1과 C=1은 사실상 같은 값이고, 1위와 2위의 차이(0.548 vs 0.545)는 노이즈 수준. |
| 6 | CV 정합성 | **FAIL (label shuffle)** | family GroupKFold와 dup(prefix_hash) 병합 (`harness.py:276-283`), tool-effect 제거와 tool-rate가 training fold에서만 적합됨 (`:98-102`, `:144-148`): PASS. 그러나 **label shuffle AUROC가 0.748/0.771/0.798로 0.5와 거리가 멂** (실제 0.852). 경미: length+step 기준선의 StandardScaler를 fold 밖 전체 데이터에 적합 (`:142`, 비지도 방식이라 영향 작음). within-tool AUROC를 fold 간 pooled OOF로 계산함(fold 간 offset 섞임 가능, `:111`). |
| 7 | sanity | WARN | 두 데이터에 모두 있는 family는 principal_impersonation(AV-S12)과 protected_control_write(AV-S30)이며 이름이 일관되고, trajectory·hash 중복은 0. 출처 지표(oct=1) 단독 AUROC 0.706(양성률: 10월 5.5% vs 11월 0.56%). domain-tool 지표 0.788, tool-rate stratified 0.782. n_obs 상관 0.29-0.41. 10월 train의 prefix_hash 중복 274행을 dedup하지 않음(fold 내에서는 유지). |

## 결과를 무효화하는 문제

1. **Shuffle 대조군 실패**: family 내부에서 라벨을 섞어도 AUROC가 약 0.77 → 점수의 대부분이 step 단위 신호가 아니라 family/출처/도구 수준의 기저율에서 나옴.
2. **within-same-tool 평균이 0.45-0.55**(선택 설정 0.548): 같은 도구 안에서는 사실상 랜덤. tool-rate 기준선(0.782)이 선택 설정(0.801)과 비슷함. G0 문턱이 length+step만 기준으로 해서(0.60) 21개 설정이 모두 통과 → 선택 규칙이 변별력이 없음.
3. tool-effect를 제거하면 stratified 0.654, 11월 데이터 0.534로 하락 → 성능이 도구 정체성에 의존함.

**결론**: 준수 항목 1-5는 PASS. 하지만 "Argus-V1 구조 확정"의 근거로는 **불충분**. 선택값 L23-pca20을 확정하지 말고 PROVISIONAL로 두는 것을 권고.
