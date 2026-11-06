const { json } = require("../lib/server");
module.exports = function handler(_req, res) {
  json(res, 200, { ready: Boolean(process.env.HF_TOKEN && process.env.DASHBOARD_ACCESS_KEY),
    hfPublishReady: Boolean(process.env.HF_TOKEN), accessProtectionReady: Boolean(process.env.DASHBOARD_ACCESS_KEY) });
};
