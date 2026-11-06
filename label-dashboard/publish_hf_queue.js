const fs = require("node:fs");
const readline = require("node:readline");
const { REPO, QUEUE_PATH, SHARD_PREFIX } = require("./lib/hf-store");

const HF_API = "https://huggingface.co";
const TASKS_PER_SHARD = 50;
const SHARDS_PER_COMMIT = 5;

async function datasetInfo(token) {
  const response = await fetch(`${HF_API}/api/datasets/${REPO}`, {
    headers: { authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Could not read HF dataset info (${response.status}).`);
  return response.json();
}

async function publishOperations(token, operations, title) {
  if (!operations.length) return;
  const info = await datasetInfo(token);
  const { commit } = await import("@huggingface/hub");
  return commit({
    repo: { type: "dataset", name: REPO },
    accessToken: token,
    parentCommit: info.sha,
    title,
    operations,
  });
}

async function main() {
  const token = process.env.HF_TOKEN;
  if (!token) throw new Error("Set HF_TOKEN in the environment first.");
  const input = process.argv[2] || "label-dashboard/november_queue.jsonl";
  const sourceInfo = await datasetInfo(token);
  const inputStream = fs.createReadStream(input, { encoding: "utf8" });
  const lines = readline.createInterface({ input: inputStream, crlfDelay: Infinity });

  const indexTasks = [];
  let shardTasks = [];
  let shardOperations = [];
  let shardStart = 0;
  let total = 0;
  let publishedShards = 0;

  async function queueShard() {
    if (!shardTasks.length) return;
    const number = String(publishedShards).padStart(3, "0");
    const path = `${SHARD_PREFIX}_${number}.json`;
    const shard = { tasks: shardTasks };
    shardOperations.push({
      operation: "addOrUpdate",
      path,
      content: new Blob([JSON.stringify(shard)], { type: "application/json" }),
    });
    publishedShards += 1;
    shardTasks = [];

    if (shardOperations.length === SHARDS_PER_COMMIT) {
      const end = publishedShards - 1;
      await publishOperations(token, shardOperations, `Add November labeling queue shards ${String(shardStart).padStart(3, "0")}-${String(end).padStart(3, "0")}`);
      console.log(`Published queue shards ${String(shardStart).padStart(3, "0")}-${String(end).padStart(3, "0")}`);
      shardStart = publishedShards;
      shardOperations = [];
    }
  }

  for await (const line of lines) {
    if (!line.trim()) continue;
    const task = JSON.parse(line);
    shardTasks.push(task);
    indexTasks.push({
      unit_id: task.unit_id,
      scenario_id: task.scenario_id,
      condition: task.condition,
      seed: task.seed,
      batch: task.batch,
      source_revision: task.source_revision,
      queue_shard: `${SHARD_PREFIX}_${String(publishedShards).padStart(3, "0")}.json`,
    });
    total += 1;
    if (shardTasks.length === TASKS_PER_SHARD) await queueShard();
  }

  await queueShard();
  if (shardOperations.length) {
    const end = publishedShards - 1;
    await publishOperations(token, shardOperations, `Add November labeling queue shards ${String(shardStart).padStart(3, "0")}-${String(end).padStart(3, "0")}`);
    console.log(`Published queue shards ${String(shardStart).padStart(3, "0")}-${String(end).padStart(3, "0")}`);
  }
  if (!total) throw new Error("Queue input is empty.");

  const queue = {
    schema_version: "argus-v.hf-team-queue.v1",
    source_repo: REPO,
    source_revision: sourceInfo.sha,
    built_at: new Date().toISOString(),
    total,
    task_payload_shard_size: TASKS_PER_SHARD,
    tasks: indexTasks,
  };
  const result = await publishOperations(token, [{
    operation: "addOrUpdate",
    path: QUEUE_PATH,
    content: new Blob([JSON.stringify(queue)], { type: "application/json" }),
  }], "Publish November labeling queue index");

  console.log(`Published ${total} source tasks in ${publishedShards} HF queue sidecar shards`);
  console.log(`Index: ${QUEUE_PATH}`);
  if (result?.commitUrl) console.log(result.commitUrl);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
