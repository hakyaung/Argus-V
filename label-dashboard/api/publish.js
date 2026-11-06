const { json, requireAuthor, handlerErrors } = require("../lib/server");
const { readState, STATE_PATH, REPO } = require("../lib/hf-store");
module.exports = async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "METHOD_NOT_ALLOWED" });
  try {
    await requireAuthor(req);
    const state = await readState();
    return json(res, 200, { ok: true, repo: REPO, path: STATE_PATH,
      samples: (state.samples || []).length, already_published: true,
      message: "모든 초안과 검수 결과는 저장 즉시 HF sidecar에 기록됩니다." });
  } catch (error) { return handlerErrors(res, error); }
};
