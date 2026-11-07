#!/usr/bin/env node
"use strict";

// Add the criteria and generated review overlay as new HF sidecar files only.
// Refuses to replace either destination if it already exists.
const fs = require("node:fs");
const path = require("node:path");
const { REPO } = require("./lib/hf-store");

const HF_API = "https://huggingface.co";
const destinations = [
  {
    local: path.join(__dirname, "NOVEMBER_LABELING_CRITERIA.md"),
    remote: "label_review/ArgusV_november_labeling_criteria.md",
    mime: "text/markdown; charset=utf-8",
  },
  {
    local: path.join(__dirname, "november_auto_review.json"),
    remote: "label_review/ArgusV_november_auto_review.json",
    mime: "application/json",
  },
];

async function datasetInfo(token) {
  const response = await fetch(`${HF_API}/api/datasets/${REPO}`, {
    headers: { authorization: `Bearer ${token}` }, cache: "no-store",
  });
  if (!response.ok) throw new Error(`Could not read HF dataset info (${response.status}).`);
  return response.json();
}

async function assertDestinationAbsent(token, remote) {
  const url = `${HF_API}/datasets/${REPO}/resolve/main/${remote}?download=true`;
  const response = await fetch(url, { method: "HEAD", headers: { authorization: `Bearer ${token}` }, cache: "no-store" });
  if (response.status === 404) return;
  if (response.ok) throw new Error(`Refusing to replace existing HF file: ${remote}`);
  throw new Error(`Could not verify HF destination ${remote} (${response.status}).`);
}

async function main() {
  const token = process.env.HF_TOKEN;
  if (!token) throw new Error("HF_TOKEN is missing. Set a Hugging Face write token in the environment.");
  for (const file of destinations) {
    if (!fs.existsSync(file.local)) throw new Error(`Required local file is missing: ${file.local}`);
    await assertDestinationAbsent(token, file.remote);
  }
  const info = await datasetInfo(token);
  const { commit } = await import("@huggingface/hub");
  const result = await commit({
    repo: { type: "dataset", name: REPO }, accessToken: token,
    parentCommit: info.sha, title: "Add November labeling criteria and auto review overlay",
    operations: destinations.map((file) => ({
      operation: "addOrUpdate", path: file.remote,
      content: new Blob([fs.readFileSync(file.local)], { type: file.mime }),
    })),
  });
  console.log(`Published criteria: ${destinations[0].remote}`);
  console.log(`Published additive review sidecar: ${destinations[1].remote}`);
  if (result?.commitUrl) console.log(result.commitUrl);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
