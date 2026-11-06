"""Authoring helpers for generator (G) scripts. The JSON written here is the source of truth.

Usage from a builder script (cwd = Scenario/):
    sys.path.insert(0, "v2-generation/tools"); from authoring import finalize
    finalize(spec, padding_file="/workspace/input/<note>.md", names=["홍길동", ...])
"""
import sys
from pathlib import Path

SCENARIO = Path(__file__).resolve().parents[2]
if str(SCENARIO) not in sys.path:
    sys.path.insert(0, str(SCENARIO))

from scenario_suite.v2.diversity import extracted_entities, public_text  # noqa: E402
from scenario_suite.v2.registry import write_spec  # noqa: E402

import padding  # noqa: E402

COMMON_STATUS = ["number_commas", "korean_units", "case", "whitespace", "list_order"]


def entities_for(spec, names=()):
    found = set(extracted_entities(public_text(spec)))
    ordered = sorted(found | {name for name in names if name})
    return ordered


def finalize(spec, padding_file, names=(), write=True, root=None):
    """Calibrate padding, fill provenance.entities, write the JSON. Returns the calibration numbers."""
    calibration = padding.calibrate(spec, padding_file)
    spec["provenance"]["entities"] = entities_for(spec, names)
    spec["provenance"]["padding_calibration"] = calibration
    if write:
        write_spec(spec, root)
    return calibration
