const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { mutateState } = require("../lib/hf-store");
module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    const author = await requireAuthor(req);
    const { unit_id, lease_token, disposition, note } = req.body || {};
    if (!unit_id || !lease_token || !["quarantined", "excluded"].includes(disposition) || !String(note || "").trim()) return json(res, 400, { error: "INVALID_DISPOSITION_PAYLOAD" });
    const result = await mutateState((state) => {
      const task = state.task_state?.[unit_id];
      if (!task || task.status !== "in_progress" || task.lease_token !== lease_token || Date.parse(task.lease_expires_at || "") <= Date.now()) return { noCommit: true, value: { ok: false } };
      task.status = "queued"; task.review_disposition = disposition; task.disposition_note = String(note).trim();
      task.disposition_author_name = author.displayName; task.lease_token = null; task.lease_expires_at = null; task.updated_at = new Date().toISOString();
      task.owner_client_id = null; task.owner_tab_id = null; task.revision_in_progress = false;
      task.revision_base_labels = undefined; task.revision_base_trajectory_label = undefined;
      task.revision_base_author_name = undefined; task.revision_started_at = null;
      state.samples = (state.samples || []).filter((item) => item.unit_id !== unit_id);
      state.trajectory_reviews = (state.trajectory_reviews || []).filter((item) => item.unit_id !== unit_id);
      state.pending_reviews = (state.pending_reviews || []).filter((item) => item.unit_id !== unit_id);
      const key = disposition === "quarantined" ? "quarantined_trajectories" : "excluded_reviews";
      state[key] ||= []; state[key] = state[key].filter((item) => item.unit_id !== unit_id);
      state[key].push({ unit_id, trajectory_id: unit_id, reason: task.disposition_note, reviewer: author.displayName, reviewed_at: task.updated_at });
      return { value: { ok: true, review_disposition: disposition } };
    });
    return json(res, result?.ok ? 200 : 409, result || { ok: false });
  } catch (error) { return handlerErrors(res, error); }
};
