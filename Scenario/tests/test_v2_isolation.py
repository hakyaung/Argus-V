"""v1 regression guard (TASK P0-4, A-7): v2 never changes v1 profiles, sessions or replays."""
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from scenario_suite import all_profiles

ROOT = Path(__file__).resolve().parents[1]  # Scenario/
BASELINE = ROOT / "v2-generation" / "baseline"
P0_2 = [("                 observer=None, clock=None, max_steps=80, profile=None):",
         "                 observer=None, clock=None, max_steps=80):"),
        ("        self.profile = deepcopy(profile) if profile is not None else all_profiles()[scenario]",
         "        self.profile = all_profiles()[scenario]")]


@unittest.skipUnless(BASELINE.joinpath("v1-fingerprint.json").exists(), "Phase 0 baseline not recorded")
class IsolationTests(unittest.TestCase):
    def test_v1_profiles_are_still_eighty(self):
        import scenario_suite.v2  # noqa: F401
        self.assertEqual(len(all_profiles()), 80)
        self.assertEqual(sorted(all_profiles()), [f"AV-S{number:02d}" for number in range(1, 81)])

    def test_fingerprint_after_importing_v2_matches_the_baseline(self):
        script = ROOT / "v2-generation" / "tools" / "v1_fingerprint.py"
        code = ("import runpy, sys; sys.path.insert(0, %r); import scenario_suite.v2; runpy.run_path(%r, run_name='__main__')"
                % (str(ROOT), str(script)))
        output = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(output), json.loads(BASELINE.joinpath("v1-fingerprint.json").read_text()))

    def test_v1_files_unchanged_except_the_allowed_engine_change(self):
        recorded = json.loads(BASELINE.joinpath("v1-file-sha256.json").read_text())
        for name, digest in recorded.items():
            path = ROOT.parent / name
            data = path.read_bytes()
            if name.endswith("scenario_suite/engine.py"):
                text = data.decode("utf-8")
                for new, old in P0_2:
                    self.assertEqual(text.count(new), 1, new)
                    text = text.replace(new, old)
                data = text.encode("utf-8")
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)


if __name__ == "__main__":
    unittest.main()
