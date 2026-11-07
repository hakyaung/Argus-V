const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { readQueue, readTask, mutateState } = require("../lib/hf-store");
const { toSample } = require("./export");

module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    const author = await requireAuthor(req);
    const { unit_id, lease_token, labels, trajectory = {}, finalize = false } = req.body || {};
    if (!unit_id || !lease_token || !Array.isArray(labels) || labels.length > 300) return json(res, 400, { error: "INVALID_LABEL_PAYLOAD" });
    const queue = await readQueue();
    const source = await readTask(queue, unit_id);
    if (!source) return json(res, 404, { error: "HF_TASK_NOT_FOUND" });
    const result = await mutateState((sidecar) => {
      const prior = sidecar.task_state?.[unit_id];
      if (!prior || prior.status !== "in_progress" || prior.lease_token !== lease_token || Date.parse(prior.lease_expires_at || "") <= Date.now()) {
        return { noCommit: true, value: { ok: false, error: "LEASE_EXPIRED_OR_NOT_OWNED" } };
      }
      const expected = (source.payload?.files || []).filter((file) => /^rollout\/steps\/step_\d+\/step\.json$/.test(file.path)).map((file) => Number(file.path.match(/step_(\d+)/)[1])).sort((a,b)=>a-b);
      const supplied = labels.map((label) => Number(label.step_index)).sort((a,b)=>a-b);
      if (finalize && (expected.length !== supplied.length || expected.some((value,index)=>value !== supplied[index]))) {
        return { noCommit: true, value: { ok: false, error: "ALL_ACTION_STEPS_REQUIRED" } };
      }
      const stamp = new Date().toISOString();
      const oldLabels = prior.labels || [];
      const revisionBaseLabels = prior.revision_in_progress && Array.isArray(prior.revision_base_labels)
        ? prior.revision_base_labels : oldLabels;
      const hasRevisionTrajectorySnapshot = prior.revision_in_progress && Object.prototype.hasOwnProperty.call(prior, "revision_base_trajectory_label");
      const revisionBaseTrajectory = hasRevisionTrajectorySnapshot ? prior.revision_base_trajectory_label : (prior.trajectory_label || null);
      const comparable = (value) => {
        const row = value || {};
        return JSON.stringify({ action_id: row.action_id || "", step_index: Number(row.step_index),
          gold_violation: row.gold_violation ?? null, violation_executed: row.violation_executed ?? null,
          tool_call_error: row.tool_call_error ?? null, error_type: row.error_type || null,
          review_notes: row.review_notes || [], exception_notes: row.exception_notes || [],
          pre_state_evidence: row.pre_state_evidence || [], computed_check: row.computed_check || null,
          scenario_definition_evidence: row.scenario_definition_evidence || [], public_contract: row.public_contract || [],
          suggested_status: row.suggested_status || null, notes: row.notes || "", review_status: row.review_status || "draft" });
      };
      const normalized = labels.map((label) => {
        const previous = oldLabels.find((item) => Number(item.step_index) === Number(label.step_index));
        const changed = !previous || comparable(previous) !== comparable(label);
        // An automatic label stays delegated until a reviewer explicitly confirms
        // it in /api/auto-review. Saving an unrelated hold in the same rollout must
        // never count as the human's final check of this untouched automatic step.
        const awaitingAutoHumanReview = previous?.user_authorized_auto_adjudication === true && previous?.human_confirmed !== true;
        return { ...label,
          human_confirmed: awaitingAutoHumanReview ? false : changed ? label.review_status === "confirmed" : previous?.human_confirmed === true,
          user_authorized_auto_adjudication: previous?.user_authorized_auto_adjudication === true || label.user_authorized_auto_adjudication === true,
          adjudication_final: label.review_status === "confirmed" ? true : false,
          delegation_rule_id: previous?.delegation_rule_id || label.delegation_rule_id || null,
          author_name: changed ? author.displayName : (previous?.author_name || author.displayName),
          updated_at: changed ? stamp : (previous?.updated_at || stamp) };
      });
      if (finalize && normalized.some((label) => label.review_status !== "hold" && [label.gold_violation,label.violation_executed,label.tool_call_error].some((value)=>![0,1].includes(value)))) {
        return { noCommit: true, value: { ok: false, error: "ALL_THREE_LABELS_REQUIRED" } };
      }
      if (finalize && normalized.some((label) => label.review_status === "hold" && !String(label.notes || "").trim()))
        return { noCommit: true, value: { ok: false, error: "ACTION_HOLD_REQUIRES_NOTE" } };
      if (finalize && trajectory.review_status === "confirmed" && Number(trajectory.trajectory_has_violation) === 1
        && !normalized.some((label) => label.action_id === trajectory.evidence_action_id && label.gold_violation === 1 && label.review_status === "confirmed"))
        return { noCommit: true, value: { ok: false, error: "TRAJECTORY_EVIDENCE_NOT_CONFIRMED" } };
      sidecar.task_state ||= {};
      sidecar.samples ||= []; sidecar.trajectory_reviews ||= []; sidecar.label_revision_history ||= [];
      sidecar.trajectory_revision_history ||= [];
      normalized.forEach((label) => {
        const previous = oldLabels.find((item) => Number(item.step_index) === Number(label.step_index));
        const revisionPrevious = revisionBaseLabels.find((item) => Number(item.step_index) === Number(label.step_index));
        const shouldRecordRevision = prior.revision_in_progress ? finalize : true;
        if (shouldRecordRevision && revisionPrevious && comparable(revisionPrevious) !== comparable(label)) sidecar.label_revision_history.push({
          at: stamp, unit_id, action_id: label.action_id, step_index: label.step_index,
          confirmed_by: author.displayName, previous_record: revisionPrevious,
          new_record: label, change_reason: label.notes || "라벨 수정",
          previous_label: [revisionPrevious.gold_violation,revisionPrevious.violation_executed,revisionPrevious.tool_call_error],
          confirmed_label: [label.gold_violation,label.violation_executed,label.tool_call_error],
          proposal: { review_type: "APPLIED_LABEL_REVISION", review_status: "APPLIED", label_changes_applied: true },
        });
        const exported = toSample(source, label);
        const index = sidecar.samples.findIndex((item) => item.unit_id === unit_id && Number(item.step_index) === Number(label.step_index));
        if (prior.revision_in_progress && !finalize) {
          // Keep the published completed sample unchanged until the revision is submitted.
        } else if (label.review_status === "confirmed" || label.review_status === "hold" || finalize) {
          if (index >= 0) sidecar.samples[index] = exported; else sidecar.samples.push(exported);
        } else if (index >= 0) sidecar.samples.splice(index, 1);
      });
      const hold = normalized.some((label) => label.review_status === "hold") || trajectory.review_status === "hold";
      const status = finalize ? (hold ? "needs_adjudication" : "completed") : "in_progress";
      if (prior.revision_in_progress && finalize) {
        const hasSummary = (value) => Boolean(value && (value.review_status === "confirmed" || value.review_status === "hold"
          || value.trajectory_has_violation != null || String(value.notes || "").trim() || value.evidence_action_id));
        const previousSummary = hasSummary(revisionBaseTrajectory) ? revisionBaseTrajectory : null;
        const updatedSummary = hasSummary(trajectory) ? trajectory : null;
        if ((previousSummary || updatedSummary) && comparable(previousSummary) !== comparable(updatedSummary)) {
          sidecar.trajectory_revision_history.push({
            at: stamp, unit_id, trajectory_id: unit_id, confirmed_by: author.displayName,
            previous_record: previousSummary, new_record: updatedSummary,
            previous_value: previousSummary?.trajectory_has_violation ?? null,
            confirmed_value: updatedSummary?.trajectory_has_violation ?? null,
            change_reason: trajectory.notes || "실행 전체 요약 수정",
            proposal: { review_type: "APPLIED_TRAJECTORY_REVISION", review_status: "APPLIED", label_changes_applied: true },
          });
        }
      }
      sidecar.task_state[unit_id] = { ...prior, labels: normalized, trajectory_label: trajectory,
        status, lease_token: finalize ? null : lease_token, lease_expires_at: finalize ? null : new Date(Date.now() + 25 * 60_000).toISOString(),
        owner_client_id: finalize ? null : prior.owner_client_id,
        owner_tab_id: finalize ? null : prior.owner_tab_id,
        revision_in_progress: finalize ? false : prior.revision_in_progress,
        revision_base_labels: finalize ? undefined : prior.revision_base_labels,
        revision_base_trajectory_label: finalize ? undefined : prior.revision_base_trajectory_label,
        revision_base_author_name: finalize ? undefined : prior.revision_base_author_name,
        revision_started_at: finalize ? null : prior.revision_started_at,
        author_name: author.displayName, updated_at: stamp };
      if (!prior.revision_in_progress || finalize) sidecar.trajectory_reviews = sidecar.trajectory_reviews.filter((item) => item.unit_id !== unit_id);
      if ((!prior.revision_in_progress || finalize) && (trajectory.review_status === "confirmed" || trajectory.review_status === "hold")) sidecar.trajectory_reviews.push({
        unit_id, trajectory_id: unit_id, scenario_id: source.scenario_id, condition: source.condition, seed: source.seed,
        trajectory_has_violation: trajectory.trajectory_has_violation == null || trajectory.trajectory_has_violation === "" ? null : Number(trajectory.trajectory_has_violation),
        evidence_action_id: trajectory.evidence_action_id || null, human_confirmed: trajectory.review_status === "confirmed",
        review_status: trajectory.review_status === "hold" ? "ON_HOLD_AMBIGUOUS_REVIEW" : "REVIEWED",
        reviewer: author.displayName, reviewed_at: stamp, reason: trajectory.notes || "",
      });
      sidecar.review_progress = { exported_action_count: sidecar.samples.length,
        confirmed_action_count: sidecar.samples.filter((item)=>["REVIEWED", "REVIEWED_UNDER_USER_DELEGATION"].includes(item.review_status)).length,
        held_action_count: sidecar.samples.filter((item)=>!["REVIEWED", "REVIEWED_UNDER_USER_DELEGATION"].includes(item.review_status)).length,
        total_pending: sidecar.samples.filter((item)=>!["REVIEWED", "REVIEWED_UNDER_USER_DELEGATION"].includes(item.review_status)).length,
        human_confirmed_action_count: sidecar.samples.filter((item)=>item.human_confirmed).length,
        delegated_confirmed_action_count: sidecar.samples.filter((item)=>item.review_status === "REVIEWED_UNDER_USER_DELEGATION").length };
      sidecar.source_revisions = [...new Set(queue.tasks.map((task)=>task.source_revision).filter(Boolean))];
      sidecar.data_revision = sidecar.source_revisions.length === 1 ? sidecar.source_revisions[0] : null;
      sidecar.human_confirmed = sidecar.samples.length > 0 && sidecar.samples.every((sample)=>sample.human_confirmed);
      sidecar.exported_at = stamp;
      sidecar.pending_reviews = Object.entries(sidecar.task_state).flatMap(([pendingUnit, task]) => (task.labels || [])
        .filter((label)=>label.review_status === "draft" || label.review_status === "hold")
        .map((label)=>({ unit_id: pendingUnit, action_id: label.action_id, step_index: label.step_index,
          ...label, review_status: label.review_status === "hold" ? "ON_HOLD_AMBIGUOUS_REVIEW" : "PENDING_HUMAN_REVIEW", adjudication_final: false })));
      sidecar.trajectory_violation_evidence = Object.fromEntries((sidecar.trajectory_reviews || [])
        .filter((review)=>review.review_status === "REVIEWED")
        .map((review)=>[review.trajectory_id, { trajectory_has_violation: review.trajectory_has_violation === 1,
          ...(review.evidence_action_id ? { evidence_action_id: review.evidence_action_id } : {}) }]));
      return { value: { ok: true, status } };
    });
    return json(res, result?.ok === false ? 409 : 200, result || { ok: true });
  } catch (error) { return handlerErrors(res, error); }
};
