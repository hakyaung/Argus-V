"""V-01 / E-01: the standard-library schema validator (TASK P0-4)."""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from scenario_suite.v2.schema import iter_errors, load_template, schema_error_v2, validate_spec

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v2" / "AVv2-F01-PRC-01.v2.json"


def tool(spec, name):
    return next(item for item in spec["tools"] if item["name"] == name)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_fixture_and_template_example_pass(self):
        self.assertEqual(validate_spec(self.spec), [])
        self.assertEqual(validate_spec(load_template()["examples"][0]), [])

    def test_rejects_bad_id_extra_key_and_bad_status(self):
        for mutate, needle in ((lambda s: s.update(id="AVv2-F16-PRC-01"), "$.id"),
                               (lambda s: s.update(id="AV-S01"), "$.id"),
                               (lambda s: s.update(unexpected=1), "unknown key 'unexpected'"),
                               (lambda s: s.update(status="ready"), "$.status"),
                               (lambda s: s["public"].update(output_dir_precreated=False), "output_dir_precreated")):
            spec = deepcopy(self.spec)
            mutate(spec)
            errors = validate_spec(spec)
            self.assertTrue(any(needle in error for error in errors), (needle, errors))

    def test_nullable_token_and_bad_tool_kind_are_rejected(self):
        spec = deepcopy(self.spec)
        tool(spec, "send_secure_transfer")["parameters"]["properties"]["authorization_id"]["type"] = ["string", "null"]
        self.assertTrue(any("authorization_id" in error for error in validate_spec(spec)))
        spec = deepcopy(self.spec)
        tool(spec, "send_secure_transfer")["kind"] = "fetch"
        self.assertTrue(any(".kind" in error for error in validate_spec(spec)))

    def test_runtime_arguments_enum_pattern_and_extra_keys(self):
        submit = tool(self.spec, "submit_result")["parameters"]
        ok = {"status": "COMPLETED", "summary": "x", "claims": ["shared_to=a"]}
        self.assertIsNone(schema_error_v2(ok, submit))
        self.assertIn("enum", schema_error_v2(dict(ok, status="DONE"), submit))
        self.assertIn("pattern", schema_error_v2(dict(ok, claims=["recipient=a"]), submit))
        self.assertIn("unknown key", schema_error_v2(dict(ok, extra=1), submit))
        share = tool(self.spec, "share_document")["parameters"]
        bad = {"recipients": ["someone@else.example"], "document_path": "/workspace/output/quote_comparison.md"}
        self.assertIn("enum", schema_error_v2(bad, share))

    def test_local_ref_const_and_bounds(self):
        schema = {"$defs": {"n": {"type": "integer", "minimum": 1, "maximum": 3}},
                  "type": "object", "properties": {"n": {"$ref": "#/$defs/n"}, "c": {"const": "x"},
                                                   "s": {"type": "string", "minLength": 2}}}
        self.assertEqual(list(iter_errors({"n": 2, "c": "x", "s": "ab"}, schema)), [])
        errors = list(iter_errors({"n": 9, "c": "y", "s": "a"}, schema))
        self.assertEqual(len(errors), 3)


if __name__ == "__main__":
    unittest.main()
