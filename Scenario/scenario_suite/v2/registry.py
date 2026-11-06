"""v2 scenario registry: Scenario/operator-scenarios-v2/Fxx/AVv2-Fxx-DOM-nn.v2.json (operator-only)."""
import hashlib
import json
import os
import re
from pathlib import Path

SCENARIO_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = SCENARIO_ROOT / "operator-scenarios-v2"
ID_PATTERN = re.compile(r"^AVv2-F(0[1-9]|1[0-5])-(FIN|PRC|HRI|PRV|OPS|PUB)-[0-9]{2}$")
RUNNABLE = ("pilot_queued", "pilot_passed", "collected")
FAMILIES = {
    "F01": ("destination_substitution", 10, 8, 4, "train"),
    "F02": ("grant_scope_reuse", 8, 8, 3, "validation"),
    "F03": ("unapproved_executor", 8, 7, 3, "train"),
    "F04": ("verification_bypass", 10, 8, 4, "train"),
    "F05": ("unsupported_completion_claim", 8, 8, 4, "validation"),
    "F06": ("partial_data_overclaim", 8, 7, 3, "train"),
    "F07": ("protected_state_tamper", 8, 7, 3, "test"),
    "F08": ("disclosure_minimization", 8, 7, 3, "train"),
    "F09": ("quota_threshold_evasion", 8, 8, 3, "validation"),
    "F10": ("stale_authority_reuse", 8, 7, 3, "train"),
    "F11": ("duty_separation", 6, 5, 3, "test"),
    "F12": ("untrusted_content_instruction", 8, 7, 2, "train"),
    "F13": ("time_window_violation", 6, 6, 3, "validation"),
    "F14": ("destructive_side_effect", 6, 5, 3, "test"),
    "F15": ("identity_impersonation", 6, 5, 3, "train"),
}
DOMAINS = ("FIN", "PRC", "HRI", "PRV", "OPS", "PUB")
V1_TEST = {"AV-S19", "AV-S21", "AV-S37", "AV-S38", "AV-S44", "AV-S47", "AV-S48", "AV-S52", "AV-S53",
           "AV-S56", "AV-S58", "AV-S60"}


def family_info(family):
    name, target, minimum, domains, split = FAMILIES[family]
    return {"family_id": family, "name": name, "target": target, "minimum": minimum,
            "min_domains": domains, "split": split}


def spec_path(identifier, root=None):
    return Path(root or SPEC_ROOT) / identifier[5:8] / f"{identifier}.v2.json"


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump_spec(spec):
    """Canonical on-disk form for every v2 JSON written by tools."""
    return json.dumps(spec, ensure_ascii=False, indent=2) + "\n"


def write_spec(spec, root=None):
    path = spec_path(spec["id"], root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")  # atomic: concurrent readers never see a partial file
    temporary.write_text(dump_spec(spec), encoding="utf-8")
    os.replace(temporary, path)
    return path


def all_paths(root=None):
    return sorted(Path(root or SPEC_ROOT).glob("F*/AVv2-*.v2.json"))


def load_spec(identifier, root=None):
    """Load one scenario JSON (file name and folder must match its id and family)."""
    path = spec_path(identifier, root) if ID_PATTERN.match(str(identifier)) else Path(identifier)
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if path.name != f"{spec.get('id')}.v2.json" or path.parent.name != spec.get("family_id"):
        raise ValueError(f"{path}: file name/folder do not match id/family_id")
    return spec


def load_all(root=None):
    specs = {}
    for path in all_paths(root):
        spec = json.loads(path.read_text(encoding="utf-8"))
        specs[spec["id"]] = spec
    return specs


def list_ids(status_in=None, family=None, root=None):
    ids = []
    for identifier, spec in sorted(load_all(root).items()):
        if status_in is not None and spec.get("status") not in status_in:
            continue
        if family is not None and spec.get("family_id") != family:
            continue
        ids.append(identifier)
    return ids


def runnable_ids(family=None, root=None):
    return list_ids(RUNNABLE, family, root)


def is_v2_id(identifier):
    return bool(ID_PATTERN.match(str(identifier)))
