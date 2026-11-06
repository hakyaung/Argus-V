const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { readQueue, readTask, readState } = require("../lib/hf-store");

function parse(text) { try { return JSON.parse(text || "{}"); } catch { return { raw_text: text || "" }; } }

module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    await requireAuthor(req);
    const { unit_id } = req.body || {};
    const [queue, sidecar] = await Promise.all([readQueue(), readState()]);
    const task = await readTask(queue, unit_id);
    if (!task) return json(res, 404, { error: "HF_TASK_NOT_FOUND" });
    const files = task.payload?.files || [];
    const rollout = task.payload?.rollout || parse(files.find((file) => file.path === "rollout/rollout.json")?.text);
    const firstContext = files.find((file) => /^rollout\/steps\/step_\d+\/m1-context\.json$/.test(file.path));
    const context = parse(firstContext?.text);
    const steps = files.filter((file) => /^rollout\/steps\/step_\d+\/step\.json$/.test(file.path)).map((file) => {
      const match = file.path.match(/step_(\d+)/);
      const step = parse(file.text);
      return { step_index: Number(match?.[1] ?? step.step_index ?? 0), action_id: step.action_id || null,
        kind: step.kind || step.tool_name || step.tool || step.action?.tool || null,
        summary: [step.command, step.final_text, step.output?.action_text, step.observation, step.result, step.tool_result]
          .filter(Boolean).map((value) => typeof value === "string" ? value : JSON.stringify(value)).join("\n\n"),
        policy_label: step.policy_label ?? step.original_policy_label ?? null, evidence: step };
    }).sort((a,b)=>a.step_index-b.step_index);
    const saved = sidecar.task_state?.[unit_id] || {};
    const active = saved.status === "in_progress" && Date.parse(saved.lease_expires_at || "") > Date.now();
    const status = active ? "in_progress" : saved.status === "completed" ? "completed" : saved.status === "needs_adjudication" ? "needs_adjudication" : "queued";
    return json(res, 200, { unit_id, scenario_id: task.scenario_id, condition: task.condition, seed: task.seed,
      source_revision: task.source_revision || queue.source_revision || null, status,
      assigned_author_name: active ? saved.author_name : "", review_disposition: saved.review_disposition || "normal",
      task: rollout.task || rollout.public_context?.goal || context.task || "작업 설명 없음",
      trusted_policy: rollout.trusted_policy || (rollout.public_context?.policy || []).join("\n") || context.trusted_policy || "정책 설명 없음",
      steps, step_count: steps.length });
  } catch (error) { return handlerErrors(res, error); }
};
