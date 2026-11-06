const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { mutateState } = require("../lib/hf-store");
module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    await requireAuthor(req); const { unit_id, lease_token } = req.body || {};
    const result = await mutateState((state) => {
      const task = state.task_state?.[unit_id];
      if (!task || task.lease_token !== lease_token) return { noCommit: true, value: { ok: false } };
      const revisionEdit = task.revision_in_progress === true || Array.isArray(task.revision_base_labels);
      if (revisionEdit) {
        task.labels = task.revision_base_labels || [];
        task.trajectory_label = task.revision_base_trajectory_label || null;
        task.author_name = task.revision_base_author_name || task.author_name;
        state.pending_reviews = (state.pending_reviews || []).filter((review) => review.unit_id !== unit_id);
      }
      task.status = revisionEdit ? "completed" : task.labels?.some((label)=>label.review_status === "hold") ? "needs_adjudication" : "queued";
      task.lease_token = null; task.lease_expires_at = null; task.owner_client_id = null;
      task.owner_tab_id = null; task.revision_in_progress = false;
      task.revision_base_labels = undefined; task.revision_base_trajectory_label = undefined;
      task.revision_base_author_name = undefined; task.revision_started_at = null;
      task.updated_at = new Date().toISOString();
      return { value: { ok: true, revision_canceled: revisionEdit } };
    });
    return json(res, result?.ok ? 200 : 409, result || { ok: false });
  } catch (error) { return handlerErrors(res, error); }
};
