const REPO = process.env.HF_DATASET_REPO || "hakuuung/gpt-oss-20b-hidden-state-raw-data";
const STATE_PATH = process.env.HF_STATE_PATH || "label_review/ArgusV_november_review_progress.json";
const AUTO_REVIEW_PATH = process.env.HF_AUTO_REVIEW_PATH || "label_review/ArgusV_november_auto_review.json.gz";
const QUEUE_PATH = process.env.HF_QUEUE_PATH || "label_review/ArgusV_november_queue.json";
const SHARD_PREFIX = process.env.HF_QUEUE_SHARD_PREFIX || "label_review/queue/ArgusV_november_queue";
const BASE = "https://huggingface.co";
const { gunzipSync } = require("node:zlib");
let automaticReviewPromise = null;

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
  if (!path.endsWith(".gz")) return response.json();
  const bytes = Buffer.from(await response.arrayBuffer());
  return JSON.parse(gunzipSync(bytes).toString("utf8"));
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
      user_authorized_auto_adjudication: "10월 AV-LABEL-012와 11월 기준 문서에 따른 근거 확인 자동 판정 여부.",
      adjudication_final: "사람 또는 명시 위임 기준에 따라 최종 확정됐는지. 보류는 false.",
    },
    samples: [], trajectory_reviews: [], label_revision_history: [], trajectory_revision_history: [],
    automatic_review_decisions: {}, automatic_review_history: [],
    quarantined_trajectories: [], excluded_reviews: [], task_state: {},
  };
}

function mergeAutoReview(reviewState, automatic) {
  if (!automatic || typeof automatic !== "object") return reviewState;
  reviewState.task_state ||= {};
  reviewState.samples ||= [];
  const suppressedUnitIds = new Set();
  const manualStepIds = new Set();
  for (const [unitId, autoTask] of Object.entries(automatic.task_state || {})) {
    const current = reviewState.task_state[unitId];
    if (current?.review_disposition && current.review_disposition !== "normal") { suppressedUnitIds.add(unitId); continue; }
    const activeLease = current?.status === "in_progress" && Date.parse(current.lease_expires_at || "") > Date.now();
    const labelsByStep = new Map((current?.labels || []).map((label) => [Number(label.step_index), label]));
    const autoLabelsByStep = new Map((autoTask.labels || []).map((label) => [Number(label.step_index), label]));
    for (const label of current?.labels || []) {
      const automaticLabel = autoLabelsByStep.get(Number(label.step_index));
      if (!automaticLabel || JSON.stringify(label) !== JSON.stringify(automaticLabel)) manualStepIds.add(`${unitId}:${Number(label.step_index)}`);
    }
    if (current?.status === "completed") continue;
    for (const label of autoTask.labels || []) if (!labelsByStep.has(Number(label.step_index))) labelsByStep.set(Number(label.step_index), label);
    reviewState.task_state[unitId] = {
      ...(autoTask || {}), ...(current || {}),
      labels: [...labelsByStep.values()].sort((a, b) => Number(a.step_index) - Number(b.step_index)),
      status: activeLease ? "in_progress" : current?.status && current.status !== "queued" ? current.status : autoTask.status,
      lease_token: activeLease ? current.lease_token : null,
      lease_expires_at: activeLease ? current.lease_expires_at : null,
      owner_client_id: activeLease ? current.owner_client_id : null,
      owner_tab_id: activeLease ? current.owner_tab_id : null,
      review_disposition: current?.review_disposition || autoTask.review_disposition || "normal",
    };
  }
  const existingSamples = new Set(reviewState.samples.map((sample) => `${sample.unit_id}:${sample.step_index}`));
  for (const sample of automatic.samples || []) {
    const key = `${sample.unit_id}:${sample.step_index}`;
    if (!existingSamples.has(key) && !suppressedUnitIds.has(sample.unit_id) && !manualStepIds.has(key)) {
      reviewState.samples.push(sample); existingSamples.add(key);
    }
  }
  reviewState.pending_reviews = Object.entries(reviewState.task_state).flatMap(([unit_id, task]) => (task.labels || [])
    .filter((label) => label.review_status === "draft" || label.review_status === "hold")
    .map((label) => ({ unit_id, action_id: label.action_id, step_index: label.step_index, ...label,
      review_status: label.review_status === "hold" ? "ON_HOLD_AMBIGUOUS_REVIEW" : "PENDING_HUMAN_REVIEW", adjudication_final: false })));
  reviewState.automatic_review = automatic.metadata || { sidecar_path: AUTO_REVIEW_PATH };
  return reviewState;
}

async function readState() {
  const [reviewState, automatic] = await Promise.all([
    readJson(STATE_PATH, emptyState()),
    automaticReviewPromise ||= readJson(AUTO_REVIEW_PATH, null).catch((error) => { automaticReviewPromise = null; throw error; }),
  ]);
  return mergeAutoReview(reviewState, automatic);
}

async function readAutomaticReview() {
  return automaticReviewPromise ||= readJson(AUTO_REVIEW_PATH, null).catch((error) => { automaticReviewPromise = null; throw error; });
}

function projectManualStateForCommit(state, originalManualState, baselineTaskFingerprints, automatic) {
  // Keep immutable automated labels in AUTO_REVIEW_PATH. Persist only changed human
  // samples and task/lease overrides so heartbeats do not copy the large overlay.
  const autoSampleByKey = new Map((automatic?.samples || []).map((sample) => [`${sample.unit_id}:${sample.step_index}`, sample]));
  state.samples = (state.samples || []).filter((sample) => {
    const automaticSample = autoSampleByKey.get(`${sample.unit_id}:${sample.step_index}`);
    return !automaticSample || JSON.stringify(sample) !== JSON.stringify(automaticSample);
  });

  const manualUnitIds = new Set(Object.keys(originalManualState.task_state || {}));
  for (const [unitId, task] of Object.entries(state.task_state || {})) {
    if (manualUnitIds.has(unitId)) continue;
    if (JSON.stringify(task) === baselineTaskFingerprints.get(unitId)) {
      delete state.task_state[unitId];
      continue;
    }
    const automaticLabels = automatic?.task_state?.[unitId]?.labels || [];
    if (task.status !== "completed" && JSON.stringify(task.labels || []) === JSON.stringify(automaticLabels)) delete task.labels;
    if (!task.revision_in_progress && JSON.stringify(task.revision_base_labels || []) === JSON.stringify(automaticLabels)) delete task.revision_base_labels;
  }
  state.pending_reviews = Object.entries(state.task_state || {}).flatMap(([unit_id, task]) => (task.labels || [])
    .filter((label) => label.review_status === "draft" || label.review_status === "hold")
    .map((label) => ({ unit_id, action_id: label.action_id, step_index: label.step_index, ...label,
      review_status: label.review_status === "hold" ? "ON_HOLD_AMBIGUOUS_REVIEW" : "PENDING_HUMAN_REVIEW", adjudication_final: false })));
  delete state.automatic_review;
  return state;
}

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
    const [info, originalManualState, automatic] = await Promise.all([
      repoInfo(), readJson(STATE_PATH, emptyState()),
      automaticReviewPromise ||= readJson(AUTO_REVIEW_PATH, null).catch((error) => { automaticReviewPromise = null; throw error; }),
    ]);
    const state = mergeAutoReview(structuredClone(originalManualState), automatic);
    const baselineTaskFingerprints = new Map(Object.entries(state.task_state || {}).map(([unitId, task]) => [unitId, JSON.stringify(task)]));
    const result = await mutator(state);
    if (result?.noCommit) return result.value;
    try {
      const manualState = projectManualStateForCommit(state, originalManualState, baselineTaskFingerprints, automatic);
      const commitResult = await commitState(manualState, info.sha);
      return { ...result?.value, commit_url: commitResult?.commitUrl || null };
    } catch (error) {
      lastError = error;
      if (!/parent|commit|revision|conflict|intermediate/i.test(error.message || "")) throw error;
    }
  }
  throw Object.assign(new Error(`HF_CONCURRENT_UPDATE_RETRY_EXHAUSTED: ${lastError?.message || ""}`), { status: 409 });
}

module.exports = { REPO, STATE_PATH, AUTO_REVIEW_PATH, QUEUE_PATH, SHARD_PREFIX, readQueue, readTask, readState, readAutomaticReview, mutateState };
