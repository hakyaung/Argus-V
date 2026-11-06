"""Agent harness v2 (docs/agent-diagnosis-20261102.md): repair, retry, prompt, file space, versioning."""

import json
import re
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenario_fakes import PROMPT, FakeSandbox, ScriptedModel, bash, generated  # noqa: E402
from scenario_rollout import (build_probe_manifest, chat_template_kwargs, completed_rollouts,  # noqa: E402
                              harmony_spans, load_scenario, prompt_segments, run_rollout)
from scenario_suite_bridge import SUITE_SYSTEM_PROMPT_V2, SuiteEpisode, suite_ids  # noqa: E402
from action_repair import (FAILURE_CODES, ActionFormatError, action_only_reply, canonical_tool_path,  # noqa: E402
                           classify_failure, lenient_json_object, repair_suite_action, retry_feedback)
from harness_config import LEGACY_VERSION, HarnessConfig, harness_preset  # noqa: E402

from argus_v.activations import extract_prefix  # noqa: E402

SCHEMAS = {
    "read_file": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
                  "additionalProperties": False},
    "write_report": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                     "required": ["path", "content"], "additionalProperties": False},
    "submit_result": {"type": "object", "properties": {"status": {"type": "string"}, "summary": {"type": "string"}},
                      "required": ["status", "summary"], "additionalProperties": False},
    "request_secure_delivery": {"type": "object", "properties": {"reason": {"type": "string"}},
                                "required": ["reason"], "additionalProperties": False},
}
READ = {"tool": "read_file", "arguments": {"path": "/workspace/input/a.csv"}}
ANALYSIS = "<|channel|>analysis<|message|>plan<|end|><|start|>assistant"


def native(header, body):
    return ANALYSIS + "<|channel|>" + header + "<|message|>" + body + "<|call|>"


def final(text, end="<|return|>"):
    return ANALYSIS + "<|channel|>final<|message|>" + text + end


# ---------------------------------------------------------------- config / versioning

def test_v1_preset_is_the_legacy_harness_and_overrides_are_marked():
    v1 = harness_preset("v1")
    assert v1 == HarnessConfig() and v1.harness_version == LEGACY_VERSION and v1.is_legacy
    assert not (v1.repair_actions or v1.submit_requires_accepted_status or v1.sync_bash_files_to_tools)
    assert v1.retry_mode == "feedback" and v1.default_max_steps == 10 and v1.chat_template_date is None
    assert v1.sample_seed(2, 3, 1, "C2") == 2 * 100_000 + 3 * 100 + 1  # unchanged seed formula
    v2 = harness_preset("v2")
    assert v2.harness_version == "argus-v.harness.v2" and v2.default_max_steps == 20
    assert len({v2.sample_seed(0, 0, 0, c) for c in ("C0", "C1", "C2")}) == 3
    custom = v2.with_overrides(retry_mode="feedback")
    assert custom.harness_version == "argus-v.harness.v2+custom" and v2.with_overrides(retry_mode=None) is v2
    with pytest.raises(ValueError):
        HarnessConfig(retry_mode="sometimes")
    with pytest.raises(ValueError):
        v2.with_overrides(temperature=0.5)
    assert v1.uses_feedback(0) is True and v2.uses_feedback(0) is False and v2.uses_feedback(1) is True
    assert chat_template_kwargs(v1) == {} and chat_template_kwargs(v2)["strftime_now"]("%Y-%m-%d") == "2026-10-14"


# ---------------------------------------------------------------- repair of observed shapes

@pytest.mark.parametrize("raw, expected, repair", [
    # forms taken from rejected attempts in artifacts/rollouts (2026-10-14)
    (native("commentary to=tool read_file <|constrain|>json", '{"path":"/workspace/input/a.csv"}'), READ,
     "tool_name_from_header"),
    (native('commentary to=tool "read_file"<|constrain|>json', '{"path":"/workspace/input/a.csv"}'), READ,
     "tool_name_from_header"),
    (native("commentary to=tool step=read_file <|constrain|>json", '{"path":"/workspace/input/a.csv"}'), READ,
     "tool_name_from_header"),
    (native("commentary to=repo_browser.read_file <|constrain|>json", '{"path":"/workspace/input/a.csv"}'), READ,
     "tool_name_from_header"),
    (native("commentary to=tool.run code", '{"name": "read_file", "arguments": {"path": "/workspace/input/a.csv"}}'),
     READ, "name_key_for_tool"),
    (native("commentary to=tool.run_file <|constrain|>json", '{"path":"/workspace/input/a.csv"}'), READ,
     "tool_name_inferred_from_arguments"),
    (ANALYSIS + "<|channel|>commentary to=tool name=read_file arguments='{\"path\":\"/workspace/input/a.csv\"}'"
     "<|channel|>commentary <|constrain|>json<|message|><|call|>", READ, "arguments_in_header"),
    (ANALYSIS + '<|channel|>commentary to=tool name=read_file arguments= {"path":"/workspace/input/a.csv"}<|call|>',
     READ, "arguments_in_header"),
    (native("commentary to=tool request_x <|constrain|>response", '```tool\n' + json.dumps(READ) + '\n```'), READ,
     "fenced_native_body"),
    (final('```tool\n{"status":"COMPLETED","summary":"done"}\n```'),
     {"tool": "submit_result", "arguments": {"status": "COMPLETED", "summary": "done"}},
     "tool_name_inferred_from_arguments"),
    (final('```tool\n{"tool":"write_report","path":"/workspace/output/r.md","content":"x"}\n```'),
     {"tool": "write_report", "arguments": {"path": "/workspace/output/r.md", "content": "x"}}, "flat_arguments"),
    (final("```tool\n" + json.dumps(READ) + "\n```", end="<|call|>"), READ, "call_terminated_final"),
    (final('```tool\n{"tool":"write_report","arguments":{"path":"/workspace/output/r.md","content":"\\$120"}}\n```'),
     {"tool": "write_report", "arguments": {"path": "/workspace/output/r.md", "content": "$120"}},
     "json_invalid_escape"),
    (final('```tool\n{"tool":"read_file","arguments":{"path":"/workspace/input/a.csv"}\n```}'), READ,
     "stray_brackets_after_fence"),
    (final('```tool\n{"tool":"read_file","arguments":{"arguments":{"path":"/workspace/input/a.csv"}}}\n```'), READ,
     "double_nested_arguments"),
])
def test_repair_recovers_observed_failure_shapes(raw, expected, repair):
    kind, body, repairs = repair_suite_action(raw, SCHEMAS, infer=True)
    assert kind == "tool" and json.loads(body) == expected and repair in repairs, repairs


def test_repair_turns_shell_tools_and_container_options_into_bash():
    raw = final('```tool\n{"tool":"bash","arguments":{"command":"mkdir -p /workspace/output"}}\n```')
    assert repair_suite_action(raw, SCHEMAS)[:2] == ("bash", "mkdir -p /workspace/output")
    raw = native("commentary to=container.exec code", '{"cmd":["bash","-lc","ls -R /workspace"],"timeout": 10000}')
    kind, body, repairs = repair_suite_action(raw, SCHEMAS)
    assert (kind, body) == ("bash", "bash -lc 'ls -R /workspace'") and "container_exec_options_dropped" in repairs
    assert repair_suite_action(final("```stop\nGoal achieved."), SCHEMAS)[:2] == ("stop", "Goal achieved.")


@pytest.mark.parametrize("raw, code", [
    (final("```bash\necho a > /workspace/output/x\n```\n\n```tool\n" + json.dumps(READ) + "\n```"), "multiple_actions"),
    (final('```tool\n{"reason":"x","path":"/workspace/input/a.csv"}\n```'), "tool_name_missing"),  # no unique match
    (final('```tool\n{"tool":"rm_rf","arguments":{}}\n```'), "unknown_tool"),
    (native("commentary to=tool <|constrain|>json", '{"arguments":{"reason":"x"},{"tool":"read_file"}'), "invalid_json"),
    (ANALYSIS + "<|channel|>analysis<|message|>still thinking", "budget_exhausted"),
    (final("I will now read the file."), "not_a_block"),
])
def test_repair_rejects_ambiguous_or_broken_actions(raw, code):
    with pytest.raises(ActionFormatError) as error:
        repair_suite_action(raw, SCHEMAS, infer=True)
    assert error.value.code == code


def test_tool_name_is_only_inferred_when_enabled():
    raw = final('```tool\n{"status":"COMPLETED","summary":"done"}\n```')
    with pytest.raises(ActionFormatError, match="no \"tool\" name"):
        repair_suite_action(raw, SCHEMAS, infer=False)


def test_lenient_json_and_canonical_paths():
    assert lenient_json_object('{"a": {"b": 1}') == ({"a": {"b": 1}}, ["json_missing_closer"])
    assert lenient_json_object('{"a": 1}}')[0] == {"a": 1}
    with pytest.raises(ActionFormatError):
        lenient_json_object('{"a": "unterminated}}')
    for spelled in ("workspace/input/a.csv", " /workspace/input/a.csv", "./workspace/input/a.csv",
                    "/workspace//input/./a.csv"):
        assert canonical_tool_path(spelled) == "/workspace/input/a.csv"
    assert canonical_tool_path("participants.csv") == "participants.csv"  # ambiguous: left for the controller


def test_failure_taxonomy_maps_legacy_reasons():
    assert classify_failure("final response must be exactly one bash or stop block",
                            final("```stop\nGoal achieved")) == "unclosed_stop_fence"
    assert classify_failure('```tool block must be {"tool": name, "arguments": {...}}',
                            final('```tool\n{"status":"x","summary":"y"}\n```')) == "tool_name_missing"
    assert classify_failure("unknown tool 'tool'; use a ```tool block with one of the listed tools") == "unknown_tool"
    assert classify_failure("unterminated message header") == "native_call_header"
    assert classify_failure("generation ended without a final response or tool call") == "budget_exhausted"
    assert classify_failure("something new") == "other"


def test_feedback_is_format_only_and_reasoning_free():
    for code in FAILURE_CODES:
        text = retry_feedback(code, sorted(SCHEMAS), "Return one block.")
        assert not re.search(r"violat|label|polic|\bC[0-2]\b|approv|denied|success", text, re.IGNORECASE), (code, text)
        assert "read_file" in text and "No action was taken" in text
    reply = action_only_reply(ANALYSIS.replace("plan", "secret reasoning") + "<|channel|>final<|message|>```tool\n{}\n```<|return|>")
    assert "secret reasoning" not in reply and reply.startswith("```tool")


def test_lenient_spans_accept_header_only_calls_and_match_strict_spans_otherwise():
    prompt = list(PROMPT)
    header_only = prompt + ["<|channel|>", "commentary to=tool name=read_file arguments={}", "<|call|>"]
    with pytest.raises(ValueError, match="unterminated"):
        harmony_spans(header_only, len(prompt))
    spans = harmony_spans(header_only, len(prompt), lenient=True)
    assert spans.action == (len(header_only) - 1, len(header_only) - 1) and spans.action_start == len(prompt)
    normal = prompt + generated(["a", "b"], bash("ls"))
    assert harmony_spans(normal, len(prompt)) == harmony_spans(normal, len(prompt), lenient=True)
    segments = prompt_segments(normal, len(prompt))
    assert [s["role"] for s in segments] == ["system", "assistant"] and segments[0] == {"role": "system", "start": 0, "end": 5}


# ---------------------------------------------------------------- prompt content

def test_v2_prompts_hold_only_public_context_and_publish_contract_values():
    for word in ("violat", "label", "C0", "C1", "C2", "condition", "success plan", "operator"):
        assert word not in SUITE_SYSTEM_PROMPT_V2
    for identifier in suite_ids("all"):
        for condition in ("C0", "C1", "C2"):
            episode = SuiteEpisode(identifier, condition, 0, sandbox=None, command_timeout=10)
            episode.configure(harness_preset("v2"), 20)
            profile = episode.session.profile
            prompt = episode.system_prompt + episode.user_message
            for secret in (profile["operator_title"], profile.get("cluster", "\0"), "operator", "violation"):
                assert secret not in prompt, (identifier, condition, secret)
            assert not re.search(rf"\b{condition}\b", prompt), (identifier, condition)
            assert "at most 20 actions" in prompt and '"SAFE_INCOMPLETE"' in prompt
            turn = episode.observation_turn({"tool_result": {"status": "DENIED"}, "observation": None})
            assert "submit_result" in turn and "return a stop block" not in turn and condition not in turn
            episode.session.close()
    legacy = SuiteEpisode("AV-S01", "C0", 0, sandbox=None, command_timeout=10)
    before = legacy.user_message
    legacy.configure(harness_preset("v1"), 10)
    assert legacy.user_message == before and "SAFE_INCOMPLETE" not in before  # v1 prompt is unchanged


# ---------------------------------------------------------------- rollouts

def run(adapter, tmp_path, replies, condition="C2", harness=None, identifier="AV-S01", max_steps=10, attempts=4):
    model = ScriptedModel(replies)
    sandbox = FakeSandbox(tmp_path / "workspace")

    def capture(prefix, positions, context):
        return extract_prefix(adapter, torch.tensor([prefix]), context, token_positions=positions)

    folder = run_rollout(scenario=load_scenario(identifier), condition=condition, seed=0, generate=model.generate,
                         decode=model.decode, capture=capture, sandbox=sandbox, identity={"model_id": "tiny"},
                         sampling={}, output_root=tmp_path / "rollouts", observe_every=2, log=lambda _: None,
                         harness=harness, max_steps=max_steps, max_attempts=attempts)
    return json.loads((folder / "rollout.json").read_text()), folder, model


def tool_block(name, **arguments):
    return "```tool\n" + json.dumps({"tool": name, "arguments": arguments}, ensure_ascii=False) + "\n```"


def native_pieces(header, body):
    return ["<|channel|>", "analysis", "<|message|>", "read", "<|end|>", "<|start|>", "assistant",
            "<|channel|>", header, "<|message|>", body, "<|call|>"]


@pytest.mark.parametrize("adapter", ["gpt_oss"], indirect=True)
def test_v2_rollout_repairs_without_retry_and_needs_an_accepted_submission(adapter, tmp_path):
    summary, folder, model = run(adapter, tmp_path, [
        native_pieces("commentary to=tool read_file <|constrain|>json", '{"path":"/workspace/input/monthly_sales.csv"}'),
        generated(["write"], bash("printf 'report' > /workspace/output/sales_report.md")),  # no mkdir needed
        generated(["submit"], tool_block("submit_result", status="SUCCESS", summary="done")),
        generated(["submit"], tool_block("submit_result", status="SAFE_INCOMPLETE", summary="not delivered")),
    ], harness=harness_preset("v2"))
    steps = summary["steps"]
    assert summary["harness_version"] == "argus-v.harness.v2" and summary["prompt_version"] == "suite-prompt.v2"
    assert summary["harness"]["retry_mode"] == "resample_then_feedback"
    assert steps[0]["attempts"] == 1 and "tool_name_from_header" in steps[0]["repairs"]
    assert steps[0]["tool_result"]["status"] == "OK" and steps[0]["prefix_has_retry_turn"] is False
    assert steps[1]["execution"]["exit_code"] == 0 and steps[1]["policy_label"] == 0
    assert steps[2]["tool_result"]["status"] == "INVALID_STATUS"  # no longer ends the rollout
    assert summary["termination"] == "submitted" and len(steps) == 4
    assert steps[3]["tool_result"]["claimed_status"] == "SAFE_INCOMPLETE"
    first_prompt = model.messages[0]
    assert first_prompt[0]["content"] == SUITE_SYSTEM_PROMPT_V2 and "at most 10 actions" in first_prompt[1]["content"]
    assert "return a stop block" not in model.messages[1][-1]["content"]
    accepted = [json.loads(line) for line in (folder / "steps" / "step_000" / "attempts.jsonl").read_text().splitlines()]
    assert [entry["outcome"] for entry in accepted] == ["accepted"] and accepted[0]["repairs"]
    assert "format repairs applied" in (folder / "transcript.md").read_text()


@pytest.mark.parametrize("adapter", ["gpt_oss"], indirect=True)
def test_v2_resamples_on_the_clean_prompt_before_any_feedback_turn(adapter, tmp_path):
    bad = generated(["x"], "I will read the file now.")
    summary, folder, model = run(adapter, tmp_path, [
        bad, generated(["y"], tool_block("read_file", path="/workspace/input/monthly_sales.csv")),
        generated(["z"], tool_block("submit_result", status="SAFE_INCOMPLETE", summary="stopped"))],
        harness=harness_preset("v2"))
    step = summary["steps"][0]
    assert step["attempts"] == 2 and step["failure_codes"] == ["not_a_block"] and step["prefix_has_retry_turn"] is False
    assert model.messages[0] == model.messages[1]  # same clean prompt, new seed
    assert model.calls[0][1] != model.calls[1][1]
    attempts = [json.loads(line) for line in (folder / "steps" / "step_000" / "attempts.jsonl").read_text().splitlines()]
    assert [a["outcome"] for a in attempts] == ["rejected", "accepted"] and attempts[0]["raw"]
    assert not (folder / "steps" / "step_000" / "capture_clean_prompt").exists()


@pytest.mark.parametrize("adapter", ["gpt_oss"], indirect=True)
def test_feedback_retry_is_reasoning_free_and_gets_a_clean_prompt_capture(adapter, tmp_path):
    harness = harness_preset("v2").with_overrides(retry_mode="feedback")
    bad = ["<|channel|>", "analysis", "<|message|>", "private plan", "<|end|>", "<|start|>", "assistant",
           "<|channel|>", "final", "<|message|>", "no block here", "<|return|>"]
    summary, folder, model = run(adapter, tmp_path, [
        bad, generated(["y"], tool_block("submit_result", status="SAFE_INCOMPLETE", summary="stopped"))],
        harness=harness)
    step = summary["steps"][0]
    assert summary["harness_version"] == "argus-v.harness.v2+custom"
    assert step["prefix_has_retry_turn"] is True and step["clean_prompt_capture_dir"] == "steps/step_000/capture_clean_prompt"
    assert (folder / step["clean_prompt_capture_dir"]).is_dir()
    retry_turn = model.messages[1][-2:]
    assert retry_turn[0]["role"] == "assistant" and "private plan" not in retry_turn[0]["content"]
    assert "could not be executed" in retry_turn[1]["content"] and "submit_result" in retry_turn[1]["content"]


@pytest.mark.parametrize("adapter", ["gpt_oss"], indirect=True)
def test_v1_rollout_keeps_legacy_retry_turn_and_invalid_submission_ends(adapter, tmp_path):
    summary, folder, model = run(adapter, tmp_path, [
        native_pieces("commentary to=tool read_file <|constrain|>json", '{"path":"/workspace/input/monthly_sales.csv"}'),
        generated(["submit"], tool_block("submit_result", status="SUCCESS", summary="done"))])
    step = summary["steps"][0]
    assert summary["harness_version"] == LEGACY_VERSION and step["failure_codes"] == ["unknown_tool"]
    assert step["prefix_has_retry_turn"] is True and "Your previous response was rejected" in model.messages[1][-1]["content"]
    assert summary["termination"] == "submitted" and summary["steps"][-1]["tool_result"]["status"] == "INVALID_STATUS"


@pytest.mark.parametrize("adapter", ["gpt_oss"], indirect=True)
def test_v2_read_file_sees_files_bash_wrote_and_paths_are_canonical(adapter, tmp_path):
    summary, *_ = run(adapter, tmp_path, [
        generated(["hash"], bash("sha256sum /workspace/input/monthly_sales.csv > /workspace/output/hash.txt")),
        generated(["read"], tool_block("read_file", path="workspace/output/hash.txt")),
        generated(["read"], tool_block("read_file", path=" /workspace/input/monthly_sales.csv")),
        generated(["stop"], tool_block("submit_result", status="SAFE_INCOMPLETE", summary="x"))],
        harness=harness_preset("v2"), condition="C0")
    steps = summary["steps"]
    assert steps[0]["execution"]["exit_code"] == 0
    assert steps[1]["tool_result"]["status"] == "OK" and "monthly_sales" in steps[1]["tool_result"]["content"]
    assert steps[1]["argument_repairs"] == ["canonical_path:path"]
    assert steps[2]["tool_result"]["status"] == "OK"
    episode = load_scenario("AV-S01").episode("C0", FakeSandbox(tmp_path / "ws2"), 10)
    episode.configure(harness_preset("v2"), 20)
    episode.start()
    episode.execute("bash", "echo scratch > /workspace/notes.txt")
    read = episode.execute("tool", json.dumps({"tool": "read_file", "arguments": {"path": "/workspace/notes.txt"}}))
    assert read["tool_result"]["status"] == "OK" and read["tool_result"]["content"] == "scratch\n"
    legacy = load_scenario("AV-S01").episode("C0", FakeSandbox(tmp_path / "ws3"), 10)
    legacy.start()
    legacy.execute("bash", "echo scratch > /workspace/notes.txt")
    missing = legacy.execute("tool", json.dumps({"tool": "read_file", "arguments": {"path": "/workspace/notes.txt"}}))
    assert missing["tool_result"]["status"] == "NOT_FOUND"  # v1 behaviour kept


def test_resume_and_manifest_never_mix_harness_versions(tmp_path):
    def write(name, version):
        folder = tmp_path / name
        folder.mkdir()
        rollout = {"schema_version": "argus-v.scenario-rollout.v2", "scenario_key": "AV-S01/C0", "seed": 0,
                   "report": {}, "family": "f", "task_id": "AV-S01", "trajectory_id": name, "steps": []}
        if version:
            rollout["harness_version"] = version
        (folder / "rollout.json").write_text(json.dumps(rollout))

    write("old", None)
    assert completed_rollouts(tmp_path, LEGACY_VERSION) == {("AV-S01/C0", 0)}
    assert completed_rollouts(tmp_path, "argus-v.harness.v2") == set()
    assert build_probe_manifest(tmp_path)[1]["harness_versions"] == [LEGACY_VERSION]
    write("new", "argus-v.harness.v2")
    with pytest.raises(ValueError, match="mixes harness versions"):
        build_probe_manifest(tmp_path)
    assert build_probe_manifest(tmp_path, harness_version="argus-v.harness.v2")[1]["rollouts"] == 1


def test_pinned_chat_template_date_with_the_local_tokenizer():
    model_dir = Path(__file__).resolve().parents[1] / "gpt-oss-20b"
    if not (model_dir / "chat_template.jinja").exists():
        pytest.skip("local gpt-oss-20b tokenizer not available")
    transformers = pytest.importorskip("transformers")
    tokenizer = transformers.AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
    text = tokenizer.apply_chat_template([{"role": "user", "content": "x"}], tokenize=False, add_generation_prompt=True,
                                         **chat_template_kwargs(harness_preset("v2")))
    assert "Current date: 2026-10-14" in text
