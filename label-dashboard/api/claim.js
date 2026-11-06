const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { readQueue, readTask, mutateState } = require("../lib/hf-store");
const { randomUUID } = require("node:crypto");

module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    const user = await requireAuthor(req);
    const { unit_id, mode = "new", confirm_takeover = false } = req.body || {};
    if (!["new", "resume", "revise"].includes(mode)) return json(res, 400, { error: "INVALID_CLAIM_MODE" });
    const queue = await readQueue();
    const result = await mutateState(async (state) => {
      state.task_state ||= {};
      const now = Date.now();
      const candidates = unit_id ? queue.tasks.filter((task) => task.unit_id === unit_id) : queue.tasks;
      const source = candidates.find((task) => {
        const current = state.task_state[task.unit_id] || {};
        const expired = current.status === "in_progress" && Date.parse(current.lease_expires_at || "") <= now;
        const canClaimNew = mode === "new" && (current.status == null || current.status === "queued" || current.status === "needs_adjudication" || (expired && !current.revision_in_progress));
        const sameBrowser = current.owner_client_id === user.clientId;
        const sameTab = current.owner_tab_id === user.tabId;
        const sameAuthor = current.author_name === user.displayName;
        const canResume = mode === "resume" && current.status === "in_progress"
          && ((sameBrowser && (sameTab || confirm_takeover === true)) || (sameAuthor && confirm_takeover === true));
        const canRevise = mode === "revise" && (current.status === "completed" || (current.revision_in_progress && expired));
        return (canClaimNew || canResume || canRevise) && (current.review_disposition || "normal") === "normal";
      });
      if (!source) {
        const resumeConfirmationRequired = mode === "resume" && candidates.some((task) => {
          const current = state.task_state[task.unit_id] || {};
          return current.status === "in_progress" && confirm_takeover !== true && (
            (current.owner_client_id === user.clientId && current.owner_tab_id !== user.tabId)
            || (current.author_name === user.displayName && current.owner_client_id !== user.clientId)
          );
        });
        return { noCommit: true, value: resumeConfirmationRequired ? { resume_confirmation_required: true } : { task: null } };
      }
      const hydrated = await readTask(queue, source.unit_id);
      if (!hydrated) throw Object.assign(new Error("HF_QUEUE_TASK_PAYLOAD_MISSING"), { status: 503 });
      const current = state.task_state[source.unit_id] || {};
      const lease_token = randomUUID();
      const lease_expires_at = new Date(now + 25 * 60_000).toISOString();
      const revisionInProgress = mode === "revise" || current.revision_in_progress === true;
      state.task_state[source.unit_id] = { ...current, status: "in_progress", lease_token, lease_expires_at,
        owner_client_id: user.clientId, owner_tab_id: user.tabId, revision_in_progress: revisionInProgress,
        revision_base_labels: revisionInProgress ? (Array.isArray(current.revision_base_labels) ? current.revision_base_labels : (mode === "revise" ? current.labels || [] : [])) : undefined,
        revision_base_trajectory_label: revisionInProgress
          ? (Object.prototype.hasOwnProperty.call(current, "revision_base_trajectory_label") ? current.revision_base_trajectory_label : (mode === "revise" ? current.trajectory_label || null : null))
          : undefined,
        revision_base_author_name: revisionInProgress ? (current.revision_base_author_name ?? (mode === "revise" ? current.author_name || "" : "")) : undefined,
        revision_started_at: revisionInProgress ? (current.revision_started_at || new Date(now).toISOString()) : null,
        author_name: user.displayName, updated_at: new Date(now).toISOString() };
      return { value: { unit_id: source.unit_id, lease_token, labels: current.labels || [], trajectory_label: current.trajectory_label || null,
        mode: revisionInProgress ? "revise" : mode, task: { ...hydrated, lease_token } } };
    });
    if (result?.resume_confirmation_required) return json(res, 409, { error: "RESUME_REQUIRES_CONFIRMATION" });
    if (!result?.unit_id) return json(res, 200, { task: null });
    return json(res, 200, result);
  } catch (error) { return handlerErrors(res, error); }
};
