"""TASK §5.5 diversity checks D-1..D-6 over the v2 registry."""
import re
import unicodedata

from .registry import FAMILIES, load_all
from .replay import _leaf_values

COMMON_TOOLS = {"read_file", "write_output", "submit_result", "notify_requester", "save_draft", "schedule_retry"}
EMAIL = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
DOMAIN = re.compile(r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:example|com|net|org|io|kr|co|test|internal|local|corp|lan)\b",
                    re.IGNORECASE)
OBJECT_ID = re.compile(r"[A-Za-z]{2,}-[A-Za-z0-9]{3,}")


def public_text(spec):
    """goal + policy + file contents (padding excluded) + tool descriptions, all placements."""
    parts = [spec["public"]["goal"], *spec["public"]["trusted_policy"]]
    parts += [item["content"] for item in spec["public"]["files"]]
    tools = list(spec["tools"])
    for variant in spec["decision_point"].get("placement_variants", {}).values():
        if variant.get("goal"):
            parts.append(variant["goal"])
        tools += variant.get("extra_tools", [])
    parts += [tool["description"] for tool in tools]
    return "\n".join(parts)


def words(text):
    text = unicodedata.normalize("NFKC", text).lower()
    return [token for token in re.split(r"[^\w]+", text) if token]


def shingles(text, size=5):
    tokens = words(text)
    return {tuple(tokens[index:index + size]) for index in range(max(0, len(tokens) - size + 1))}


def jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def extracted_entities(text):
    found = set(EMAIL.findall(text)) | {match.group(0) for match in DOMAIN.finditer(text)}
    found |= set(OBJECT_ID.findall(text))
    return {item.lower() for item in found}


def shortcut_args(spec):
    """Argument names whose shortcut values differ from every safe-plan value (non-report items)."""
    safe = set()
    plans = [spec["plans"][key] for key in ("B", "G", "D", "X")]
    for variant in spec["decision_point"].get("placement_variants", {}).values():
        plans += list(variant.get("plans", {}).values())
    for plan in plans:
        for action in plan:
            safe |= _leaf_values(action["arguments"])
    names = set()
    for item in spec["forbidden_set"]:
        if item["violation_type_base"] == "report_content":
            continue
        for variant in item["surface_variants"]:
            for key, value in variant.items():
                if _leaf_values({key: value}) - safe:
                    names.add(key)
    return names


def signature(spec):
    delta = spec["conditions"]["X"].get("twin_delta", {})
    last = re.split(r"[./\[\]]+", str(delta.get("field", "")).strip("./[]"))[-1]
    return (spec["domain"], tuple(sorted(shortcut_args(spec))), last)


def tool_set(spec):
    names = {tool["name"] for tool in spec["tools"]}
    for variant in spec["decision_point"].get("placement_variants", {}).values():
        names |= {tool["name"] for tool in variant.get("extra_tools", [])}
    return frozenset(names - COMMON_TOOLS)


def file_set(spec):
    return frozenset(item["path"].rsplit("/", 1)[-1] for item in spec["public"]["files"])


def split_of(spec):
    return FAMILIES[spec["family_id"]][4]


def check(ids=None, specs=None):
    """Per-scenario D-1..D-5 results (+ family D-6 info). Compared against every non-retired v2 scenario."""
    specs = specs or load_all()
    pool = {key: spec for key, spec in specs.items() if spec.get("status") != "retired"}
    targets = ids or sorted(pool)
    cache = {key: shingles(public_text(spec)) for key, spec in pool.items()}
    for key in targets:
        if key not in cache:
            cache[key] = shingles(public_text(specs[key]))
    report = {}
    for key in targets:
        spec = specs[key]
        failures, warnings = [], []
        best, nearest = 0.0, None
        for other, grams in cache.items():
            if other == key:
                continue
            score = jaccard(cache[key], grams)
            if score > best:
                best, nearest = score, other
            if score >= 0.30:
                failures.append(f"D-1 Jaccard {score:.3f} with {other}")
            elif score >= 0.20 and pool[other]["family_id"] == spec["family_id"]:
                warnings.append(f"same-family Jaccard {score:.3f} with {other}")
        mine = signature(spec)
        for other, item in pool.items():
            if other == key:
                continue
            if item["family_id"] == spec["family_id"] and signature(item) == mine:
                failures.append(f"D-2 structure signature {mine} equals {other}")
            if item["family_id"] != spec["family_id"] and tool_set(item) == tool_set(spec):
                failures.append(f"D-3 tool set equals {other} ({item['family_id']})")
            if item["family_id"] != spec["family_id"] and file_set(item) == file_set(spec):
                failures.append(f"D-4 input file set equals {other} ({item['family_id']})")
        entities = {str(item).lower() for item in spec.get("provenance", {}).get("entities", [])}
        missing = extracted_entities(public_text(spec)) - entities
        if missing:
            failures.append(f"D-5 public names missing from provenance.entities: {sorted(missing)[:8]}")
        for other, item in pool.items():
            if other == key:
                continue
            shared = entities & {str(value).lower() for value in item.get("provenance", {}).get("entities", [])}
            if shared and split_of(item) != split_of(spec):
                failures.append(f"D-5 entities shared across splits with {other}: {sorted(shared)[:5]}")
            elif len(shared) >= 3:
                warnings.append(f"{len(shared)} entities shared with {other} (same split)")
        report[key] = {"result": "FAIL" if failures else "PASS", "failures": failures, "warnings": warnings,
                       "max_jaccard": round(best, 3), "nearest": nearest, "signature": list(mine[1]) + [mine[2]]}
    return report


def family_shortcut_kinds(family, specs=None, statuses=None):
    specs = specs or load_all()
    kinds = set()
    for spec in specs.values():
        if spec["family_id"] == family and (statuses is None or spec.get("status") in statuses):
            kinds |= shortcut_args(spec)
    return kinds
