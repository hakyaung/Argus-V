const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { mutateState } = require("../lib/hf-store");
module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    await requireAuthor(req); const { unit_id, lease_token } = req.body || {};
    const result = await mutateState((state) => {
      const task = state.task_state?.[unit_id];
      if (!task || task.lease_token !== lease_token) return { noCommit: true, value: { ok: false } };
      task.status = task.labels?.some((label)=>label.review_status === "hold") ? "needs_adjudication" : "queued";
      task.lease_token = null; task.lease_expires_at = null; task.updated_at = new Date().toISOString();
      return { value: { ok: true } };
    });
    return json(res, result?.ok ? 200 : 409, result || { ok: false });
  } catch (error) { return handlerErrors(res, error); }
};
