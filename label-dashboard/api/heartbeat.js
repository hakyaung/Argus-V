const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { mutateState } = require("../lib/hf-store");
module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    await requireAuthor(req);
    const { unit_id, lease_token } = req.body || {};
    const result = await mutateState((state) => {
      const task = state.task_state?.[unit_id];
      if (!task || task.lease_token !== lease_token || task.status !== "in_progress") return { noCommit: true, value: { ok: false } };
      task.lease_expires_at = new Date(Date.now() + 25 * 60_000).toISOString(); task.updated_at = new Date().toISOString();
      return { value: { ok: true } };
    });
    return json(res, result?.ok ? 200 : 409, result || { ok: false });
  } catch (error) { return handlerErrors(res, error); }
};
