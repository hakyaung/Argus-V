# Argus-V 팀 라벨링 대시보드

이 대시보드는 10월 sidecar의 방식을 따라 Hugging Face 원본 rollout과 Parquet을 수정하지 않습니다. 11월 작업 인덱스는 `label_review/ArgusV_november_queue.json`, rollout 복사본은 `label_review/queue/ArgusV_november_queue_NNN.json` 샤드에 두고, 사람 라벨과 작업 상태는 `label_review/ArgusV_november_review_progress.json`에 별도로 기록합니다. 모두 같은 HF dataset 저장소에 추가되는 sidecar입니다.

## 10월 기준

10월 sidecar의 1,748개 중 사람이 직접 확인한 라벨 623개, 사용자 위임 기준 판정 1,124개, 미처리 1개를 구분해 읽기 전용 참고 패널에 표시합니다. 패널에는 직접 검수 사례와 확정 규칙도 포함됩니다.

행동마다 독립적으로 기록합니다.

- `gold_violation`: 정책·권한 경계를 위반하려는 행동인지
- `violation_executed`: 금지된 효과가 실제 발생했는지
- `tool_call_error`: 도구 형식·이름·선택·인자 오류인지
- 추가 근거: `error_type`, `reason`, `review_notes`, `exception_notes`, `pre_state_evidence`, `computed_check`, `scenario_definition_evidence`, `public_contract`, `suggested_status`

행동별 3축 검수가 주 라벨이며 trajectory 요약은 선택 입력입니다. 수정 이력, 보류, 전체 rollout 격리·제외, 작성자 표시명도 sidecar에 저장합니다. HF commit의 `parentCommit` 조건으로 동시 갱신을 확인하고, 경합하면 최신 sidecar를 다시 읽어 재시도합니다.

## 준비와 실행

Python 3.10 이상과 Node.js 20 이상이 필요합니다. 저장소 루트에서 importer 의존성을 설치하고 먼저 HF queue JSONL을 만듭니다.

```powershell
 npm install --prefix label-dashboard
py -3.10 -m pip install -r label-dashboard/requirements-import.txt
py -3.10 label-dashboard/import_hf_queue.py --since-batch 20261102 --out label-dashboard/november_queue.jsonl
```

생성 목록을 확인한 뒤 Hugging Face write token을 환경변수로 설정하고 queue sidecar를 한 번 올립니다. 이 작업은 `manifest/units.json`과 rollout 원본 파일을 변경하지 않습니다.

```powershell
$env:HF_TOKEN = "<HF write token>"
node label-dashboard/publish_hf_queue.js label-dashboard/november_queue.jsonl
```

이 대시보드는 타인의 GitHub 저장소를 Vercel에 연결할 필요가 없습니다. Argus-V의 `label-dashboard` 폴더를 내 Vercel 계정으로 직접 업로드하면 됩니다. PowerShell에서 저장소 루트 기준으로 실행합니다.

```powershell
npx vercel login
npx vercel --cwd .\label-dashboard
```

처음 실행 시 내 Vercel 계정으로 로그인하고 새 프로젝트를 만듭니다. 이후 프로젝트의 Settings → Environment Variables에 `HF_TOKEN`과 `DASHBOARD_ACCESS_KEY`를 등록한 뒤 production 배포를 실행합니다.

```powershell
npx vercel --cwd .\label-dashboard --prod
```

업로드가 완료되면 사이트와 API는 Vercel에서 실행되므로 내 PC가 꺼져 있어도 팀원들이 계속 사용할 수 있습니다. CLI 배포는 연결된 Git 저장소가 없으므로 코드 변경을 반영할 때 위 배포 명령을 다시 실행합니다. Vercel 서버 환경변수 `HF_TOKEN`에는 HF write token을, `DASHBOARD_ACCESS_KEY`에는 팀원끼리 별도 전달할 긴 공용 접근키를 설정합니다. 이름 입력은 여전히 개인 로그인과 별개이며, 공용 키는 API를 보호합니다. 키는 브라우저 탭 세션에만 보관되고 HF에는 기록되지 않습니다. `HF_DATASET_REPO`, `HF_QUEUE_PATH`, `HF_STATE_PATH`는 기본값이 이미 설정되어 있어 선택 사항입니다.

여러 팀원이 함께 라벨링할 때는 Vercel 배포 URL과 팀 공용 접근키를 사용합니다. 각 팀원은 자기 이름을 표시명으로 입력하지만 계정 로그인은 없습니다. 라벨 저장, 예약, heartbeat, 격리·제외는 HF sidecar에 즉시 커밋됩니다. sidecar의 작성자 이름은 공개 dataset에서 보일 수 있습니다. 최종 sidecar 다운로드는 유효한 작업 예약이 없고, 각 rollout이 완료·보류 판정·격리·제외 중 하나로 처리된 경우에만 허용됩니다.

로컬 화면 미리보기는 PowerShell에서 다음처럼 실행합니다.

```powershell
cd label-dashboard/public
py -m http.server 8000
```

`http://localhost:8000/`은 정적 미리보기로, HF manifest와 10월 참고 패널을 읽습니다. 저장·공용 예약까지 로컬에서 시험하려면 프로젝트 루트에서 `vercel dev`를 실행하고 서버 환경변수 `HF_TOKEN`, `DASHBOARD_ACCESS_KEY`를 설정해야 합니다. Python 정적 서버에는 Vercel API가 포함되지 않습니다.

## HF sidecar 저장 구조

- `label_review/ArgusV_november_queue.json`: rollout shard 위치를 담은 작업 인덱스와 HF source revision
- `label_review/queue/ArgusV_november_queue_NNN.json`: 원본 shard에서 선별한 검수용 rollout/context 복사본(50개 단위)
- `label_review/ArgusV_november_review_progress.json`: 사람 라벨, 초안, 작성자, 예약/만료 상태, 수정 이력, 격리·제외 기록
- 기존 October sidecar와 raw rollout/Parquet은 읽기만 하며 수정하지 않습니다.
