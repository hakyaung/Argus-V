const { timingSafeEqual } = require("node:crypto");

function json(res, status, value) {
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  res.end(JSON.stringify(value));
}

async function requireAuthor(req) {
  const expected = String(process.env.DASHBOARD_ACCESS_KEY || "");
  const supplied = String(req.headers["x-team-access-key"] || "");
  if (!expected) throw Object.assign(new Error("DASHBOARD_ACCESS_KEY_NOT_CONFIGURED"), { status: 503 });
  const expectedBuffer = Buffer.from(expected);
  const suppliedBuffer = Buffer.from(supplied);
  if (expectedBuffer.length !== suppliedBuffer.length || !timingSafeEqual(expectedBuffer, suppliedBuffer)) {
    throw Object.assign(new Error("TEAM_ACCESS_KEY_INVALID"), { status: 403 });
  }
  let displayName;
  try {
    displayName = decodeURIComponent(String(req.headers["x-author-name"] || "")).trim();
  } catch {
    throw Object.assign(new Error("AUTHOR_NAME_INVALID_ENCODING"), { status: 400 });
  }
  if (!displayName || displayName.length > 80 || /[\u0000-\u001f\u007f]/.test(displayName)) {
    throw Object.assign(new Error("AUTHOR_NAME_REQUIRED"), { status: 428 });
  }
  const clientId = String(req.headers["x-reviewer-client-id"] || "");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(clientId)) {
    throw Object.assign(new Error("REVIEWER_CLIENT_ID_REQUIRED"), { status: 428 });
  }
  const tabId = String(req.headers["x-reviewer-tab-id"] || "");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(tabId)) {
    throw Object.assign(new Error("REVIEWER_TAB_ID_REQUIRED"), { status: 428 });
  }
  return { displayName, clientId, tabId };
}

function handlerErrors(res, error) {
  return json(res, error.status || 500, { error: error.message || "INTERNAL_ERROR" });
}

module.exports = { json, requireAuthor, handlerErrors };

