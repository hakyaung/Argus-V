const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { readQueue, readState } = require("../lib/hf-store");

function parseJson(text) { try { return JSON.parse(text || "{}"); } catch { return {}; } }
function toSample(task, label) {
  const files = task.payload?.files || [];
  const rolloutFile = files.find((file) => file.path === "rollout/rollout.json");
  const rollout = task.payload?.rollout && Object.keys(task.payload.rollout).length ? task.payload.rollout : parseJson(rolloutFile?.text);
  const stepFile = files.find((file) => Number(file.path?.match(/^rollout\/steps\/step_(\d+)\/step\.json$/)?.[1]) === Number(label.step_index));
  const contextFile = files.find((file) => Number(file.path?.match(/^rollout\/steps\/step_(\d+)\/m1-context\.json$/)?.[1]) === Number(label.step_index));
  const step = parseJson(stepFile?.text); const context = parseJson(contextFile?.text);
  const action = step.action && typeof step.action === "object" ? step.action : {};
  const actionId = label.action_id || step.action_id || `${rollout.rollout_id || rollout.trajectory_id || task.unit_id}:${label.step_index}`;
  const trajectoryId = rollout.trajectory_id || rollout.rollout_id || String(actionId).split(":")[0] || task.unit_id;
  return {
    unit_id: task.unit_id, source_revision: task.source_revision || null,
    scenario_id: task.scenario_id, condition: task.condition, seed: task.seed,
    action_id: actionId, trajectory_id: trajectoryId,
    run_id: step.run_id || rollout.run_id || `${trajectoryId}-s${String(label.step_index).padStart(3,"0")}`,
    step_index: label.step_index,
    task: rollout.task || rollout.public_context?.goal || context.task || "",
    trusted_policy: rollout.trusted_policy || (rollout.public_context?.policy || []).join("\n") || context.trusted_policy || "",
    review_phase: "november_scenario_review", review_group: "team_human_review",
    original_policy_label: step.original_policy_label ?? step.policy_label ?? step.rule_label ?? null,
    proposed_next_action_violation: step.proposed_next_action_violation ?? step.policy_label ?? step.gold_violation ?? null,
    proposed_violation_executed: step.proposed_violation_executed ?? step.violation_executed ?? null,
    proposed_tool_call_error: step.proposed_tool_call_error ?? step.tool_call_error ?? null,
    proposed_error_type: step.proposed_error_type ?? step.error_type ?? null,
    gold_violation: label.gold_violation, violation_executed: label.violation_executed,
    tool_call_error: label.tool_call_error, error_type: label.error_type || null,
    human_confirmed: label.human_confirmed === true,
    user_authorized_auto_adjudication: label.user_authorized_auto_adjudication === true,
    adjudication_final: label.adjudication_final === true,
    delegation_rule_id: label.delegation_rule_id || null,
    review_status: label.review_status === "hold" ? "ON_HOLD_AMBIGUOUS_REVIEW"
      : label.user_authorized_auto_adjudication === true && label.human_confirmed !== true ? "REVIEWED_UNDER_USER_DELEGATION" : "REVIEWED",
    reviewer: label.author_name || "team_human_review", reviewed_at: label.updated_at || null,
    kind: step.kind || step.tool_name || step.tool || action.tool || null,
    command: step.command || step.tool_call || step.action || null,
    result: step.result || step.tool_result || null, execution: step.execution || null,
    observation: step.observation || null,
    review_notes: Array.isArray(label.review_notes) ? label.review_notes : [],
    exception_notes: [...(Array.isArray(step.exception_notes) ? step.exception_notes : []), ...(Array.isArray(label.exception_notes) ? label.exception_notes : [])],
    pre_state_evidence: [...(Array.isArray(step.pre_state_evidence) ? step.pre_state_evidence : []), ...(Array.isArray(label.pre_state_evidence) ? label.pre_state_evidence : [])],
    computed_check: label.computed_check || step.computed_check || null,
    scenario_definition_evidence: label.scenario_definition_evidence || [],
    public_contract: label.public_contract || [], suggested_status: label.suggested_status || null,
    supporting_prior_action_ids: Array.isArray(step.supporting_prior_action_ids) ? step.supporting_prior_action_ids : [],
    reason: label.notes || "",
  };
}
module.exports = async function handler(req, res) {
  if (req.method !== "GET") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    await requireAuthor(req);
    const [queue, state] = await Promise.all([readQueue(), readState()]);
    const activeLeases = Object.entries(state.task_state || {})
      .filter(([, task]) => task.status === "in_progress" && Date.parse(task.lease_expires_at || "") > Date.now())
      .map(([unit_id, task]) => ({ unit_id, author_name: task.author_name || "", lease_expires_at: task.lease_expires_at }));
    if (activeLeases.length) return json(res, 409, { error: "ACTIVE_LABELING_LEASES", active_leases: activeLeases });
    const incomplete = queue.tasks.filter((source) => {
      const task = state.task_state?.[source.unit_id] || {};
      return (task.review_disposition || "normal") === "normal" && !["completed", "needs_adjudication"].includes(task.status);
    });
    if (incomplete.length) return json(res, 409, { error: `QUEUE_TASKS_INCOMPLETE_${incomplete.length}`, incomplete_count: incomplete.length });
    const { task_state = {}, ...sidecar } = state;
    return json(res, 200, { ...sidecar, schema_version: "argus-v.collaborative-label-review-draft.v1",
      source_revisions: [...new Set((state.samples || []).map((item)=>item.source_revision).filter(Boolean))],
      next_cursor: null, has_more: false, snapshot_at: new Date().toISOString(),
      pending_reviews: Object.entries(task_state).flatMap(([unit_id, task]) => (task.labels || [])
        .filter((label) => label.review_status === "draft" || label.review_status === "hold")
        .map((label) => ({ unit_id, action_id: label.action_id, step_index: label.step_index,
          ...label, review_status: label.review_status === "hold" ? "ON_HOLD_AMBIGUOUS_REVIEW" : "PENDING_HUMAN_REVIEW", adjudication_final: false }))),
    });
  } catch (error) { return handlerErrors(res, error); }
};
module.exports.toSample = toSample;
