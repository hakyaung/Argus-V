"""E-09: (scenario JSON, O, L, P, W) -> engine profile. Deterministic; one JSON source per scenario."""
import csv
import io
import json
import re
from copy import deepcopy

CELLS = {
    "pilotD": {"stage": "pilot", "O": "D", "L": "L2", "P": "base", "W": "base", "seeds": 6},
    "pilotX": {"stage": "pilot", "O": "X", "L": "L2", "P": "base", "W": "base", "seeds": 2},
    "cell1": {"stage": "matrix", "O": "B", "L": "L1", "P": "base", "W": "base", "seeds": 2},
    "cell2": {"stage": "matrix", "O": "G", "L": "L1", "P": "base", "W": "base", "seeds": 2},
    "cell3": {"stage": "matrix", "O": "D", "L": "L1", "P": "base", "W": "base", "seeds": 2},
    "cell4": {"stage": "matrix", "O": "D", "L": "L3", "P": "base", "W": "base", "seeds": 2},
    "cell5": {"stage": "matrix", "O": "X", "L": "L3", "P": "base", "W": "base", "seeds": 2},
    "cell6": {"stage": "matrix", "O": "D", "L": "L2", "P": "variant", "W": "variant", "seeds": 2},
}
ENGINE_CONDITION = {"B": "C0", "G": "C1", "D": "C2", "X": "C2"}
ENGINE_KIND = {"read": "read", "write": "write", "observe": "observe", "request": "request",
               "effect": "effect", "hold": "effect", "submit": "submit"}
TARGET_STEP = {"early": [0, 2], "mid": [2, 5], "late": [5, 9]}
MAX_STEPS = {"early": 12, "mid": 12, "late": 14}
ORDER_O = ("B", "G", "D", "X")


def base_axes(spec):
    return spec["decision_point"]["placement"], spec["collection"]["length_band"]


def variant_axes(spec):
    """(P, W) of matrix cell 6; P equals the base placement when the scenario has no placement variant."""
    base_p, base_w = base_axes(spec)
    for entry in spec["collection"].get("matrix", []):
        if entry.get("cell") == 6:
            return entry["P"], entry["W"]
    return base_p, base_w


def has_variant(spec):
    return variant_axes(spec)[0] != base_axes(spec)[0]


def cells(spec):
    """The 8 rollout cells of a scenario with concrete axes."""
    base_p, base_w = base_axes(spec)
    var_p, var_w = variant_axes(spec)
    if var_p == base_p:
        var_w = base_w  # TASK §5.3: without a variant cell 6 is D-L2-base
    result = []
    for name, cell in CELLS.items():
        p = var_p if cell["P"] == "variant" else base_p
        w = var_w if cell["W"] == "variant" else base_w
        result.append({"cell": name, "stage": cell["stage"], "O": cell["O"], "L": cell["L"], "P": p, "W": w,
                       "seeds": cell["seeds"], "max_steps": MAX_STEPS[p]})
    return result


def resolve_cell(spec, cell):
    if isinstance(cell, dict):
        axes = dict(cell)
        axes.setdefault("cell", "custom")
        axes.setdefault("max_steps", MAX_STEPS[axes["P"]])
        return axes
    for entry in cells(spec):
        if entry["cell"] == cell:
            return entry
    raise ValueError(f"unknown cell {cell}")


def _parse_field_path(field):
    if field.startswith("/"):
        return [int(part) if part.isdigit() else part for part in field[1:].split("/")]
    parts = []
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", field):
        parts.append(int(token[1:-1]) if token.startswith("[") else token)
    return parts


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def apply_twin_delta(content, delta, path):
    """Set one field of a JSON (compact form) or CSV file; returns the new content."""
    parts = _parse_field_path(delta["field"])
    if path.endswith(".json"):
        data = json.loads(content)
        node = data
        for part in parts[:-1]:
            node = node[part]
        if node[parts[-1]] != delta["from"]:
            raise ValueError(f"twin_delta 'from' does not match {path}:{delta['field']}")
        node[parts[-1]] = delta["to"]
        return compact_json(data)
    if path.endswith(".csv"):
        if len(parts) != 3 or parts[0] != "rows":
            raise ValueError("CSV twin_delta field must be rows[i].column")
        lines = content.split("\n")
        header = lines[0].split(",")
        row = lines[1 + parts[1]].split(",")
        column = header.index(parts[2])
        if row[column] != str(delta["from"]):
            raise ValueError(f"twin_delta 'from' does not match {path}:{delta['field']}")
        row[column] = str(delta["to"])
        lines[1 + parts[1]] = ",".join(row)
        return "\n".join(lines)
    raise ValueError("twin_delta file must be .json or .csv")


def twin_value(content, delta, path):
    parts = _parse_field_path(delta["field"])
    if path.endswith(".json"):
        node = json.loads(content)
        for part in parts:
            node = node[part]
        return node
    rows = list(csv.DictReader(io.StringIO(content)))
    return rows[parts[1]][parts[2]]


def _filter_enums(schema, missing):
    """Drop enum values that name input files absent from this placement instance."""
    if isinstance(schema, dict):
        result = {}
        for key, value in schema.items():
            if key == "enum" and isinstance(value, list):
                result[key] = [item for item in value if item not in missing]
            else:
                result[key] = _filter_enums(value, missing)
        return result
    if isinstance(schema, list):
        return [_filter_enums(item, missing) for item in schema]
    return schema


def files_for(spec, axes):
    """Public files of a cell: placement_only, L0 override, X twin_delta, length-band padding."""
    overrides = spec["conditions"].get("pressure_file_overrides", {}).get("L0", {}) if axes["L"] == "L0" else {}
    delta = spec["conditions"]["X"].get("twin_delta") if axes["O"] == "X" else None
    files, missing = {}, set()
    for item in spec["public"]["files"]:
        if item.get("placement_only") and axes["P"] not in item["placement_only"]:
            missing.add(item["path"])
            continue
        content = overrides.get(item["path"], item["content"])
        if delta and delta["file"] == item["path"]:
            content = apply_twin_delta(content, delta, item["path"])
        content += item.get("length_band_padding", {}).get(axes["W"], "")
        files[item["path"]] = content
    return files, missing


def variant_block(spec, placement):
    if placement == spec["decision_point"]["placement"]:
        return None
    variants = spec["decision_point"].get("placement_variants", {})
    if placement not in variants:
        raise ValueError(f"{spec['id']}: no placement variant '{placement}'")
    return variants[placement]


def tools_for(spec, axes):
    variant = variant_block(spec, axes["P"])
    tools = list(spec["tools"]) + (list(variant.get("extra_tools", [])) if variant else [])
    return tools


def plan_for(spec, placement, outcome):
    variant = variant_block(spec, placement)
    if variant is not None:
        plans = variant.get("plans", {})
        if outcome not in plans:
            raise ValueError(f"{spec['id']}: placement variant '{placement}' has no plan for {outcome}")
        return deepcopy(plans[outcome])
    return deepcopy(spec["plans"][outcome])


def dp_for(spec, placement):
    point = spec["decision_point"]
    variant = variant_block(spec, placement)
    if variant is None:
        return {"id": point["id"], "placement": placement, "prelude_min_actions": point["prelude_min_actions"],
                "target_step": list(point["target_step"])}
    return {"id": point["id"], "placement": placement, "prelude_min_actions": variant["prelude_min_actions"],
            "target_step": list(variant["target_step"])}


def expectations_for(spec, placement):
    expectations = deepcopy(spec["validation_expectations"])
    variant = variant_block(spec, placement)
    if variant is not None:
        expectations.update(deepcopy(variant.get("validation_expectations", {})))
    return expectations


def operation(tool, missing):
    kind = tool["kind"]
    schema = _filter_enums(deepcopy(tool["parameters"]), missing)
    op = {"kind": ENGINE_KIND[kind], "v2_kind": kind, "schema": schema, "description": tool["description"],
          "result_statuses": list(tool["result_statuses"]), "preconditions": list(tool.get("preconditions", [])),
          "result_fields": deepcopy(tool.get("result_fields", {})),
          "policy_checks": deepcopy(tool.get("policy_checks", [])),
          "business_checks": deepcopy(tool.get("business_checks", [])),
          "condition_behavior": deepcopy(tool.get("condition_behavior", {})),
          "updates": deepcopy(tool.get("updates", {})), "once": False}
    # Engine-compatible mirrors (TASK P0-3); SessionV2 evaluates the v2 fields above.
    op["checks"] = deepcopy(op["policy_checks"]) + [dict(check, failure_status="NOT_READY", failure_label=0)
                                                     for check in op["business_checks"]]
    if "NOT_READY" in op["result_fields"]:
        op["not_ready"] = deepcopy(op["result_fields"]["NOT_READY"])
    for key in ("authorization", "grant", "receipt"):
        if key in tool:
            op[key] = deepcopy(tool[key])
    if kind == "request":
        op["scope"] = tool.get("grant", {}).get("scope", tool["name"])
        op["bind_paths"] = list(tool.get("grant", {}).get("bind_paths", []))
        op["grant_state"] = deepcopy(tool.get("grant", {}).get("grant_state", {}))
    if kind == "write":
        values = schema["properties"].get("path", {}).get("enum", [])
        op["allowed_paths"] = [value for value in values if value.startswith("/workspace/output/")]
    return op


def materialize(spec, cell):
    """Return (profile, engine_condition, meta) for one cell (name or {O, L, P, W} dict)."""
    axes = resolve_cell(spec, cell)
    outcome = axes["O"]
    variant = variant_block(spec, axes["P"])
    files, missing = files_for(spec, axes)
    state = deepcopy(spec.get("initial_state", {}))
    state.update(deepcopy(spec["conditions"][outcome].get("state_overrides", {})))
    tools = {}
    for tool in tools_for(spec, axes):
        tools[tool["name"]] = operation(tool, missing)
    engine_condition = ENGINE_CONDITION[outcome]
    plan = plan_for(spec, axes["P"], outcome)
    point = dp_for(spec, axes["P"])
    meta = {"spec_id": spec["id"], "family_id": spec["family_id"], "domain": spec["domain"],
            "cell": axes["cell"], "stage": axes.get("stage"), "O": outcome, "L": axes["L"], "P": axes["P"],
            "W": axes["W"], "seeds": axes.get("seeds"), "max_steps": axes["max_steps"], "dp_id": point["id"],
            "prelude_min_actions": point["prelude_min_actions"], "target_step": point["target_step"],
            "approval_outcome": spec["conditions"][outcome]["approval_outcome"],
            "expected_outcome": spec["conditions"][outcome].get("expected_outcome"),
            "variant": variant is not None}
    profile = {
        "id": spec["id"], "goal": (variant or {}).get("goal", spec["public"]["goal"]),
        "public_policy": list(spec["public"]["trusted_policy"]), "files": files, "initial_state": state,
        "tools": tools, "plans": {engine_condition: plan}, "cluster": spec["family_id"],
        "operator_title": spec["title_operator"], "success": deepcopy(spec["success"]),
        "conditions": deepcopy({key: spec["conditions"][key] for key in ORDER_O}),
        "label_contract": deepcopy(spec["label_contract"]),
        "forbidden_set": deepcopy(spec["forbidden_set"]),
        "pressure_injections": deepcopy(spec["conditions"].get("pressure_injections", {})),
        "v2": meta,
    }
    return profile, engine_condition, meta
