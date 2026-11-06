const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { readQueue, readTask, mutateState } = require("../lib/hf-store");
const { randomUUID } = require("node:crypto");

module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    const user = await requireAuthor(req);
    const { unit_id } = req.body || {};
    const queue = await readQueue();
    const result = await mutateState(async (state) => {
      state.task_state ||= {};
      const now = Date.now();
      const candidates = unit_id ? queue.tasks.filter((task) => task.unit_id === unit_id) : queue.tasks;
      const source = candidates.find((task) => {
        const current = state.task_state[task.unit_id] || {};
        const expired = current.status === "in_progress" && Date.parse(current.lease_expires_at || "") <= now;
        return (current.status == null || current.status === "queued" || current.status === "needs_adjudication" || expired)
          && (current.review_disposition || "normal") === "normal";
      });
      if (!source) return { noCommit: true, value: { task: null } };
      const hydrated = await readTask(queue, source.unit_id);
      if (!hydrated) throw Object.assign(new Error("HF_QUEUE_TASK_PAYLOAD_MISSING"), { status: 503 });
      const current = state.task_state[source.unit_id] || {};
      const lease_token = randomUUID();
      const lease_expires_at = new Date(now + 25 * 60_000).toISOString();
      state.task_state[source.unit_id] = { ...current, status: "in_progress", lease_token, lease_expires_at,
        author_name: user.displayName, updated_at: new Date(now).toISOString() };
      return { value: { unit_id: source.unit_id, lease_token, labels: current.labels || [], trajectory_label: current.trajectory_label || null,
        task: { ...hydrated, lease_token } } };
    });
    if (!result?.unit_id) return json(res, 200, { task: null });
    return json(res, 200, result);
  } catch (error) { return handlerErrors(res, error); }
};
