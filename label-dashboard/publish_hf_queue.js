const fs = require("node:fs/promises");
const { REPO, QUEUE_PATH, SHARD_PREFIX } = require("./lib/hf-store");

async function main() {
  if (!process.env.HF_TOKEN) throw new Error("Set HF_TOKEN in the environment first.");
  const input = process.argv[2] || "label-dashboard/november_queue.jsonl";
  const tasks = (await fs.readFile(input, "utf8")).split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  if (!tasks.length) throw new Error("Queue input is empty.");
  const infoResponse = await fetch(`https://huggingface.co/api/datasets/${REPO}`, { headers: { authorization: `Bearer ${process.env.HF_TOKEN}` } });
  if (!infoResponse.ok) throw new Error(`Could not read HF dataset info (${infoResponse.status}).`);
  const info = await infoResponse.json();
  const shardSize = 50;
  const shards = [];
  const queueIndex = [];
  for (let offset = 0; offset < tasks.length; offset += shardSize) {
    const number = String(Math.floor(offset / shardSize)).padStart(3, "0");
    const path = `${SHARD_PREFIX}_${number}.json`;
    const rows = tasks.slice(offset, offset + shardSize);
    shards.push({ path, tasks: rows });
    rows.forEach((task) => queueIndex.push({ unit_id: task.unit_id, scenario_id: task.scenario_id,
      condition: task.condition, seed: task.seed, batch: task.batch, source_revision: task.source_revision,
      queue_shard: path }));
  }
  const queue = { schema_version: "argus-v.hf-team-queue.v1", source_repo: REPO,
    source_revision: info.sha, built_at: new Date().toISOString(), total: tasks.length,
    task_payload_shard_size: shardSize, tasks: queueIndex };
  const { commit } = await import("@huggingface/hub");
  const result = await commit({ repo: { type: "dataset", name: REPO }, accessToken: process.env.HF_TOKEN,
    parentCommit: info.sha, title: "Add November labeling queue sidecar",
    operations: [...shards.map((shard) => ({ operation: "addOrUpdate", path: shard.path,
      content: new Blob([JSON.stringify(shard)], { type: "application/json" }) })),
      { operation: "addOrUpdate", path: QUEUE_PATH,
        content: new Blob([JSON.stringify(queue)], { type: "application/json" }) }] });
  process.stdout.write(`Published ${tasks.length} source tasks in ${shards.length} HF queue sidecar shards\nIndex: ${QUEUE_PATH}\n${result?.commitUrl || ""}\n`);
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
