# 파일 위치: ~/Desktop/Argus-V/argus_hook/harness_config.py
"""Versioned agent-harness settings for scenario rollouts.

Every rollout records ``harness_version`` and the full config in rollout.json, so data from
different harness versions is never mixed silently (resume and manifest building check it).

* ``v1`` reproduces the harness that produced the 2026-10-14 rollouts (default; unchanged data).
* ``v2`` is the upgraded harness described in docs/agent-diagnosis-20261102.md. Each change that
  can shift the data distribution is a separate field, so it can be ablated on its own.

No field may put condition IDs, operator titles, plans or label information into the prompt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

LEGACY_VERSION = "argus-v.harness.v1"
RETRY_MODES = ("feedback", "resample", "resample_then_feedback")
SEED_SCHEMES = ("shared", "condition_salted")


@dataclass(frozen=True)
class HarnessConfig:
    harness_version: str = LEGACY_VERSION
    prompt_version: str = "suite-prompt.v1"
    default_max_steps: int = 10
    # ---- output parsing / retry
    repair_actions: bool = False  # deterministic, logged repairs of unambiguous format slips
    infer_tool_from_arguments: bool = False  # ```tool block without a name → the single schema match
    retry_mode: str = "feedback"  # feedback: rejected reply + correction turn; resample: clean prompt, new seed
    resample_attempts: int = 2  # resample_then_feedback: clean resamples before the first feedback turn
    retry_context: str = "full_reply"  # full_reply (legacy: reasoning + action) | action_only
    # ---- business tool contract
    submit_requires_accepted_status: bool = False  # INVALID_STATUS submissions no longer end the rollout
    publish_submit_status_values: bool = False  # render the controller's accepted submit status values
    prompt_limits_from_harness: bool = False  # "Limits" line shows the harness step budget
    canonicalize_tool_paths: bool = False  # 'workspace/x', ' /workspace/x', './workspace/x' → '/workspace/x'
    # ---- bash / virtual file space
    ensure_output_dir: bool = False  # mkdir /workspace/output before the first action
    sync_bash_files_to_tools: bool = False  # files bash creates anywhere in /workspace are visible to tools
    # ---- sampling / observation
    seed_scheme: str = "shared"  # shared: same sample seed for C0/C1/C2 (identical early steps)
    chat_template_date: str | None = None  # pin "Current date:" in the gpt-oss system header
    capture_clean_prompt: bool = False  # extra pre_reasoning capture on the retry-free prompt of retried steps

    def __post_init__(self):
        if self.retry_mode not in RETRY_MODES:
            raise ValueError(f"retry_mode must be one of {RETRY_MODES}")
        if self.seed_scheme not in SEED_SCHEMES:
            raise ValueError(f"seed_scheme must be one of {SEED_SCHEMES}")
        if self.retry_context not in ("full_reply", "action_only"):
            raise ValueError("retry_context must be full_reply or action_only")
        if self.default_max_steps < 1 or self.resample_attempts < 0:
            raise ValueError("default_max_steps must be positive and resample_attempts non-negative")

    @property
    def is_legacy(self) -> bool:
        return self.harness_version == LEGACY_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    def with_overrides(self, **overrides) -> "HarnessConfig":
        known = {item.name for item in fields(self)}
        unknown = set(overrides) - known
        if unknown:
            raise ValueError(f"unknown harness settings {sorted(unknown)}")
        changed = {key: value for key, value in overrides.items() if value is not None}
        if not changed:
            return self
        # An override is a different harness; mark it so it never passes as the preset.
        version = self.harness_version if self.harness_version.endswith("+custom") else self.harness_version + "+custom"
        return replace(self, harness_version=version, **changed)

    def sample_seed(self, seed: int, step: int, attempt: int, condition: str) -> int:
        base = seed * 100_000 + step * 100 + attempt
        if self.seed_scheme == "shared":
            return base
        salt = sum(ord(char) for char in condition) % 97 + 1  # stable, small; keeps seeds distinct per condition
        return base + salt * 10_000_000

    def uses_feedback(self, attempt: int) -> bool:
        """Whether the retry after failed ``attempt`` (0-based) carries a correction turn."""
        if self.retry_mode == "feedback":
            return True
        if self.retry_mode == "resample":
            return False
        return attempt + 1 >= self.resample_attempts


PRESETS = {
    "v1": HarnessConfig(),
    "v2": HarnessConfig(
        harness_version="argus-v.harness.v2", prompt_version="suite-prompt.v2", default_max_steps=20,
        repair_actions=True, infer_tool_from_arguments=True, retry_mode="resample_then_feedback",
        resample_attempts=2, retry_context="action_only", submit_requires_accepted_status=True,
        publish_submit_status_values=True, prompt_limits_from_harness=True, canonicalize_tool_paths=True,
        ensure_output_dir=True, sync_bash_files_to_tools=True, seed_scheme="condition_salted",
        chat_template_date="2026-10-14", capture_clean_prompt=True),
}


def harness_preset(name: str) -> HarnessConfig:
    if name not in PRESETS:
        raise ValueError(f"unknown harness preset {name}; choose from {sorted(PRESETS)}")
    return PRESETS[name]


def rollout_harness_version(rollout: dict) -> str:
    """Rollouts written before versioning are v1."""
    return rollout.get("harness_version") or LEGACY_VERSION
