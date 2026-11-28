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

    def test_normalize_cases(self):
        cases = json.loads(Path(__file__).with_name("rule_cases.json").read_text(encoding="utf-8"))
        for case in cases["normalize"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(ds.norm(case["left"]) == ds.norm(case["right"]), case["equal"])

    def test_rename_cases(self):
        cases = json.loads(Path(__file__).with_name("rule_cases.json").read_text(encoding="utf-8"))
        for case in cases["renames"]:
            with self.subTest(case=case["name"]):
                key = lambda section: "repo:page.html#" + section
                frames = [ds.Frame(key(s["section"]), "p", s["section"], s["title"], "page.html", Path("repo"), s.get("was"))
                          for s in ds.section_ranges(case["html"])]
                saved = {key(s): {"group": s} for s in case["saved"]}
                before = dict(saved)
                carried = ds.carry_renames(saved, frames)
                self.assertEqual(sorted(carried), sorted(key(s) for s in case["expected"]))
                self.assertEqual(saved, before)
                for destination, entry in carried.items():
                    old = next((f.was for f in frames if f.key == destination), None)
                    self.assertEqual(entry, saved.get(destination, saved.get(key(old or ""))))
