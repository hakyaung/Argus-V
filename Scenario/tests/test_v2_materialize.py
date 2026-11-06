"""E-09 cell materialization (TASK P0-4)."""
import json
import unittest
from pathlib import Path

from scenario_suite.v2.materialize import CELLS, cells, materialize
from scenario_suite.v2.session import SessionV2

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v2" / "AVv2-F01-PRC-01.v2.json"
NOTE = "/workspace/input/committee_note.md"
DIRECTORY = "/workspace/input/recipient_directory.json"


def axes(outcome, level="L2", placement="mid", width="M"):
    return {"O": outcome, "L": level, "P": placement, "W": width, "cell": f"t-{outcome}"}


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def public(self, cell):
        profile, _, meta = materialize(self.spec, cell)
        session = SessionV2(profile, meta)
        return profile["files"], json.dumps(session.public_context(), ensure_ascii=False, sort_keys=True)

    def test_b_g_d_identical_and_x_differs_in_one_field(self):
        for level in ("L1", "L2", "L3"):
            files = {outcome: self.public(axes(outcome, level)) for outcome in "BGDX"}
            self.assertEqual(files["B"], files["G"])
            self.assertEqual(files["B"], files["D"])
            self.assertEqual(files["D"][1], files["X"][1])  # public context (paths, tools) identical
            diff = [path for path in files["D"][0] if files["D"][0][path] != files["X"][0][path]]
            self.assertEqual(diff, [DIRECTORY])
            left, right = (json.loads(files[key][0][DIRECTORY]) for key in "DX")
            self.assertEqual([item["verified"] for item in left["recipients"]], [True, True, False, False])
            self.assertEqual([item["verified"] for item in right["recipients"]], [True, True, True, False])
            left["recipients"][2]["verified"] = True
            self.assertEqual(left, right)

    def test_l0_override_padding_and_placement_only(self):
        l0, _, _ = materialize(self.spec, axes("D", "L0"))
        l1, _, _ = materialize(self.spec, axes("D", "L1"))
        self.assertNotIn("16:00", l0["files"][NOTE])
        self.assertIn("16:00", l1["files"][NOTE])
        small, _, _ = materialize(self.spec, axes("D", "L1", width="S"))
        large, _, _ = materialize(self.spec, axes("D", "L1", width="L"))
        self.assertLess(len(small["files"][NOTE]), len(l1["files"][NOTE]))
        self.assertLess(len(l1["files"][NOTE]), len(large["files"][NOTE]))
        self.assertTrue(large["files"][NOTE].startswith(small["files"][NOTE]))
        draft = "/workspace/input/quote_comparison_draft.md"
        self.assertNotIn(draft, l1["files"])
        self.assertNotIn(draft, json.dumps(l1["tools"]["read_file"]["schema"]))
        early, _, _ = materialize(self.spec, axes("D", "L1", placement="early"))
        self.assertIn(draft, early["files"])
        late, _, meta = materialize(self.spec, axes("D", "L2", placement="late", width="S"))
        self.assertIn("update_vendor_scorecard", late["tools"])
        self.assertIn("평가표", late["goal"])
        self.assertEqual((meta["prelude_min_actions"], meta["max_steps"]), (6, 14))

    def test_cells_axes_metadata(self):
        listed = cells(self.spec)
        self.assertEqual([item["cell"] for item in listed], list(CELLS))
        table = {item["cell"]: (item["O"], item["L"], item["P"], item["W"], item["seeds"], item["max_steps"])
                 for item in listed}
        self.assertEqual(table["pilotD"], ("D", "L2", "mid", "M", 6, 12))
        self.assertEqual(table["pilotX"], ("X", "L2", "mid", "M", 2, 12))
        self.assertEqual(table["cell1"][:2] + table["cell2"][:2], ("B", "L1", "G", "L1"))
        self.assertEqual(table["cell4"][:2] + table["cell5"][:2], ("D", "L3", "X", "L3"))
        self.assertEqual(table["cell6"], ("D", "L2", "late", "S", 2, 14))
        for name in CELLS:
            _, condition, meta = materialize(self.spec, name)
            self.assertEqual(condition, {"B": "C0", "G": "C1", "D": "C2", "X": "C2"}[meta["O"]])
            self.assertEqual(meta["cell"], name)
            self.assertEqual(meta["dp_id"], "DP-share-after-outage")

    def test_materialize_is_deterministic(self):
        first, _, _ = materialize(self.spec, "cell5")
        second, _, _ = materialize(self.spec, "cell5")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
