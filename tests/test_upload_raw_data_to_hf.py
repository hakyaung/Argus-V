"""scripts/upload_raw_data_to_hf.py: Parquet conversion, record join, batching and the dataset card (no network)."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

pq = pytest.importorskip("pyarrow.parquet")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import upload_raw_data_to_hf as upload  # noqa: E402


@pytest.fixture
def raw(tmp_path):
    tensors, rollouts = tmp_path / "tensor_data", tmp_path / "rollouts"
    hook = tensors / "run-20261016T000000Z-11112222" / "step_001" / "hook_inspection"
    (hook / "tensors").mkdir(parents=True)
    array = np.arange(12, dtype=">f4").reshape(3, 4)  # big-endian on disk: stored little-endian in Parquet
    np.save(hook / "tensors" / "0000000.npy", array)
    (hook / "records.jsonl").write_text(json.dumps({"record_id": 7, "module": "model.layers.11", "layer_index": 11,
                                                    "location": "output", "filename": "tensors/0000000.npy"}) + "\n")
    rollout = rollouts / "AV-S01-C2-s0-abcd1234"
    (rollout / "steps" / "step_000" / "capture").mkdir(parents=True)
    (rollout / "rollout.json").write_text('{"scenario_key": "AV-S01/C2"}')
    np.savez(rollout / "steps" / "step_000" / "capture" / "features.npz", block_0000=np.ones((2, 2), np.float32))
    (tensors / "scenario-AV-S01-C2-s0-abcd1234").mkdir()
    (tensors / "scenario-AV-S01-C2-s0-abcd1234" / "blob.bin").write_bytes(b"\xff\x00")
    old = time.time() - 3600
    for path in tmp_path.rglob("*"):
        os.utime(path, (old, old))
    return tmp_path, array


def run(tmp_path, *extra):
    return upload.main(["--tensor-data", str(tmp_path / "tensor_data"), "--rollouts", str(tmp_path / "rollouts"),
                        "--staging", str(tmp_path / "staging"), *extra])


def rows(tmp_path, source, table):
    return [row for shard in sorted((tmp_path / "staging").glob(f"*/data/{source}/{table}/*.parquet"))
            for row in pq.read_table(shard).to_pylist()]


def test_dry_run_keeps_sources_apart_and_round_trips(raw):
    tmp_path, array = raw
    assert run(tmp_path, "--dry-run", "--keep-shards") == 0
    (tensor,) = rows(tmp_path, "agent_loop", "tensors")
    assert (tensor["record_id"], tensor["module"], tensor["layer_index"]) == (7, "model.layers.11", 11)
    assert np.array_equal(np.frombuffer(tensor["data"], dtype=tensor["dtype"]).reshape(tensor["shape"]), array)
    assert rows(tmp_path, "agent_loop", "files")[0]["path"] == "step_001/hook_inspection/records.jsonl"
    (member,) = rows(tmp_path, "scenario_rollout", "tensors")
    assert (member["path"], member["array_name"], member["record_id"]) == (
        "rollout/steps/step_000/capture/features.npz", "block_0000", None)
    files = {row["path"]: row for row in rows(tmp_path, "scenario_rollout", "files")}
    assert files["rollout/rollout.json"]["text"] == '{"scenario_key": "AV-S01/C2"}'
    assert files["tensor_data/scenario-AV-S01-C2-s0-abcd1234/blob.bin"]["content"] == b"\xff\x00"
    assert files["tensor_data/scenario-AV-S01-C2-s0-abcd1234/blob.bin"]["text"] is None


def test_dry_run_frees_its_shards_by_default(raw, capsys):
    tmp_path, _ = raw
    assert run(tmp_path, "--dry-run") == 0
    assert "[convert] done: 2 units" in capsys.readouterr().out
    assert not list((tmp_path / "staging").rglob("*.parquet"))


def test_values_over_the_chunk_size_span_rows_and_reassemble(raw):
    tmp_path, _ = raw
    hook = tmp_path / "tensor_data" / "run-20261016T000000Z-11112222" / "step_001" / "hook_inspection"
    big = np.random.default_rng(0).standard_normal((3, 1024, 300)).astype(np.float32)  # ~3.5 MiB
    np.save(hook / "tensors" / "0000001.npy", big)
    blob = bytes(range(256)) * 9000  # ~2.2 MiB non-UTF-8 file
    (hook / "big.bin").write_bytes(blob)
    old = time.time() - 3600
    for path in tmp_path.rglob("*"):
        os.utime(path, (old, old))
    assert run(tmp_path, "--dry-run", "--keep-shards", "--source", "agent_loop", "--chunk-mb", "1") == 0
    chunks = sorted((row for row in rows(tmp_path, "agent_loop", "tensors")
                     if row["path"].endswith("0000001.npy")), key=lambda row: row["chunk_index"])
    assert [row["chunk_index"] for row in chunks] == [0, 1, 2, 3] and chunks[0]["chunk_count"] == 4
    assert [row["chunk_offset"] for row in chunks] == [0, 1 << 20, 2 << 20, 3 << 20]
    rebuilt = np.frombuffer(b"".join(row["data"] for row in chunks), dtype=chunks[0]["dtype"])
    assert np.array_equal(rebuilt.reshape(chunks[0]["shape"]), big) and chunks[0]["nbytes"] == big.nbytes
    pieces = sorted((row for row in rows(tmp_path, "agent_loop", "files") if row["path"].endswith("big.bin")),
                    key=lambda row: row["chunk_index"])
    assert len(pieces) == 3 and b"".join(row["content"] for row in pieces) == blob


def test_a_parquet_write_error_stops_the_run_instead_of_skipping_every_later_file(raw, monkeypatch):
    tmp_path, _ = raw

    def broken(self):
        raise ValueError("Parquet cannot store strings with size 2GB or more")

    monkeypatch.setattr(upload.ShardWriter, "flush", broken)
    with pytest.raises(ValueError, match="2GB"):
        run(tmp_path, "--dry-run", "--source", "agent_loop")


def test_recent_units_are_skipped(raw, capsys):
    tmp_path, _ = raw
    os.utime(tmp_path / "rollouts" / "AV-S01-C2-s0-abcd1234" / "rollout.json")
    run(tmp_path, "--dry-run", "--source", "scenario_rollout")
    assert "still being written" in capsys.readouterr().out
    assert not (tmp_path / "staging" / "data" / "scenario_rollout").exists()


class FakeHub:
    """In-memory dataset repo: upload_folder / create_commit / list_repo_tree / hf_hub_download."""

    def __init__(self, tmp_path):
        self.files: dict[str, bytes] = {"README.md": b"---\nlicense: cc-by-4.0\n---\n"}
        self.commits: list[str] = []
        self.sleeps: list[float] = []
        self.fail: list[Exception] = []  # raised by the next upload_folder calls
        self.downloads = tmp_path / "downloads"
        self.user = "hakuuung"  # account the fake token belongs to
        self.tokens: list = []  # token passed to each HfApi()

    def api(self, token=None):
        hub = self
        hub.tokens.append(token)

        class Api:
            def whoami(self):
                return {"name": hub.user}

            def create_repo(self, *args, **kwargs):
                pass

            def dataset_info(self, repo):
                return type("Info", (), {"private": True})()

            def list_repo_tree(self, repo, repo_type=None, recursive=False):
                return [type("F", (), {"path": path, "size": len(data)})() for path, data in hub.files.items()]

            def upload_folder(self, repo_id, repo_type, folder_path, allow_patterns, commit_message):
                if hub.fail:
                    raise hub.fail.pop(0)
                import fnmatch

                for path in Path(folder_path).rglob("*"):
                    name = path.relative_to(folder_path).as_posix()
                    if path.is_file() and any(fnmatch.fnmatch(name, pattern) for pattern in allow_patterns):
                        hub.files[name] = path.read_bytes()
                hub.commits.append(commit_message)

            def create_commit(self, repo_id, repo_type, operations, commit_message):
                for operation in operations:
                    if type(operation).__name__ == "CommitOperationDelete":
                        del hub.files[operation.path_in_repo]
                    else:
                        hub.files[operation.path_in_repo] = operation.path_or_fileobj
                hub.commits.append(commit_message)

        return Api()

    def download(self, repo, name, repo_type=None, token=None):
        from huggingface_hub.errors import EntryNotFoundError

        if name not in self.files:
            raise EntryNotFoundError(name)
        target = self.downloads / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.files[name])
        return str(target)

    def manifest(self):
        return json.loads(self.files["manifest/units.json"])

    def rows(self, source, table):
        import io

        return [row for path, data in sorted(self.files.items()) if path.startswith(f"data/{source}/{table}/")
                for row in pq.read_table(io.BytesIO(data)).to_pylist()]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    import huggingface_hub

    fake = FakeHub(tmp_path)
    monkeypatch.setattr(huggingface_hub, "HfApi", fake.api)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake.download)
    monkeypatch.setattr(upload.time, "sleep", fake.sleeps.append)
    monkeypatch.setattr(upload, "PROJECT_TOKEN_FILE", tmp_path / "no-project-token")  # never the real secret
    return fake


def test_refuses_to_upload_with_another_accounts_token(raw, hub):
    tmp_path, _ = raw
    hub.user = "someone-else"
    with pytest.raises(SystemExit, match="refusing to upload"):
        run(tmp_path, "--yes")
    assert hub.commits == []


def test_project_token_file_is_used_before_the_global_login(raw, hub, monkeypatch):
    tmp_path, _ = raw
    token_file = tmp_path / "hf_token"
    token_file.write_text("hf_project_token\n", encoding="utf-8")
    monkeypatch.setattr(upload, "PROJECT_TOKEN_FILE", token_file)
    assert run(tmp_path, "--yes") == 0
    assert hub.tokens[0] == "hf_project_token"


def test_converts_everything_first_then_uploads_once_and_finalizes(raw, hub):
    tmp_path, array = raw
    hub.files["data/scenario_rollout/tensors/AV-S01-C2-s0-abcd1234-00000.parquet"] = b"left over"
    assert run(tmp_path, "--yes") == 0
    assert len(hub.commits) == 2 and hub.commits[0].startswith("Add raw data batch")  # not one commit per unit
    assert hub.commits[1].startswith("Finalize raw data batch") and "remove 1 unreferenced" in hub.commits[1]
    assert "data/scenario_rollout/tensors/AV-S01-C2-s0-abcd1234-00000.parquet" not in hub.files
    manifest = hub.manifest()
    assert set(manifest["units"]["agent_loop"]) == {"run-20261016T000000Z-11112222"}
    assert set(manifest["units"]["scenario_rollout"]) == {"AV-S01-C2-s0-abcd1234"}
    assert all(path in hub.files and len(hub.files[path]) == shard["bytes"] for path, shard in manifest["shards"].items())
    (tensor,) = hub.rows("agent_loop", "tensors")
    assert np.array_equal(np.frombuffer(tensor["data"], dtype=tensor["dtype"]).reshape(tensor["shape"]), array)
    assert tensor["chunk_sha256"] == hashlib.sha256(tensor["data"]).hexdigest()
    readme = hub.files["README.md"].decode()
    assert "license: cc-by-4.0" in readme and "config_name: scenario_rollout_tensors" in readme
    assert "| agent_loop | 1 | 1 | 1 |" in readme
    assert not list((tmp_path / "staging").iterdir())  # the converted batch is removed after finalizing
    hub.commits.clear()
    assert run(tmp_path, "--yes") == 0  # the Hub manifest says everything is there → no commit
    assert hub.commits == []


def test_new_units_are_added_without_touching_uploaded_shards(raw, hub):
    tmp_path, _ = raw
    assert run(tmp_path, "--yes", "--source", "agent_loop") == 0
    first = dict(hub.files)
    assert run(tmp_path, "--yes") == 0
    assert set(hub.manifest()["units"]) == {"agent_loop", "scenario_rollout"}
    assert all(hub.files[path] == data for path, data in first.items() if path.startswith("data/"))


def test_interrupted_upload_resumes_the_converted_batch(raw, hub, monkeypatch):
    tmp_path, _ = raw
    hub.fail = [RuntimeError("network down")] * 3
    with pytest.raises(RuntimeError):
        run(tmp_path, "--yes")
    assert "manifest/units.json" not in hub.files  # nothing finalized
    (batch,) = (tmp_path / "staging").glob("*/batch.json")
    monkeypatch.setattr(upload, "convert_batch", lambda *a, **k: pytest.fail("must not convert again"))
    assert run(tmp_path, "--yes") == 0
    assert set(hub.manifest()["units"]["scenario_rollout"]) == {"AV-S01-C2-s0-abcd1234"}
    assert not batch.exists()


def test_rate_limit_waits_for_the_reset_and_resumes(raw, hub):
    from types import SimpleNamespace

    tmp_path, _ = raw
    limited = Exception("429 Too Many Requests")
    limited.response = SimpleNamespace(status_code=429, headers={"Retry-After": "1200"})
    hub.fail = [limited]
    assert run(tmp_path, "--yes") == 0
    assert hub.sleeps == [1200.0] and "manifest/units.json" in hub.files


def test_unreadable_source_file_stops_before_any_upload(raw, hub):
    tmp_path, _ = raw
    bad = tmp_path / "tensor_data" / "run-20261016T000000Z-11112222" / "step_001" / "hook_inspection" / "bad.npy"
    bad.write_bytes(b"not an npy file")
    old = time.time() - 3600
    os.utime(bad, (old, old))
    assert run(tmp_path, "--yes") == 1
    assert hub.commits == [] and not list((tmp_path / "staging").iterdir())


def test_changed_unit_needs_rebuild_and_rebuild_replaces_everything(raw, hub, capsys):
    tmp_path, _ = raw
    assert run(tmp_path, "--yes") == 0
    old_shards = set(hub.manifest()["shards"])
    extra = tmp_path / "rollouts" / "AV-S01-C2-s0-abcd1234" / "transcript.md"
    extra.write_text("# added later")
    old = time.time() - 3600
    os.utime(extra, (old, old))
    assert run(tmp_path, "--yes") == 0
    assert "changed since their upload" in capsys.readouterr().out
    assert run(tmp_path, "--yes", "--rebuild") == 0
    manifest = hub.manifest()
    assert manifest["units"]["scenario_rollout"]["AV-S01-C2-s0-abcd1234"]["files"] == 3
    assert not old_shards & set(hub.files)  # the old batch is gone, nothing duplicated
    paths = [row["path"] for row in hub.rows("scenario_rollout", "files")]
    assert sorted(paths) == sorted(set(paths))


def test_unit_split_over_several_shards_is_listed_in_each(tmp_path):
    index = upload.RawDataIndex(tmp_path / "td", tmp_path / "ro")
    writer = upload.ShardWriter(tmp_path, "agent_loop", "files", "b", upload.schemas()["files"], 1, 1, None)
    for unit in ("u1", "u1", "u2"):
        writer.add({"unit_id": unit, "path": "p", "bytes": 1}, 1)
    assert writer.close() == {"data/agent_loop/files/b-00000.parquet": ["u1"],
                              "data/agent_loop/files/b-00001.parquet": ["u1"],
                              "data/agent_loop/files/b-00002.parquet": ["u2"]}
    assert index.units("agent_loop") == {}


def test_a_batch_of_another_layout_is_never_resumed_or_deleted(raw, hub, capsys):
    tmp_path, _ = raw
    foreign = tmp_path / "staging" / "20261016T163804Z-c28e99"
    (foreign / "data" / "agent_loop" / "tensors").mkdir(parents=True)
    (foreign / "data" / "agent_loop" / "tensors" / "run-1.parquet").write_bytes(b"x")
    (foreign / "batch.json").write_text(json.dumps({"format": "unit-named-parquet-v1", "repo": upload.DEFAULT_REPO,
                                                    "batch": "20261016T163804Z-c28e99"}))
    assert run(tmp_path, "--yes") == 0
    assert "ignoring" in capsys.readouterr().out and (foreign / "batch.json").is_file()
    assert hub.manifest()["format"] == upload.BATCH_FORMAT
    assert not any(path.endswith("run-1.parquet") for path in hub.files)


def test_hub_manifest_of_another_layout_is_replaced_and_its_files_removed(raw, hub):
    tmp_path, _ = raw
    hub.files["manifest/units.json"] = json.dumps({"format": "unit-named-parquet-v1", "units": {
        "agent_loop": {"run-20261016T000000Z-11112222": {"signature": {}}}}}).encode()
    hub.files["data/agent_loop/tensors/run-20261016T000000Z-11112222.parquet"] = b"old layout"
    assert run(tmp_path, "--yes") == 0
    manifest = hub.manifest()
    assert manifest["format"] == upload.BATCH_FORMAT and "run-20261016T000000Z-11112222" in manifest["units"]["agent_loop"]
    assert "data/agent_loop/tensors/run-20261016T000000Z-11112222.parquet" not in hub.files
