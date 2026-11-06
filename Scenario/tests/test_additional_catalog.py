"""Batch selection and JSON packaging tests; never model performance tests."""
import json
import tempfile
import unittest
from pathlib import Path

from scenario_suite import Session, all_profiles
from scenario_suite.catalog import export_operator_specs, make_bank, operator_contract, scenario_spec, select_ids
from scenario_suite.__main__ import export_fixtures
from scenario_suite.engine import digest


class AdditionalCatalogTests(unittest.TestCase):
    def test_eighty_distinct_business_titles_and_ids(self):
        profiles = all_profiles()
        self.assertEqual(set(profiles), {f"AV-S{number:02d}" for number in range(1, 81)})
        self.assertEqual(len({item["operator_title"] for item in profiles.values()}), 80)
        self.assertEqual(len({item["goal"] for item in profiles.values()}), 80)

    def test_batches_do_not_overlap(self):
        initial, additional = set(select_ids("initial")), set(select_ids("additional"))
        self.assertEqual(len(initial), 30)
        self.assertEqual(len(additional), 30)
        self.assertFalse(initial & additional)
        fakeweb = set(select_ids("fakeweb"))
        self.assertEqual(len(fakeweb), 20)
        self.assertFalse((initial | additional) & fakeweb)
        self.assertEqual(initial | additional | fakeweb, set(select_ids()))
        with self.assertRaises(ValueError):
            select_ids("additional", "AV-S01")

    def test_all_new_specs_have_implemented_tools_and_matched_controls(self):
        profiles = all_profiles()
        for identifier in select_ids("additional"):
            with self.subTest(identifier=identifier):
                spec = scenario_spec(identifier, profiles)
                self.assertEqual(spec["title"], profiles[identifier]["operator_title"])
                self.assertEqual(spec["goal"], profiles[identifier]["goal"])
                self.assertEqual(spec["cluster"], profiles[identifier]["cluster"])
                self.assertGreaterEqual(len(profiles[identifier]["files"]), 3)
                self.assertEqual(set(spec["tools"]), set(profiles[identifier]["tools"]))
                self.assertEqual(set(profiles[identifier]["plans"]), {"C0", "C1", "C2"})
                self.assertEqual(spec["forbidden_call"], profiles[identifier]["violation"])

    def test_operator_contract_marks_gold_private(self):
        contract = operator_contract("AV-S31")
        self.assertEqual(contract["visibility"], "OPERATOR_ONLY")
        self.assertEqual(contract["model_rollouts"], 0)
        self.assertIn("plans", contract["runtime_contract"])
        self.assertIn("success", contract["runtime_contract"])
        self.assertFalse(contract["execution"]["json_file_loading_implemented"])
        public = Session("AV-S31", "C2").public_context()
        self.assertNotIn("plans", public)
        self.assertNotIn("success", public)
        self.assertNotIn("spec", public)

    def test_bank_counts_and_condition_contracts(self):
        bank = make_bank("additional")
        self.assertEqual(bank["family_count"], 30)
        self.assertEqual(bank["total_condition_specs"], 90)
        self.assertEqual(bank["identifier_range"], "AV-S31..AV-S60")
        self.assertEqual([item["id"] for item in bank["scenarios"]], select_ids("additional"))
        fakeweb = make_bank("fakeweb")
        self.assertEqual(fakeweb["family_count"], 20)
        self.assertEqual(fakeweb["total_condition_specs"], 60)
        self.assertEqual(make_bank("all")["total_condition_specs"], 240)

    def test_thirty_full_json_files_export_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "operators"
            result = export_operator_specs(root)
            self.assertEqual(result["individual_full_json"], 30)
            self.assertEqual(len(list(root.glob("AV-S*.v1.json"))), 30)
            index = json.loads((root / "OPERATOR-INDEX.json").read_text())
            self.assertEqual(index["visibility"], "OPERATOR_ONLY")
            for item in index["entries"]:
                self.assertEqual(digest((root / item["file"]).read_text()), item["sha256"])
                self.assertEqual(json.loads((root / item["file"]).read_text())["scenario_id"], item["scenario"])
            markdown = (root / "scenario-bank.md").read_text()
            self.assertEqual(sum(line.startswith("## AV-S") for line in markdown.splitlines()), 30)
            with self.assertRaises(FileExistsError):
                export_operator_specs(root)

    def test_additional_public_export_ninety_conditions_with_no_private_spec(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "public"
            result = export_fixtures(root, batch="additional")
            self.assertEqual(result["conditions"], 90)
            self.assertEqual(len(list(root.rglob("public-context.json"))), 90)
            self.assertFalse((root / "AV-S01").exists())
            self.assertFalse(list(root.rglob("AV-S*.v1.json")))
            for identifier in select_ids("additional"):
                first, second = root / identifier / "C1", root / identifier / "C2"
                self.assertEqual((first / "public-context.json").read_bytes(), (second / "public-context.json").read_bytes())
                first_files = {str(path.relative_to(first / "files")): path.read_bytes() for path in (first / "files").rglob("*") if path.is_file()}
                second_files = {str(path.relative_to(second / "files")): path.read_bytes() for path in (second / "files").rglob("*") if path.is_file()}
                self.assertEqual(first_files, second_files)
                self.assertEqual(set(first_files), {path[1:] for path in Session(identifier, "C1").files})

    def test_invalid_batch_does_not_create_partial_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "public"
            with self.assertRaises(ValueError):
                export_fixtures(root, "AV-S01", batch="additional")
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
