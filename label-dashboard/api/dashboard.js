const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { readQueue, readState } = require("../lib/hf-store");

module.exports = async function handler(req, res) {
  if (req.method !== "GET") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    await requireAuthor(req);
    const [queue, sidecar] = await Promise.all([readQueue(), readState()]);
    const tasks = queue.tasks.map((source) => {
      const saved = sidecar.task_state?.[source.unit_id] || {};
      const active = saved.status === "in_progress" && saved.lease_expires_at && Date.parse(saved.lease_expires_at) > Date.now();
      return { unit_id: source.unit_id, scenario_id: source.scenario_id, condition: source.condition,
        seed: source.seed, batch: source.batch, status: active ? "in_progress" : (saved.status === "completed" ? "completed" : saved.status === "needs_adjudication" ? "needs_adjudication" : "queued"),
        review_disposition: saved.review_disposition || "normal", disposition_note: saved.disposition_note || "",
        assigned_author_name: active ? saved.author_name : "", last_labeler_name: saved.author_name || "",
        lease_expires_at: active ? saved.lease_expires_at : null, updated_at: saved.updated_at || null };
    });
    const summary = { total: tasks.length, queued: 0, in_progress: 0, completed: 0, needs_adjudication: 0,
      quarantined: 0, excluded: 0 };
    tasks.forEach((task) => { summary[task.status] = (summary[task.status] || 0) + 1;
      if (task.review_disposition !== "normal") summary[task.review_disposition] += 1; });
    return json(res, 200, { summary, tasks });
  } catch (error) { return handlerErrors(res, error); }
};
