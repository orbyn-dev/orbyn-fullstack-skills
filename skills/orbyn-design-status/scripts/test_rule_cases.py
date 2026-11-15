"""Run the shared Designer lifecycle cases against the Python rules."""
import json
import unittest
from pathlib import Path

import design_status as ds


class SharedRuleCases(unittest.TestCase):
    def test_build_cases(self):
        cases = json.loads(Path(__file__).with_name("rule_cases.json").read_text(encoding="utf-8"))
        for case in cases["build"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(ds.build_state(case["frame"], case.get("facts")), case["expected"])

    def test_design_cases(self):
        cases = json.loads(Path(__file__).with_name("rule_cases.json").read_text(encoding="utf-8"))
        for case in cases["design"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(ds.design_state(case["section"], case["planned"], case["versions"]), case["expected"])
