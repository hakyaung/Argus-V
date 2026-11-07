const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { readState, readAutomaticReview, mutateState } = require("../lib/hf-store");

const AXES = ["gold_violation", "violation_executed", "tool_call_error"];
const ERROR_TYPES = new Set(["INVALID_STATUS", "NONCANONICAL_PATH", "UNKNOWN_TOOL_CALL_FORMAT", "UNKNOWN_ARGUMENT", "WRONG_TOOL_ARGUMENT_SCHEMA", "WRONG_TOOL_SELECTION", "TIMESTAMP_FORMAT", "OTHER"]);
const keyOf = (unitId, stepIndex) => `${unitId}:${Number(stepIndex)}`;
const tupleOf = (value) => AXES.map((axis) => value?.[axis] ?? null);

function summarize(autoSamples, state) {
  const decisions = state.automatic_review_decisions || {};
  const currentSamples = new Map((state.samples || []).map((row) => [keyOf(row.unit_id, row.step_index), row]));
  let approved = 0, edited = 0;
  for (const sample of autoSamples) {
    const decision = decisions[keyOf(sample.unit_id, sample.step_index)]?.decision;
    if (decision === "approved" || (!decision && currentSamples.get(keyOf(sample.unit_id, sample.step_index))?.human_confirmed)) approved += 1;
    if (decision === "edited") edited += 1;
  }
  return { total: autoSamples.length, approved, edited, reviewed: approved + edited, pending: autoSamples.length - approved - edited };
}

module.exports = async function handler(req, res) {
  try {
    const author = await requireAuthor(req);
    if (req.method === "GET") {
      const [automatic, state] = await Promise.all([readAutomaticReview(), readState()]);
      if (!automatic || !Array.isArray(automatic.samples)) throw Object.assign(new Error("HF_AUTO_REVIEW_NOT_READY"), { status: 503 });
      const sourceRows = automatic.samples;
      const counts = summarize(sourceRows, state);
      const currentByKey = new Map((state.samples || []).map((sample) => [keyOf(sample.unit_id, sample.step_index), sample]));
      const decisions = state.automatic_review_decisions || {};
      const query = String(req.query?.q || "").trim().toLowerCase();
      const statusFilter = String(req.query?.status || "all");
      const pageSize = Math.min(50, Math.max(1, Number(req.query?.limit) || 25));
      let rows = sourceRows.map((source) => {
        const key = keyOf(source.unit_id, source.step_index);
        const current = currentByKey.get(key) || source;
        const decision = decisions[key] || null;
        const status = decision?.decision === "edited" ? "edited"
          : decision?.decision === "approved" || (!decision && current.human_confirmed) ? "approved" : "pending";
        return { source, current, decision, status };
      });
      if (statusFilter !== "all") rows = rows.filter((row) => row.status === statusFilter);
      if (query) rows = rows.filter(({ source, current, decision }) => [source.unit_id, source.scenario_id, source.action_id,
        source.kind, source.task, decision?.reviewer, current.reviewer].join(" ").toLowerCase().includes(query));
      rows.sort((a, b) => a.source.scenario_id.localeCompare(b.source.scenario_id)
        || a.source.unit_id.localeCompare(b.source.unit_id) || Number(a.source.step_index) - Number(b.source.step_index));
      const page = Math.max(1, Number(req.query?.page) || 1);
      const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
      const safePage = Math.min(page, pageCount);
      return json(res, 200, { summary: counts, total_filtered: rows.length, page: safePage, page_size: pageSize,
        page_count: pageCount, items: rows.slice((safePage - 1) * pageSize, safePage * pageSize) });
    }
    if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
    const { unit_id, step_index, lease_token, action, labels = {}, notes = "" } = req.body || {};
    if (!unit_id || !Number.isInteger(Number(step_index)) || !lease_token || !["approve", "edit", "revert"].includes(action))
      return json(res, 400, { error: "INVALID_AUTO_REVIEW_PAYLOAD" });
    const automatic = await readAutomaticReview();
    const autoSample = (automatic?.samples || []).find((sample) => sample.unit_id === unit_id && Number(sample.step_index) === Number(step_index));
    const autoLabel = (automatic?.task_state?.[unit_id]?.labels || []).find((label) => Number(label.step_index) === Number(step_index));
    if (!autoSample || !autoLabel) return json(res, 404, { error: "AUTO_LABEL_NOT_FOUND" });
    let finalValues;
    if (action === "edit") {
      finalValues = Object.fromEntries(AXES.map((axis) => [axis, Number(labels[axis])]));
      if (AXES.some((axis) => ![0, 1].includes(finalValues[axis]))) return json(res, 400, { error: "ALL_THREE_LABELS_REQUIRED" });
      finalValues.error_type = finalValues.tool_call_error === 1 ? String(labels.error_type || "") : null;
      if (finalValues.tool_call_error === 1 && !ERROR_TYPES.has(finalValues.error_type)) return json(res, 400, { error: "ERROR_TYPE_REQUIRED" });
      if (!String(notes).trim()) return json(res, 400, { error: "EDIT_REASON_REQUIRED" });
    } else finalValues = Object.fromEntries(AXES.map((axis) => [axis, Number(autoLabel[axis])]));

    const response = await mutateState((state) => {
      const task = state.task_state?.[unit_id];
      if (!task || task.status !== "in_progress" || task.lease_token !== lease_token
        || Date.parse(task.lease_expires_at || "") <= Date.now())
        return { noCommit: true, value: { ok: false, error: "LEASE_EXPIRED_OR_NOT_OWNED" } };
      if ((task.review_disposition || "normal") !== "normal") return { noCommit: true, value: { ok: false, error: "TASK_NOT_IN_NORMAL_REVIEW" } };

      const now = new Date().toISOString();
      const key = keyOf(unit_id, step_index);
      const priorLabel = (task.labels || []).find((label) => Number(label.step_index) === Number(step_index)) || autoLabel;
      const priorDecision = state.automatic_review_decisions?.[key] || null;
      const reverted = action === "revert";
      const reviewLabel = reverted ? { ...autoLabel } : {
        ...autoLabel, ...finalValues, human_confirmed: true, user_authorized_auto_adjudication: true,
        adjudication_final: true, delegation_rule_id: autoLabel.delegation_rule_id || "AV-LABEL-012",
        review_status: "confirmed", author_name: author.displayName, updated_at: now,
        notes: String(notes || (action === "approve" ? "사람이 자동 라벨과 원본 행동 증거를 확인하고 승인함." : "")).trim(),
      };
      const labelIndex = (task.labels || []).findIndex((label) => Number(label.step_index) === Number(step_index));
      task.labels ||= [];
      if (labelIndex >= 0) task.labels[labelIndex] = reviewLabel;
      else task.labels.push(reviewLabel);
      task.labels.sort((a, b) => Number(a.step_index) - Number(b.step_index));

      const priorSample = (state.samples || []).find((sample) => sample.unit_id === unit_id && Number(sample.step_index) === Number(step_index)) || autoSample;
      const finalSample = reverted ? { ...autoSample } : {
        ...autoSample, ...finalValues, human_confirmed: true, user_authorized_auto_adjudication: true,
        adjudication_final: true, delegation_rule_id: autoSample.delegation_rule_id || "AV-LABEL-012",
        review_status: "REVIEWED", reviewer: author.displayName, reviewed_at: now,
        reason: String(notes || (action === "approve" ? "사람이 자동 라벨과 원본 행동 증거를 확인하고 승인함." : "")).trim(),
        human_review_of_auto: { reviewer: author.displayName, reviewed_at: now, decision: action,
          original_label: tupleOf(autoSample), confirmed_label: tupleOf(finalValues), notes: String(notes || "").trim() },
      };
      state.samples ||= [];
      const sampleIndex = state.samples.findIndex((sample) => sample.unit_id === unit_id && Number(sample.step_index) === Number(step_index));
      if (sampleIndex >= 0) state.samples[sampleIndex] = finalSample;
      else state.samples.push(finalSample);

      state.automatic_review_decisions ||= {};
      state.automatic_review_history ||= [];
      const decision = reverted ? "unapproved" : action === "edit" ? "edited" : "approved";
      const decisionRecord = { decision, reviewer: author.displayName, reviewed_at: now,
        original_label: tupleOf(autoSample), final_label: tupleOf(finalSample), notes: String(notes || "").trim() };
      state.automatic_review_history.push({ at: now, unit_id, action_id: autoSample.action_id, step_index: Number(step_index),
        changed_by: author.displayName, previous_decision: priorDecision, new_decision: decisionRecord,
        previous_label: tupleOf(priorLabel), new_label: tupleOf(reviewLabel), reason: decisionRecord.notes });
      state.automatic_review_decisions[key] = decisionRecord;

      state.label_revision_history ||= [];
      if (JSON.stringify(tupleOf(priorLabel)) !== JSON.stringify(tupleOf(reviewLabel)) || action === "revert") state.label_revision_history.push({
        at: now, unit_id, action_id: autoSample.action_id, step_index: Number(step_index), confirmed_by: author.displayName,
        previous_record: priorLabel, new_record: reviewLabel, change_reason: decisionRecord.notes || "자동 라벨 사람 최종 검수",
        previous_label: tupleOf(priorLabel), confirmed_label: tupleOf(reviewLabel),
        proposal: { review_type: reverted ? "REVERTED_AUTO_LABEL_REVIEW" : action === "edit" ? "HUMAN_EDITED_AUTO_LABEL" : "HUMAN_APPROVED_AUTO_LABEL",
          review_status: reverted ? "REVERTED" : "APPLIED", label_changes_applied: !reverted },
      });

      const hasHold = task.labels.some((label) => label.review_status === "hold");
      const allFinal = task.labels.length > 0 && task.labels.every((label) => label.review_status === "confirmed");
      task.status = hasHold ? "needs_adjudication" : allFinal ? "completed" : "queued";
      task.lease_token = null; task.lease_expires_at = null; task.owner_client_id = null; task.owner_tab_id = null;
      task.revision_in_progress = false; task.revision_base_labels = undefined; task.revision_base_trajectory_label = undefined;
      task.revision_base_author_name = undefined; task.revision_started_at = null;
      task.author_name = author.displayName; task.updated_at = now;
      state.review_progress = { ...(state.review_progress || {}), exported_action_count: state.samples.length,
        confirmed_action_count: state.samples.filter((sample) => ["REVIEWED", "REVIEWED_UNDER_USER_DELEGATION"].includes(sample.review_status)).length,
        held_action_count: state.samples.filter((sample) => !["REVIEWED", "REVIEWED_UNDER_USER_DELEGATION"].includes(sample.review_status)).length,
        total_pending: state.samples.filter((sample) => !["REVIEWED", "REVIEWED_UNDER_USER_DELEGATION"].includes(sample.review_status)).length,
        human_confirmed_action_count: state.samples.filter((sample) => sample.human_confirmed).length,
        delegated_confirmed_action_count: state.samples.filter((sample) => sample.review_status === "REVIEWED_UNDER_USER_DELEGATION").length };
      state.pending_reviews = Object.entries(state.task_state || {}).flatMap(([pendingUnit, pendingTask]) => (pendingTask.labels || [])
        .filter((label) => label.review_status === "draft" || label.review_status === "hold")
        .map((label) => ({ unit_id: pendingUnit, action_id: label.action_id, step_index: label.step_index, ...label,
          review_status: label.review_status === "hold" ? "ON_HOLD_AMBIGUOUS_REVIEW" : "PENDING_HUMAN_REVIEW", adjudication_final: false })));
      return { value: { ok: true, decision, status: task.status } };
    });
    return json(res, response?.ok === false ? 409 : 200, response || { ok: true });
  } catch (error) { return handlerErrors(res, error); }
};
