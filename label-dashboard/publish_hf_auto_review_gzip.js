#!/usr/bin/env node
"use strict";

// Publish a compressed transport copy of the immutable auto-review sidecar.
const fs = require("node:fs");
const path = require("node:path");
const { REPO } = require("./lib/hf-store");

const token = process.env.HF_TOKEN;
const filePath = "label_review/ArgusV_november_auto_review.json.gz";
const localPath = path.join(__dirname, "november_auto_review.json.gz");

async function main() {
  if (!token) throw new Error("HF_TOKEN is missing.");
  if (!fs.existsSync(localPath)) throw new Error(`Compressed review file is missing: ${localPath}`);
  const base = "https://huggingface.co";
  const destination = await fetch(`${base}/datasets/${REPO}/resolve/main/${filePath}?download=true`, {
    method: "HEAD", headers: { authorization: `Bearer ${token}` }, cache: "no-store",
  });
  if (destination.ok) throw new Error(`Refusing to replace existing HF file: ${filePath}`);
  if (destination.status !== 404) throw new Error(`Could not verify destination (${destination.status}).`);
  const infoResponse = await fetch(`${base}/api/datasets/${REPO}`, {
    headers: { authorization: `Bearer ${token}` }, cache: "no-store",
  });
  if (!infoResponse.ok) throw new Error(`Could not read HF dataset info (${infoResponse.status}).`);
  const info = await infoResponse.json();
  const { commit } = await import("@huggingface/hub");
  const result = await commit({
    repo: { type: "dataset", name: REPO }, accessToken: token, parentCommit: info.sha,
    title: "Add compressed transport copy of November auto review sidecar",
    operations: [{ operation: "addOrUpdate", path: filePath,
      content: new Blob([fs.readFileSync(localPath)], { type: "application/gzip" }) }],
  });
  console.log(`Published compressed review sidecar: ${filePath}`);
  if (result?.commitUrl) console.log(result.commitUrl);
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
