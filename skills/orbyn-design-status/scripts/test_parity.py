"""Git/CLI regressions around the shared pure lifecycle rules."""
import json
import unittest

import design_status as ds
from test_design_status import Fixture, FakeGh, git, commit_all, set_origin_main, page


class ParityTests(Fixture):
    def settings(self, **settings):
        path = self.root / "workspace/projects.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["projects"][0].update(settings)
        path.write_text(json.dumps(data), encoding="utf-8")

    def merged(self, screens=None):
        base = git(self.app, "rev-parse", "HEAD")
        tip = self.branch("feat/x")
        git(self.app, "merge", "-q", "--no-ff", "feat/x", "-m", "merged")
        set_origin_main(self.app)
        build = {"repo": "../app", "branch": "feat/x", "sha": base}
        if screens:
            build["screens"] = screens
        self.set_status({self.key(): {"build": build}, self.key("two"): {"build": build}})
        return tip

    def test_each_screen_confirms_marker_on_default_commit(self):
        self.merged([self.key(), self.key("two")])
        one = self.frame()[0]
        two = self.frame("two")[0]
        self.assertEqual(one["build"], "implemented")
        self.assertFalse(one["unconfirmed"])
        self.assertTrue(two["unconfirmed"])
        self.assertEqual(two["screens"], [self.key(), self.key("two")])
        (self.app / "src/a.ts").write_text("// design: p/two\n", encoding="utf-8")
        # Uncommitted markers never confirm merged code.
        self.assertFalse(self.frame()[0]["unconfirmed"])
        self.assertTrue(self.frame("two")[0]["unconfirmed"])

    def test_markers_require_exact_token_and_full_key_can_confirm(self):
        self.merged()
        (self.app / "src/a.ts").write_text(f"// design: p/one-more\n// {self.key('two')}\n", encoding="utf-8")
        commit_all(self.app, "markers")
        set_origin_main(self.app)
        self.assertTrue(self.frame()[0]["unconfirmed"])
        self.assertFalse(self.frame("two")[0]["unconfirmed"])

    def test_tag_ship_requires_matching_setting(self):
        tip = self.merged()
        git(self.app, "tag", "v1.2", tip)
        self.assertEqual(self.frame()[0]["build"], "implemented")
        self.settings(releaseTagPattern="release-*")
        self.assertEqual(self.frame()[0]["build"], "implemented")
        self.settings(releaseTagPattern="v[0-9].?")
        self.assertEqual(self.frame()[0]["build"], "shipped")

    def test_deploy_ship_checks_ancestry_and_default_branch(self):
        self.merged()
        self.settings(deployWorkflow="deploy.yml")
        sha = git(self.app, "rev-parse", "HEAD")
        gh = FakeGh(False)
        calls = []
        gh.runs = lambda repo, workflow, branch: calls.append((repo, workflow, branch)) or [{"headSha": "bad"}, {"headSha": sha}]
        self.assertEqual(self.frame(gh=gh)[0]["build"], "shipped")
        self.assertEqual(calls[0], (self.app, "deploy.yml", "main"))
        gh.runs = lambda *args: [{"headSha": "0" * 40}]
        self.assertEqual(self.frame(gh=gh)[0]["build"], "implemented")
        gh.runs = lambda *args: None
        self.assertEqual(self.frame(gh=gh)[0]["build"], "implemented")

    def test_lookup_failure_is_reported_as_unverified(self):
        self.set_status({self.key(): {"build": {"repo": "../app", "branch": "gone"}}})
        gh = FakeGh(True)
        gh.prs = lambda *args, **kwargs: None
        row, result = self.frame(gh=gh)
        self.assertEqual(row["buildInfo"]["reason"], "unverified")
        self.assertTrue(any("unverified" in n for n in result["notes"]))
        row = self.frame(gh=FakeGh(True))[0]
        self.assertEqual(row["buildInfo"]["reason"], "Branch deleted without a merge")

    def test_mismatched_saved_repo_is_not_looked_up_in_app(self):
        self.merged()
        self.set_status({self.key(): {"build": {"repo": "../elsewhere", "branch": "feat/x", "sha": "0" * 40}}})
        row = self.frame()[0]
        self.assertEqual(row["buildInfo"], {"state": "check", "reason": "unverified"})

    def test_v1_read_migrates_without_writing_and_clear_persists(self):
        self.set_status({self.key(): {"status": "implemented", "group": "g", "checked": "old",
                                     "evidence": {"paths": ["src/a.ts:1", 3], "pr": "old"}},
                         self.key("two"): {"status": "planned"}, "gone": {"status": "designed"}})
        before = self.status.read_bytes()
        migrated = ds.load_status(self.root, "p")["frames"]
        self.assertEqual(migrated[self.key()], {"group": "g", "markers": ["src/a.ts:1"], "legacy": "implemented"})
        self.assertEqual(migrated[self.key("two")], {"planned": True})
        self.assertNotIn("gone", migrated)
        self.assertEqual(self.status.read_bytes(), before)
        ds.clear(self.root, "p/one")
        saved = json.loads(self.status.read_bytes())["frames"]
        self.assertEqual(saved[self.key()], {"group": "g", "markers": ["src/a.ts:1"]})

    def test_record_migrates_legacy_and_keeps_markers_and_screens(self):
        self.set_status({self.key(): {"status": "building", "evidence": {"paths": ["src/a.ts:1"]}}})
        ds.record(self.root, "p/one", "main", screens=[self.key(), self.key("two")])
        saved = json.loads(self.status.read_bytes())["frames"][self.key()]
        self.assertNotIn("status", saved)
        self.assertNotIn("legacy", saved)
        self.assertEqual(saved["markers"], ["src/a.ts:1"])
        self.assertEqual(saved["build"]["screens"], [self.key(), self.key("two")])

    def test_migration_filters_build_and_invalid_frames(self):
        self.assertEqual(ds.migrate_frame([]), {})
        self.assertEqual(ds.migrate_frame({"build": {"repo": "r", "branch": "b", "screens": [1, "s"]},
                                           "status": "implemented", "markers": []}),
                         {"build": {"repo": "r", "branch": "b", "recorded": "", "screens": ["s"]}})

    def test_data_was_moves_saved_status_in_memory_then_on_save(self):
        self.set_status({self.key(): {"planned": True, "markers": ["a:1"]}})
        self.file.write_text(self.file.read_text(encoding="utf-8").replace('id="one"', 'id="renamed" data-was="one"'), encoding="utf-8")
        before = self.status.read_bytes()
        self.assertEqual(self.frame("renamed")[0]["design"], "planned")
        self.assertEqual(self.status.read_bytes(), before)
        ds.record(self.root, "p/renamed", "main")
        saved = json.loads(self.status.read_bytes())["frames"]
        self.assertNotIn(self.key(), saved)
        self.assertTrue(saved[self.key("renamed")]["planned"])

    def test_rename_collision_and_live_old_key_are_preserved(self):
        saved = {self.key(): {"planned": True}, self.key("two"): {"group": "g"}}
        frames = ds.frames_of(self.root, ds.get_project(self.root, "p"))
        frames[1].was = "one"
        self.assertEqual(ds.carry_renames(saved, frames), saved)
        frames[0].section = "new"
        frames[0].key = self.key("new")
        frames[0].was = "one"
        frames[1].was = "one"
        self.assertEqual(ds.carry_renames(saved, frames), saved)

    def test_clean_old_section_deleted_on_main_is_designed(self):
        old = git(self.designs, "rev-parse", "HEAD")
        self.file.write_text(page(("two", "Two")), encoding="utf-8")
        commit_all(self.designs, "delete one")
        set_origin_main(self.designs)
        git(self.designs, "checkout", "-q", "--detach", old)
        self.assertEqual(self.frame()[0]["design"], "designed")

    def test_formatting_only_is_published_but_raw_whitespace_matters(self):
        original = self.file.read_text(encoding="utf-8")
        self.file.write_text(original.replace('<section id="one">', "<SECTION class='' id='one'>").replace("body one", " body   one "), encoding="utf-8")
        # Adding class is a semantic change.
        self.assertEqual(self.frame()[0]["design"], "designed")
        self.file.write_text(original.replace('id="one"', "id = 'one'").replace("body one", " body   one "), encoding="utf-8")
        self.assertEqual(self.frame()[0]["design"], "published")
        for tag in ("pre", "textarea", "script", "style"):
            self.assertNotEqual(ds.norm(f"<{tag}> a  b </{tag}>"), ds.norm(f"<{tag}> a b </{tag}>"))
        self.assertEqual(ds.norm('<section z="2" a="1"> a <!-- comment --> b </section>'),
                         '<section a="1" z="2">a  b</section>')

    def test_shared_styles_metadata(self):
        (self.file.parent / "style.css").write_text("body {}", encoding="utf-8")
        row = self.frame()[0]
        self.assertEqual(row["design"], "published")
        self.assertTrue(row["designInfo"]["sharedStylesChanged"])

    def test_default_fallback_order_uses_full_refs(self):
        for branch in ("trunk", "develop", "master"):
            git(self.app, "update-ref", "refs/remotes/origin/" + branch, "HEAD")
        git(self.app, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
        gh = FakeGh(True)
        gh.default_branch = lambda repo: "trunk"
        self.assertEqual(ds.Git().default_ref(self.app, "master", gh), "refs/remotes/origin/master")
        self.assertEqual(ds.Git().default_ref(self.app, "missing", gh), "refs/remotes/origin/develop")
        git(self.app, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        self.assertEqual(ds.Git().default_ref(self.app, "bad..name", gh), "refs/remotes/origin/trunk")
        self.assertEqual(ds.Git().default_ref(self.app), "refs/remotes/origin/main")
        git(self.app, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(ds.Git().default_ref(self.app), "refs/remotes/origin/master")

    def test_branch_tag_name_clash_does_not_resolve_tag(self):
        git(self.app, "tag", "feat/tag-only")
        self.assertIsNone(ds.Git().branch_tip(self.app, "feat/tag-only"))

    def test_known_pr_follows_renamed_branch_and_ignores_fork_tip(self):
        self.branch("renamed")
        gh = FakeGh(True)
        gh.prs = lambda *args, **kwargs: [{"number": 1, "branch": "renamed", "state": "open"}]
        facts = ds.build_facts(ds.Git(), gh, self.app, {"branch": "old", "pr": 1})
        self.assertEqual(facts["renamedBranch"], "renamed")
        self.assertTrue(facts["tip"])
        gh.prs = lambda *args, **kwargs: [{"number": 1, "branch": "renamed", "state": "open", "fork": True}]
        self.assertIsNone(ds.build_facts(ds.Git(), gh, self.app, {"branch": "old", "pr": 1})["tip"])

    def test_record_fork_head_uses_pr_without_local_sha(self):
        self.branch("feat/x")
        git(self.app, "update-ref", "refs/remotes/origin/feat/x", "HEAD")
        recorded = ds.record(self.root, "p/one", "alice:feat/x", pr="42")
        self.assertEqual(recorded["build"]["branch"], "alice:feat/x")
        self.assertEqual(recorded["build"]["pr"], "42")
        self.assertNotIn("sha", recorded["build"])
        calls = []
        gh = FakeGh(True)
        gh.prs = lambda repo, **kwargs: calls.append(kwargs) or [{"number": 42, "branch": "alice:feat/x", "state": "merged"}]
        facts = ds.build_facts(ds.Git(), gh, self.app, recorded["build"])
        self.assertEqual(calls, [{"pr": "42"}])
        self.assertIsNone(facts["tip"])
        self.assertEqual(ds.build_state({"build": recorded["build"]}, facts)["state"], "implemented")

    def test_fork_head_without_pr_cannot_borrow_local_branch(self):
        self.branch("feat/x")
        with self.assertRaises(SystemExit):
            ds.record(self.root, "p/one", "alice:feat/x")
        recorded = ds.record(self.root, "p/one", "alice:feat/x", allow_missing=True)
        self.assertNotIn("sha", recorded["build"])
        self.assertIsNone(ds.Git().branch_tip(self.app, "alice:feat/x"))

    def test_record_rejects_invalid_fork_owner_or_branch(self):
        for head in (":feat/x", "alice:", "alice:bad branch", "a.b:feat/x", "alice:../x", "alice:b:c", "a/b:feat/x"):
            with self.subTest(head=head), self.assertRaises(SystemExit):
                ds.record(self.root, "p/one", head, pr="42")


class GhAdapterTests(unittest.TestCase):
    def test_null_github_default_falls_through(self):
        gh = ds.Gh()
        gh.origin = lambda repo: "owner/repo"
        gh.query = lambda *args: {"defaultBranchRef": None}
        self.assertIsNone(gh.default_branch("repo"))

    def test_origin_parser(self):
        for url in ("https://github.com/owner/repo.git", "git@github.com:owner/repo.git", "ssh://git@github.com:22/owner/repo"):
            self.assertEqual(ds.github_origin(url), "owner/repo")
        self.assertIsNone(ds.github_origin("https://elsewhere.test/owner/repo"))

    def test_tag_glob_and_invalid_range(self):
        self.assertTrue(ds.matches_tag("v2", "v[!a-z]"))
        self.assertFalse(ds.matches_tag("v2", "v[z-a]"))
        self.assertTrue(ds.matches_tag("v2", "v[^2]"))

    def test_open_prs_paginate_past_100(self):
        gh = ds.Gh()
        gh.origin = lambda repo: "owner/repo"
        raw = {"number": 1, "html_url": "u", "title": "t", "state": "open",
               "head": {"ref": "feature", "repo": {"full_name": "owner/repo"}, "user": {"login": "owner"}},
               "base": {"repo": {"full_name": "owner/repo"}}}
        calls = []
        gh.query = lambda repo, *args: calls.append(args) or ([raw] * 100 if args[-1].endswith("&page=1") else [])
        self.assertEqual(len(gh.prs("repo", state="open")), 100)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
