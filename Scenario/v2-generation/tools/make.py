"""Build designs into scenario JSON: python3 v2-generation/tools/make.py F02 [ID ...] (cwd = Scenario/)."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import cs  # noqa: E402


def designs(family):
    path = HERE.parent / "designs" / f"{family}.py"
    spec = importlib.util.spec_from_file_location(f"design_{family}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DESIGNS


def main(argv):
    family, wanted = argv[0], set(argv[1:])
    for design in designs(family):
        if wanted and design["id"] not in wanted:
            continue
        spec, calibration = cs.make(design)
        print(design["id"], spec["decision_point"]["placement"], spec["collection"]["length_band"], calibration)


if __name__ == "__main__":
    main(sys.argv[1:])
