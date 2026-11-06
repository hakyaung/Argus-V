const REPO = process.env.HF_DATASET_REPO || "hakuuung/gpt-oss-20b-hidden-state-raw-data";
const STATE_PATH = process.env.HF_STATE_PATH || "label_review/ArgusV_november_review_progress.json";
const QUEUE_PATH = process.env.HF_QUEUE_PATH || "label_review/ArgusV_november_queue.json";
const SHARD_PREFIX = process.env.HF_QUEUE_SHARD_PREFIX || "label_review/queue/ArgusV_november_queue";
const BASE = "https://huggingface.co";

function token() {
  const value = process.env.HF_TOKEN;
  if (!value) throw Object.assign(new Error("HF_WRITE_TOKEN_NOT_CONFIGURED"), { status: 503 });
  return value;
}

async function repoInfo() {
  const response = await fetch(`${BASE}/api/datasets/${REPO}`, { headers: { authorization: `Bearer ${token()}` }, cache: "no-store" });
  if (!response.ok) throw Object.assign(new Error(`HF_REPO_INFO_${response.status}`), { status: 502 });
  return response.json();
}

async function readJson(path, fallback) {
  const response = await fetch(`${BASE}/datasets/${REPO}/resolve/main/${path}?download=true`, {
    headers: { authorization: `Bearer ${token()}` }, cache: "no-store",
  });
  if (response.status === 404) return fallback;
  if (!response.ok) throw Object.assign(new Error(`HF_READ_${path}_${response.status}`), { status: 502 });
  return response.json();
}

async function readQueue() {
  const value = await readJson(QUEUE_PATH, null);
  if (!value || !Array.isArray(value.tasks)) throw Object.assign(new Error("HF_QUEUE_INDEX_MISSING"), { status: 503 });
  return value;
}

const shardCache = new Map();
async function readTask(queue, unitId) {
  const entry = queue.tasks.find((task) => task.unit_id === unitId);
  if (!entry) return null;
  if (entry.payload) return entry;
  if (!entry.queue_shard) throw Object.assign(new Error("HF_QUEUE_TASK_PAYLOAD_MISSING"), { status: 503 });
  let shard = shardCache.get(entry.queue_shard);
  if (!shard) {
    shard = await readJson(entry.queue_shard, null);
    if (!shard || !Array.isArray(shard.tasks)) throw Object.assign(new Error("HF_QUEUE_SHARD_MISSING"), { status: 503 });
    if (shardCache.size >= 16) shardCache.delete(shardCache.keys().next().value);
    shardCache.set(entry.queue_shard, shard);
  }
  return shard.tasks.find((task) => task.unit_id === unitId) || null;
}

function emptyState() {
  return {
    schema_version: "argus-v.collaborative-label-review-draft.v1",
    code_revision: "november-team-review-dashboard",
    overlay_note: "Supplemental human-review sidecar. Raw Hugging Face rollout and Parquet files are not modified.",
    reviewer_names_are_public: true,
    field_definitions: {
      gold_violation: "정책 위반 시도: 0/1/null.",
      violation_executed: "금지된 효과가 실제 발생했는지: 0/1/null.",
      tool_call_error: "도구 호출 형식·선택·인자 오류: 0/1/null.",
      error_type: "tool_call_error=1일 때 구체적 오류 유형.",
      reviewer: "라벨 작성자의 공개 표시명.",
      user_authorized_auto_adjudication: "사용자 위임 기준에 따른 판정 여부. 11월 작업은 모두 false.",
      adjudication_final: "사람이 확정했는지. 보류는 false.",
    },
    samples: [], trajectory_reviews: [], label_revision_history: [],
    quarantined_trajectories: [], excluded_reviews: [], task_state: {},
  };
}

async function readState() { return readJson(STATE_PATH, emptyState()); }

async function commitState(state, expectedSha) {
  const { commit } = await import("@huggingface/hub");
  const result = await commit({
    repo: { type: "dataset", name: REPO }, accessToken: token(), parentCommit: expectedSha,
    title: "Update November human review sidecar",
    operations: [{ operation: "addOrUpdate", path: STATE_PATH,
      content: new Blob([JSON.stringify(state, null, 2)], { type: "application/json" }) }],
  });
  return result;
}

async function mutateState(mutator, attempts = 5) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const [info, state] = await Promise.all([repoInfo(), readState()]);
    const result = await mutator(state);
    if (result?.noCommit) return result.value;
    try {
      const commitResult = await commitState(state, info.sha);
      return { ...result?.value, commit_url: commitResult?.commitUrl || null };
    } catch (error) {
      lastError = error;
      if (!/parent|commit|revision|conflict|intermediate/i.test(error.message || "")) throw error;
    }
  }
  throw Object.assign(new Error(`HF_CONCURRENT_UPDATE_RETRY_EXHAUSTED: ${lastError?.message || ""}`), { status: 409 });
}

module.exports = { REPO, STATE_PATH, QUEUE_PATH, SHARD_PREFIX, readQueue, readTask, readState, mutateState };
