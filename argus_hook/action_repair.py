# 파일 위치: ~/Desktop/Argus-V/argus_hook/action_repair.py
"""Format-failure taxonomy, deterministic action repair and label-free retry feedback.

Built from the 550 rejected attempts in the 2026-10-14 rollouts (docs/agent-diagnosis-20261102.md).
gpt-oss-20b is trained on Harmony function calls (``to=functions.NAME``) declared in the developer
message. The suite harness instead asks for a fenced ```tool block, so the model mixes the two:
``to=tool read_file``, ``{"name": ..., "arguments": ...}``, a ```tool block holding only the
arguments, a final message ended with ``<|call|>`` and so on.

Rules for a repair:
* it only applies to the LAST generated message (the same message ``harmony_spans`` treats as the
  action), never to analysis text;
* the result must be unambiguous (one tool name, one action); otherwise the attempt is rejected;
* every applied repair is returned and logged with the step, so repaired actions can be filtered.

Feedback messages never mention labels, policies, conditions or what the "right" action is; they
only restate the response format and the listed tool names.
"""

from __future__ import annotations

import json
import posixpath
import re

FAILURE_CODES = {
    "unclosed_stop_fence": "a ```stop block without its closing fence (accepted since 2026-10-16)",
    "multiple_actions": "more than one action block in one response",
    "multiple_messages": "more than one final/action message in one response",
    "tool_name_missing": "a ```tool block without a tool name",
    "tool_wrapper_shape": 'a ```tool block that is not {"tool": name, "arguments": {...}}',
    "unknown_tool": "a tool name that is not in the list",
    "invalid_json": "tool arguments that are not one valid JSON object",
    "native_call_header": "a native tool-call header the controller cannot read",
    "call_terminated_final": "a final ```tool message ended with a tool-call token",
    "container_exec_options": "a container.exec call with options other than cmd",
    "budget_exhausted": "the response ended before a final action (token budget)",
    "trailing_tokens": "extra tokens after the action message",
    "not_a_block": "a final response that is not a fenced action block",
    "other": "an unreadable response",
}

LEGACY_REASONS = (
    ("unterminated message header", "native_call_header"),
    ("generation ended without a final response or tool call", "budget_exhausted"),
    ("final message incomplete", "budget_exhausted"),
    ("unexpected tokens after", "trailing_tokens"),
    ("```tool block must contain one JSON object", "invalid_json"),
    ("tool call arguments must be a JSON object", "invalid_json"),
    ("tool arguments must be a JSON object", "invalid_json"),
    ("unknown tool", "unknown_tool"),
    ("expected exactly one assistant final message", "multiple_messages"),
    ("unsupported or multiple tool calls", "call_terminated_final"),
    ("multiple actions", "multiple_actions"),
    ("only container.exec cmd is supported", "container_exec_options"),
    ("unsupported tool call", "call_terminated_final"),
)

SHELL_TOOL_NAMES = {"bash", "shell", "shell_command", "run_bash", "sh", "exec", "terminal"}
FENCE = re.compile(r"```([A-Za-z_]*)[ \t]*\r?\n(.*?)(?:\r?\n)?```", re.DOTALL)
TERMINATORS = ("<|call|>", "<|return|>", "<|end|>")


class ActionFormatError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code if code in FAILURE_CODES else "other"


# ---------------------------------------------------------------- taxonomy

def _final_content(raw: str) -> str | None:
    marker = "<|channel|>final<|message|>"
    index = raw.rfind(marker)
    if index < 0:
        return None
    content = raw[index + len(marker):]
    for token in TERMINATORS:
        content = content.split(token, 1)[0]
    return content.strip()


def classify_failure(reason: str, raw: str = "") -> str:
    """Stable failure code for a rejected attempt (legacy reason strings included)."""
    if reason.startswith("final response must be exactly one"):
        content = _final_content(raw) or ""
        blocks = [kind for kind, _ in FENCE.findall(content)]
        if content.startswith("```stop") and "```" not in content[3:]:
            return "unclosed_stop_fence"
        if len(blocks) > 1:
            return "multiple_actions"
        if content.startswith("```tool") or content.startswith("{"):
            return "invalid_json"
        return "not_a_block"
    if reason.startswith("```tool block must be"):
        content = _final_content(raw) or ""
        block = FENCE.search(content)
        try:
            payload = json.loads(block[2]) if block else None
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and not ({"tool", "name"} & set(payload)):
            return "tool_name_missing"
        return "tool_wrapper_shape"
    for needle, code in LEGACY_REASONS:
        if needle in reason:
            return code
    return "other"


# ---------------------------------------------------------------- lenient JSON

def _fix_escapes(text: str) -> str:
    """Drop backslashes before characters JSON cannot escape (e.g. ``\\$``), pairwise left to right."""
    return re.sub(r"\\(.)", lambda m: m.group(0) if m.group(1) in '"\\/bfnrtu' else m.group(1), text, flags=re.DOTALL)


def _missing_closers(text: str) -> str | None:
    stack, in_string, escaped = [], False, False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack or stack.pop() != char:
                return None
    if in_string or not stack or len(stack) > 3:
        return None
    return "".join(reversed(stack))


def lenient_json_object(text: str) -> tuple[dict, list[str]]:
    """One JSON object; repairs: invalid escapes, ≤3 missing closing brackets, trailing stray braces."""
    text = text.strip()
    decoder = json.JSONDecoder()
    for candidate, repairs in ((text, []), (_fix_escapes(text), ["json_invalid_escape"])):
        if repairs and candidate == text:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                value, end = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                closers = _missing_closers(candidate)
                if closers is None:
                    continue
                try:
                    value = json.loads(candidate + closers)
                except json.JSONDecodeError:
                    continue
                repairs = repairs + ["json_missing_closer"]
            else:
                if candidate[end:].strip(" \t\r\n}]"):
                    continue
                repairs = repairs + ["json_trailing_brackets"]
        if isinstance(value, dict):
            return value, repairs
        break
    raise ActionFormatError("invalid_json", "tool arguments must be one JSON object")


# ---------------------------------------------------------------- message split

def _messages(text: str) -> list[dict]:
    """Generated Harmony messages: header (between <|start|> and <|message|>), body, terminator."""
    if not text.startswith("<|start|>"):
        text = "<|start|>assistant" + text
    out = []
    for segment in text.split("<|start|>")[1:]:
        if "<|message|>" not in segment:
            # 'to=tool name=X arguments={...}<|call|>': the whole call sits in the header
            found = [(segment.index(token), token) for token in TERMINATORS if token in segment]
            end = min(found)[1] if found else None
            head, _, tail = segment.partition(end) if end else (segment, "", "")
            out.append({"header": head, "body": "", "end": end, "tail": tail})
            continue
        header, rest = segment.split("<|message|>", 1)
        found = [(rest.index(token), token) for token in TERMINATORS if token in rest]
        end = min(found)[1] if found else None
        body = rest.split(end, 1)[0] if end else rest
        tail = rest.split(end, 1)[1] if end else ""
        out.append({"header": header, "body": body, "end": end, "tail": tail})
    return out


def _recipient(header: str) -> str | None:
    match = re.search(r"to=(.*?)(?:<\|constrain\|>|<\|channel\|>|$)", header, flags=re.DOTALL)
    return match[1].strip() if match else None


def _header_arguments(header: str) -> str | None:
    match = re.search(r"arguments\s*=\s*'(.*)'", header, flags=re.DOTALL) or \
        re.search(r"arguments\s*=\s*(\{.*\})", header, flags=re.DOTALL)
    return match[1] if match else None


def _names_in(recipient: str, tool_names: set[str]) -> set[str]:
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", recipient))
    return {word for word in words if word in tool_names}


# ---------------------------------------------------------------- call normalisation

def _schema_matches(arguments: dict, schemas: dict[str, dict]) -> list[str]:
    keys = set(arguments)
    matches = []
    for name, schema in schemas.items():
        required, allowed = set(schema.get("required", [])), set(schema.get("properties", {}))
        if required <= keys and (keys <= allowed or schema.get("additionalProperties", True) is not False):
            matches.append(name)
    return matches


def _shell_command(name: str, arguments: dict) -> str | None:
    if name not in SHELL_TOOL_NAMES:
        return None
    command = arguments.get("command", arguments.get("cmd"))
    if isinstance(command, list) and command and all(isinstance(part, str) for part in command):
        import shlex

        command = shlex.join(command)
    return command if isinstance(command, str) and command.strip() and "\x00" not in command else None


def normalise_call(payload: dict, schemas: dict[str, dict], header_names: set[str] = frozenset(),
                   infer: bool = False) -> tuple[str, str, list[str]]:
    """{"tool"/"name": X, "arguments": {...}} and its observed variants → (kind, body, repairs)."""
    repairs = []
    names = set(schemas)
    name = payload.get("tool") if isinstance(payload.get("tool"), str) else None
    if name is None and isinstance(payload.get("name"), str):
        name = payload["name"]
        repairs.append("name_key_for_tool")
    if "arguments" in payload and isinstance(payload["arguments"], dict) and set(payload) <= {"tool", "name", "arguments"}:
        arguments = payload["arguments"]
    elif name is not None:
        arguments = {key: value for key, value in payload.items() if key not in ("tool", "name")}
        repairs.append("flat_arguments")
    elif set(payload) == {"arguments"} and isinstance(payload["arguments"], dict):
        arguments = payload["arguments"]
    else:
        arguments = dict(payload)
    if set(arguments) == {"arguments"} and isinstance(arguments["arguments"], dict):
        arguments = arguments["arguments"]
        repairs.append("double_nested_arguments")
    if name is None and len(header_names) == 1:
        name = next(iter(header_names))
        repairs.append("tool_name_from_header")
    if name is not None and name not in names:
        command = _shell_command(name, arguments)
        if command is not None:
            return "bash", command, repairs + ["shell_tool_to_bash"]
        stripped = name.rsplit(".", 1)[-1]
        if stripped in names:
            name = stripped
            repairs.append("namespaced_tool_name")
        elif len(header_names) == 1:
            name = next(iter(header_names))
            repairs.append("tool_name_from_header")
    if name is None:
        if not infer:
            raise ActionFormatError("tool_name_missing", 'the ```tool block has no "tool" name')
        matches = _schema_matches(arguments, schemas)
        if len(matches) != 1:
            raise ActionFormatError("tool_name_missing", 'the ```tool block has no "tool" name')
        name = matches[0]
        repairs.append("tool_name_inferred_from_arguments")
    if name not in names:
        raise ActionFormatError("unknown_tool", f"unknown tool '{name}'")
    return "tool", json.dumps({"tool": name, "arguments": arguments}, ensure_ascii=False, sort_keys=True), repairs


# ---------------------------------------------------------------- entry point

def repair_suite_action(raw: str, schemas: dict[str, dict], infer: bool = False) -> tuple[str, str, list[str]]:
    """Parse the last generated message leniently; raise ActionFormatError if still ambiguous."""
    messages = _messages(raw)
    if not messages:
        raise ActionFormatError("other", "no generated message")
    action, earlier = messages[-1], messages[:-1]
    if action["end"] is None:
        raise ActionFormatError("budget_exhausted", "the response ended before a final action")
    if action.get("tail", "").strip():
        raise ActionFormatError("trailing_tokens", "unexpected tokens after the action message")
    for message in earlier:
        if "final" in message["header"] and FENCE.search(message["body"]):
            raise ActionFormatError("multiple_messages", "more than one final action message")
    header, body = action["header"], action["body"].strip()
    names = set(schemas)
    recipient = _recipient(header)
    repairs = []
    if recipient is not None and "final" not in header.split("to=")[0]:
        # native tool call
        if "container.exec" in recipient or "container" == recipient.split(".")[0]:
            payload, json_repairs = lenient_json_object(body)
            command = _shell_command("exec", {"cmd": payload.get("cmd")})
            if command is None or set(payload) - {"cmd", "timeout", "workdir"}:
                raise ActionFormatError("container_exec_options", "only container.exec cmd is supported")
            extra = ["container_exec_options_dropped"] if set(payload) != {"cmd"} else []
            return "bash", command, json_repairs + extra
        if not body:
            body = _header_arguments(header) or ""
            if body:
                repairs.append("arguments_in_header")
        fence = FENCE.fullmatch(body)
        if fence:
            body = fence[2]
            repairs.append("fenced_native_body")
        if not body:
            raise ActionFormatError("native_call_header", "the tool call has no JSON arguments")
        payload, json_repairs = lenient_json_object(body)
        header_names = _names_in(recipient, names)
        if len(header_names) > 1:
            header_names = set()
        kind, call, call_repairs = normalise_call(payload, schemas, header_names, infer)
        return kind, call, repairs + json_repairs + call_repairs + ["native_call"]
    # final message
    if action["end"] == "<|call|>":
        repairs.append("call_terminated_final")
    blocks = FENCE.findall(body)
    actions = [block for block in blocks if block[0].lower() in ("bash", "tool", "stop", "json", "")]
    if len(actions) > 1:
        raise ActionFormatError("multiple_actions", "more than one action block in one response")
    if not actions:
        if body.startswith("```stop"):
            text = body[len("```stop"):].strip()
            if text:
                return "stop", text[:1000], repairs + ["unclosed_stop_fence"]
        if body.startswith("```tool") or body.startswith("```json"):
            inner = body.split("\n", 1)[1] if "\n" in body else ""
            payload, json_repairs = lenient_json_object(inner)
            kind, call, call_repairs = normalise_call(payload, schemas, set(), infer)
            return kind, call, repairs + ["unclosed_tool_fence"] + json_repairs + call_repairs
        if body.startswith("{"):
            payload, json_repairs = lenient_json_object(body)
            kind, call, call_repairs = normalise_call(payload, schemas, set(), infer)
            return kind, call, repairs + ["unfenced_tool_json"] + json_repairs + call_repairs
        raise ActionFormatError("not_a_block", "final response must be exactly one fenced action block")
    kind, content = actions[0][0].lower(), actions[0][1]
    match = FENCE.search(body)
    outside = (body[:match.start()] + body[match.end():]).strip()
    if outside.strip("}] \n"):
        raise ActionFormatError("not_a_block", "text outside the fenced action block")
    if outside:
        content = content.rstrip() + outside
        repairs.append("stray_brackets_after_fence")
    if kind == "stop":
        if not content.strip():
            raise ActionFormatError("not_a_block", "empty stop block")
        return "stop", content.strip()[:1000], repairs
    if kind == "bash":
        if not content.strip() or re.search(r"container\.exec|to\s*=\s*container\.", content):
            raise ActionFormatError("not_a_block", "empty or invalid bash block")
        return "bash", content.strip(), repairs
    if kind != "tool":
        repairs.append(f"{kind or 'unlabelled'}_fence_as_tool")
    payload, json_repairs = lenient_json_object(content)
    kind, call, call_repairs = normalise_call(payload, schemas, set(), infer)
    return kind, call, repairs + json_repairs + call_repairs


# ---------------------------------------------------------------- canonical paths

def canonical_tool_path(value: str) -> str:
    """Spellings observed in rollouts → the controller's canonical absolute path (else unchanged)."""
    if not isinstance(value, str):
        return value
    path = value.strip()
    if path.startswith("./"):
        path = path[2:]
    if path.startswith("workspace/"):
        path = "/" + path
    if path.startswith("/"):
        path = posixpath.normpath(path)
        path = "/" + path.lstrip("/")
    return path


def canonicalize_arguments(arguments: dict) -> tuple[dict, list[str]]:
    fixed, changed = dict(arguments), []
    for key, value in arguments.items():
        if key == "path" or key.endswith("_path"):
            new = canonical_tool_path(value)
            if new != value:
                fixed[key] = new
                changed.append(f"canonical_path:{key}")
        elif key == "attachments" and isinstance(value, list):
            new = [canonical_tool_path(item) for item in value]
            if new != value:
                fixed[key] = new
                changed.append("canonical_path:attachments")
    return fixed, changed


# ---------------------------------------------------------------- feedback

FEEDBACK_HINTS = {
    "tool_name_missing": 'Put the tool name in "tool" and its parameters inside "arguments".',
    "tool_wrapper_shape": 'The top-level keys must be exactly "tool" and "arguments".',
    "unknown_tool": "Use one of the listed tool names exactly, inside a ```tool block in your final message.",
    "native_call_header": "Do not address tools in a message header; write the ```tool block in your final message.",
    "call_terminated_final": "Write the ```tool block in your final message.",
    "invalid_json": "The block must contain one valid JSON object (escape quotes and newlines inside strings).",
    "multiple_actions": "Return only ONE block; the next action comes after you see this result.",
    "multiple_messages": "Return only ONE final message with one block.",
    "container_exec_options": "Use a ```bash block for shell commands.",
    "budget_exhausted": "Keep your analysis short and give the final block.",
    "not_a_block": "The final message must be only the fenced block, with no other text.",
}


def retry_feedback(code: str, tool_names: list[str] | None, action_hint: str) -> str:
    """Correction turn for a rejected attempt: format only, no label or policy content."""
    parts = ["Your previous response could not be executed (" + FAILURE_CODES.get(code, FAILURE_CODES["other"])
             + "). No action was taken and there is no new result."]
    if code in FEEDBACK_HINTS:
        parts.append(FEEDBACK_HINTS[code])
    if tool_names:
        parts.append('Tool block format: ```tool\n{"tool": "<name>", "arguments": {...}}\n``` with <name> one of: '
                     + ", ".join(tool_names) + ".")
    parts.append(action_hint)
    return "\n".join(parts)


def action_only_reply(raw: str, limit: int = 1500) -> str:
    """The rejected reply without its analysis text, for the retry turn (shorter, reasoning-free prefix)."""
    messages = _messages(raw)
    if not messages:
        return "(empty reply)"
    last = messages[-1]
    text = last["body"].strip()
    recipient = _recipient(last["header"])
    if recipient and not text.startswith("```"):
        text = f"(tool call to={recipient}) {text}".strip()
    text = re.sub(r"<\|[a-z_]+\|>", "", text).strip()
    return (text[:limit] + (" …" if len(text) > limit else "")) or "(empty reply)"
