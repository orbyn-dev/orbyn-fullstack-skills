"""Tests for design_status.py. Run: python3 -m unittest discover -s skills/orbyn-design-status/scripts"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_status as ds  # noqa: E402


def git(repo: Path, *args: str) -> str:
    # Same isolation as the script: an inherited GIT_DIR (as in a Git hook) must not redirect fixture setup.
    p = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                       capture_output=True, text=True, check=True, env=ds.child_env())
    return p.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    return path


def commit_all(repo: Path, msg: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def set_origin_main(repo: Path) -> None:
    git(repo, "update-ref", "refs/remotes/origin/main", "main")


class FakeGh:
    def __init__(self, available: bool = True, prs: list[dict] | None = None):
        self.available = available
        self._prs = prs or []

    def default_branch(self, repo):
        return None

    def runs(self, repo, workflow, branch):
        return [] if self.available else None

    def prs(self, repo, head=None, state="all", pr=None):
        if not self.available:
            return None
        out = [p for p in self._prs if head is None or p["headRefName"] == head]
        if pr is not None:
            out = [p for p in out if str(p.get("number")) == str(pr) or p.get("url") == pr]
        return [p for p in out if state == "all" or p["state"].lower() == state]


def page(*sections: tuple[str, str]) -> str:
    return "<html><body>\n" + "\n".join(
        f'<section id="{i}"><h2>{t}</h2><p>body {i}</p></section>' for i, t in sections) + "\n</body></html>\n"


class Fixture(unittest.TestCase):
    """Designer root with one project (`p`), a design repo and an app repo, all in a temp dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "designer"
        (self.root / "workspace" / "status").mkdir(parents=True)
        self.designs = init_repo(base / "designs")
        (self.designs / "artifacts").mkdir()
        self.file = self.designs / "artifacts" / "p.html"
        self.file.write_text(page(("one", "One"), ("two", "Two")), encoding="utf-8", newline="\n")
        commit_all(self.designs, "init")
        set_origin_main(self.designs)
        self.app = init_repo(base / "app")
        (self.app / "src").mkdir()
        (self.app / "src" / "a.ts").write_text("// design: p/one\n", encoding="utf-8")
        commit_all(self.app, "init")
        set_origin_main(self.app)
        self.write_projects()
        self.status = self.root / "workspace" / "status" / "p.json"

    def write_projects(self, sources=None):
        sources = sources or [{"repo": "../designs", "artifact": "artifacts/p.html"}]
        (self.root / "workspace" / "projects.json").write_text(json.dumps({"projects": [
            {"key": "p", "label": "P", "appRepo": "../app", "sources": sources}]}), encoding="utf-8")

    def set_status(self, frames: dict):
        self.status.write_text(json.dumps({"groups": [{"id": "g", "label": "G", "order": 0}], "frames": frames},
                                          indent=2) + "\n", encoding="utf-8")

    def frame(self, section="one", gh=None):
        result = ds.check(self.root, "p", gh=gh or FakeGh(False))
        return next(r for r in result["projects"][0]["frames"] if r["id"].endswith("/" + section)), result

    def key(self, section="one"):
        return f"designs:artifacts/p.html#{section}"

    def branch(self, name: str, content="x") -> str:
        git(self.app, "checkout", "-q", "-b", name)
        (self.app / f"{name.replace('/', '_')}.txt").write_text(content, encoding="utf-8")
        sha = commit_all(self.app, name)
        git(self.app, "checkout", "-q", "main")
        return sha


class SectionParserTests(unittest.TestCase):
    def test_nested_sections_belong_to_the_outer_frame(self):
        html = '<section id="a"><h2>A &amp; B</h2><section id="inner"><h2>In</h2></section></section><section id="b"></section>'
        got = ds.section_ranges(html)
        self.assertEqual([(r["section"], r["title"]) for r in got], [("a", "A & B"), ("b", "b")])
        self.assertIn('id="inner"', html[got[0]["start"]:got[0]["end"]])

    def test_comments_scripts_styles_and_templates_are_ignored(self):
        html = ('<!-- <section id="c"></section> -->'
                '<script>var s = "<section id=\'s\'>"; </script><style>/* <section id="st"> */</style>'
                '<template><section id="t"></section></template>'
                '<section id="real"><h2>Real</h2></section>')
        self.assertEqual([r["section"] for r in ds.section_ranges(html)], ["real"])

    def test_sections_without_id_do_not_open_a_frame(self):
        html = '<section><section id="x"></section></section><section id="y"></section>'
        self.assertEqual([r["section"] for r in ds.section_ranges(html)], ["x", "y"])

    def test_single_quoted_and_bare_ids(self):
        html = "<section id='q'></section><section id=bare></section>"
        self.assertEqual([r["section"] for r in ds.section_ranges(html)], ["q", "bare"])


class BuildStateTests(Fixture):
    def build(self, **kw):
        return {"repo": "../app", "branch": "feat/p-one", "recorded": "2026-11-15T00:00:00Z", **kw}

    def test_no_build_is_none_and_legacy_is_check(self):
        self.assertEqual(self.frame()[0]["build"], "none")
        self.set_status({self.key(): {"legacy": "implemented"}})
        self.assertEqual(self.frame()[0]["build"], "check")

    def test_freshly_recorded_branch_is_building_not_implemented(self):
        git(self.app, "branch", "feat/p-one")  # cut from main: tip == sha, ancestor of main
        sha = git(self.app, "rev-parse", "feat/p-one")
        self.set_status({self.key(): {"build": self.build(sha=sha)}})
        self.assertEqual(self.frame()[0]["build"], "building")

    def test_branch_with_commits_not_merged_is_building(self):
        base = git(self.app, "rev-parse", "main")
        self.branch("feat/p-one")
        self.set_status({self.key(): {"build": self.build(sha=base)}})
        self.assertEqual(self.frame()[0]["build"], "building")

    def test_branch_merged_into_default_is_implemented(self):
        base = git(self.app, "rev-parse", "main")
        self.branch("feat/p-one")
        git(self.app, "merge", "-q", "--no-ff", "feat/p-one", "-m", "Merge feature")
        set_origin_main(self.app)
        self.set_status({self.key(): {"build": self.build(sha=base)}})
        row = self.frame()[0]
        self.assertEqual(row["build"], "implemented")
        self.assertIn("Branch merged into main", row["evidence"])

    def test_branch_only_updated_from_main_stays_building(self):
        base = git(self.app, "rev-parse", "main")
        git(self.app, "branch", "feat/p-one")
        (self.app / "main-only.txt").write_text("main", encoding="utf-8")
        commit_all(self.app, "main-only")
        git(self.app, "branch", "-f", "feat/p-one", "main")
        set_origin_main(self.app)
        self.set_status({self.key(): {"build": self.build(sha=base)}})
        self.assertEqual(self.frame()[0]["build"], "building")

    def test_recorded_sha_prefix_equal_to_tip_stays_building(self):
        git(self.app, "branch", "feat/p-one")
        sha = git(self.app, "rev-parse", "feat/p-one")
        self.set_status({self.key(): {"build": self.build(sha=sha[:7])}})
        self.assertEqual(self.frame()[0]["build"], "building")

    def test_missing_sha_never_counts_as_merged(self):
        self.branch("feat/p-one")
        git(self.app, "merge", "-q", "--ff-only", "feat/p-one")
        set_origin_main(self.app)
        self.set_status({self.key(): {"build": self.build()}})
        self.assertEqual(self.frame()[0]["build"], "building")

    def test_squash_merge_found_through_merged_pull_request(self):
        base = git(self.app, "rev-parse", "main")
        self.branch("feat/p-one")
        self.set_status({self.key(): {"build": self.build(sha=base)}})
        prs = [{"headRefName": "feat/p-one", "state": "MERGED", "url": "https://example.test/pr/1"}]
        self.assertEqual(self.frame(gh=FakeGh(True, prs))[0]["build"], "implemented")

    def test_deleted_branch_with_merged_pull_request_is_implemented(self):
        self.set_status({self.key(): {"build": self.build(sha="0" * 40)}})
        prs = [{"headRefName": "feat/p-one", "state": "MERGED", "url": "u"}]
        self.assertEqual(self.frame(gh=FakeGh(True, prs))[0]["build"], "implemented")

    def test_open_pull_request_is_building_even_without_a_local_branch(self):
        self.set_status({self.key(): {"build": self.build(sha="0" * 40)}})
        prs = [{"headRefName": "feat/p-one", "state": "OPEN", "url": "u"}]
        self.assertEqual(self.frame(gh=FakeGh(True, prs))[0]["build"], "building")

    def test_gone_branch_and_no_merged_pr_is_check(self):
        self.set_status({self.key(): {"build": self.build(sha="0" * 40)}})
        self.assertEqual(self.frame()[0]["build"], "check")

    def test_missing_gh_is_reported_and_git_only_answer_is_used(self):
        base = git(self.app, "rev-parse", "main")
        self.branch("feat/p-one")
        self.set_status({self.key(): {"build": self.build(sha=base)}})
        row, result = self.frame(gh=FakeGh(False))
        self.assertEqual(row["build"], "building")
        self.assertIn(ds.GH_MISSING, result["notes"])

    def test_markers_are_suggested(self):
        row, _ = self.frame("one")
        self.assertEqual(row["suggestedMarkers"], ["src/a.ts:1"])
        self.assertEqual(self.frame("two")[0]["suggestedMarkers"], [])

    def test_merged_pr_from_an_earlier_use_of_the_branch_is_not_evidence(self):
        base = git(self.app, "rev-parse", "main")
        tip = self.branch("feat/p-one")
        self.set_status({self.key(): {"build": self.build(sha=base)}})
        old = {"headRefName": "feat/p-one", "state": "MERGED", "url": "u1", "number": 11, "headRefOid": "1" * 40}
        self.assertEqual(self.frame(gh=FakeGh(True, [old]))[0]["build"], "building")
        reopened = {"headRefName": "feat/p-one", "state": "OPEN", "url": "u2", "number": 22, "headRefOid": tip}
        row = self.frame(gh=FakeGh(True, [old, reopened]))[0]
        self.assertEqual((row["build"], row["buildInfo"]["pr"]["number"]), ("building", 22))
        current = dict(old, headRefOid=tip)
        self.assertEqual(self.frame(gh=FakeGh(True, [current]))[0]["build"], "implemented")

    def test_branch_reset_away_from_the_recorded_commit_is_not_merged(self):
        recorded = self.branch("feat/p-one")
        git(self.app, "checkout", "-q", "-b", "side")
        (self.app / "side.txt").write_text("s", encoding="utf-8")
        side = commit_all(self.app, "side")
        git(self.app, "checkout", "-q", "main")
        git(self.app, "merge", "-q", "--no-ff", "side", "-m", "merge side")
        set_origin_main(self.app)
        git(self.app, "branch", "-f", "feat/p-one", side)  # reset onto a merged commit unrelated to the recording
        self.set_status({self.key(): {"build": self.build(sha=recorded)}})
        row = self.frame()[0]
        self.assertEqual(row["build"], "building")
        self.assertEqual(row["buildInfo"]["reason"], "Branch feat/p-one no longer contains the recorded commit; record it again")
        # Still implemented when the recorded commit is in the merged tip's history.
        git(self.app, "checkout", "-q", "feat/p-one")
        git(self.app, "reset", "-q", "--hard", recorded)
        (self.app / "more.txt").write_text("m", encoding="utf-8")
        commit_all(self.app, "more")
        git(self.app, "checkout", "-q", "main")
        git(self.app, "merge", "-q", "--no-ff", "feat/p-one", "-m", "merge feature")
        set_origin_main(self.app)
        self.assertEqual(self.frame()[0]["build"], "implemented")

    def test_marker_without_space_confirms_like_it_is_suggested(self):
        base = git(self.app, "rev-parse", "main")
        self.branch("feat/p-one")
        git(self.app, "merge", "-q", "--no-ff", "feat/p-one", "-m", "merged")
        (self.app / "src" / "a.ts").write_text("// design:p/one\n", encoding="utf-8")
        commit_all(self.app, "marker")
        set_origin_main(self.app)
        self.set_status({self.key(): {"build": self.build(sha=base)}})
        row = self.frame()[0]
        self.assertEqual(row["build"], "implemented")
        self.assertEqual(row["markers"], ["src/a.ts:1"])
        self.assertFalse(row["unconfirmed"])

    def test_inherited_git_dir_does_not_redirect_lookups(self):
        sha = git(self.app, "rev-parse", "main")
        with unittest.mock.patch.dict("os.environ", {"GIT_DIR": str(self.designs / ".git")}):
            self.assertEqual(ds.Git().rev(self.app, "main"), sha)


class DesignStateTests(Fixture):
    def state(self, section="one", gh=None):
        return self.frame(section, gh)[0]["design"]

    def edit(self, text="changed"):
        self.file.write_text(self.file.read_text(encoding="utf-8").replace("body one", text),
                             encoding="utf-8", newline="\n")

    def test_planned_tag_wins(self):
        self.set_status({self.key(): {"planned": True}})
        self.assertEqual(self.state(), "planned")

    def test_identical_to_default_is_published(self):
        self.assertEqual(self.state(), "published")

    def test_crlf_checkout_still_equals_default(self):
        text = self.file.read_text(encoding="utf-8").replace("\n", "\r\n")
        self.file.write_bytes(text.encode("utf-8"))
        self.assertEqual(self.state(), "published")

    def test_edit_marks_only_that_frame_designed(self):
        self.edit()
        self.assertEqual(self.state("one"), "designed")
        self.assertEqual(self.state("two"), "published")

    def test_checkout_behind_main_is_published(self):
        self.edit("newer")
        commit_all(self.designs, "newer")
        set_origin_main(self.designs)
        git(self.designs, "checkout", "-q", "--detach", "HEAD~1")
        self.assertEqual(self.state(), "published")

    def test_working_copy_matching_open_pr_branch_is_publishing(self):
        git(self.designs, "checkout", "-q", "-b", "design/x")
        self.edit("on branch")
        commit_all(self.designs, "branch")
        git(self.designs, "update-ref", "refs/remotes/origin/design/x", "design/x")
        git(self.designs, "checkout", "-q", "main")
        self.edit("on branch")  # same edit, uncommitted on main
        prs = [{"headRefName": "design/x", "state": "OPEN", "url": "https://example.test/pr/2",
                "headRefOid": git(self.designs, "rev-parse", "design/x")}]
        self.assertEqual(self.state(gh=FakeGh(True, prs)), "publishing")
        self.assertEqual(self.state(gh=FakeGh(False)), "designed")

    def test_clean_checkout_on_open_pr_branch_is_publishing(self):
        git(self.designs, "checkout", "-q", "-b", "design/y")
        self.edit("y")
        commit_all(self.designs, "y")
        git(self.designs, "update-ref", "refs/remotes/origin/design/y", "design/y")
        prs = [{"headRefName": "design/y", "state": "OPEN", "url": "u", "headRefOid": git(self.designs, "rev-parse", "design/y")}]
        self.assertEqual(self.state(gh=FakeGh(True, prs)), "publishing")

    def test_section_only_on_open_pr_branch_is_an_extra_publishing_frame(self):
        git(self.designs, "checkout", "-q", "-b", "design/z")
        self.file.write_text(page(("one", "One"), ("two", "Two"), ("three", "Three")), encoding="utf-8", newline="\n")
        commit_all(self.designs, "three")
        git(self.designs, "update-ref", "refs/remotes/origin/design/z", "design/z")
        git(self.designs, "checkout", "-q", "main")
        prs = [{"headRefName": "design/z", "state": "OPEN", "url": "https://example.test/pr/3",
                "headRefOid": git(self.designs, "rev-parse", "design/z")}]
        _, result = self.frame(gh=FakeGh(True, prs))
        extra = result["projects"][0]["extra"]
        self.assertEqual([(e["id"], e["design"], e["branch"]) for e in extra], [("p/three", "publishing", "design/z")])
        # A tracking ref behind the PR's current head (e.g. after a force-push) is not PR evidence.
        stale = [dict(prs[0], number=3, headRefOid="2" * 40)]
        _, result = self.frame(gh=FakeGh(True, stale))
        self.assertEqual(result["projects"][0]["extra"], [])
        self.assertTrue(any("PR #3 content unverified" in n for n in result["notes"]))
        # Without a valid head SHA the content cannot be verified, and neither can a PR with no tracking ref.
        for pr in (dict(prs[0], number=4, headRefOid=None), dict(prs[0], number=5, headRefOid="not-a-sha"),
                   dict(prs[0], number=6, headRefName="design/never-fetched")):
            with self.subTest(pr=pr["number"]):
                _, result = self.frame(gh=FakeGh(True, [pr]))
                self.assertEqual(result["projects"][0]["extra"], [])
                self.assertTrue(any(f"PR #{pr['number']} content unverified" in n for n in result["notes"]))

    def test_pr_only_file_with_non_ascii_name_in_a_directory_source(self):
        self.write_projects([{"repo": "../designs", "dir": "artifacts"}])
        git(self.designs, "checkout", "-q", "-b", "design/cafe")
        (self.designs / "artifacts" / "café.html").write_text(page(("menu", "Menu")), encoding="utf-8", newline="\n")
        commit_all(self.designs, "cafe")
        git(self.designs, "update-ref", "refs/remotes/origin/design/cafe", "design/cafe")
        git(self.designs, "checkout", "-q", "main")
        prs = [{"headRefName": "design/cafe", "state": "OPEN", "url": "u", "headRefOid": git(self.designs, "rev-parse", "design/cafe")}]
        extra = self.frame(gh=FakeGh(True, prs))[1]["projects"][0]["extra"]
        self.assertEqual([e["key"] for e in extra], ["designs:artifacts/café.html#menu"])


class RecordClearTests(Fixture):
    def test_record_round_trip_preserves_other_fields_and_writes_lf(self):
        base = git(self.app, "rev-parse", "main")
        git(self.app, "branch", "feat/p-one")
        self.set_status({self.key("two"): {"group": "g", "planned": True},
                         self.key(): {"group": "g", "legacy": "building"}})
        before = json.loads(self.status.read_text(encoding="utf-8"))
        git(self.app, "remote", "add", "origin", "https://github.com/o/app.git")
        out = ds.record(self.root, "p/one", "feat/p-one", pr="https://github.com/o/app/pull/9", now="2026-11-15T10:00:00Z")
        raw = self.status.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n") and not raw.endswith(b"\n\n"))
        data = json.loads(raw)
        self.assertEqual(data["groups"], before["groups"])
        self.assertEqual(data["frames"][self.key("two")], before["frames"][self.key("two")])
        entry = data["frames"][self.key()]
        self.assertEqual(entry["group"], "g")
        self.assertNotIn("legacy", entry)
        self.assertEqual(entry["build"], {"repo": "../app", "branch": "feat/p-one", "sha": base,
                                          "pr": "https://github.com/o/app/pull/9", "recorded": "2026-11-15T10:00:00Z"})
        self.assertEqual(out["key"], self.key())
        self.assertTrue(raw.decode("utf-8").startswith('{\n  "groups"'))
        # Round trip: the recorded fresh branch reads as Building.
        self.assertEqual(self.frame()[0]["build"], "building")
        ds.clear(self.root, self.key())
        data = json.loads(self.status.read_bytes())
        self.assertEqual(data["frames"][self.key()], {"group": "g"})
        self.assertEqual(data["frames"][self.key("two")], before["frames"][self.key("two")])

    def test_record_without_existing_branch_omits_sha_and_creates_the_file(self):
        ds.record(self.root, "p/two", "feat/not-yet", now="2026-11-15T10:00:00Z", allow_missing=True)
        entry = json.loads(self.status.read_text(encoding="utf-8"))["frames"][self.key("two")]
        self.assertEqual(entry["build"], {"repo": "../app", "branch": "feat/not-yet", "recorded": "2026-11-15T10:00:00Z"})

    def test_record_refuses_missing_branch(self):
        with self.assertRaisesRegex(SystemExit, "does not exist"):
            ds.record(self.root, "p/one", "feat/missing")
        self.assertFalse(self.status.exists())

    def test_record_rejects_invalid_branch_and_pr(self):
        for branch in ("-bad", "feature/../other", "bad branch", "bad;cmd"):
            with self.subTest(branch=branch), self.assertRaises(SystemExit):
                ds.record(self.root, "p/one", branch, allow_missing=True)
        git(self.app, "remote", "add", "origin", "https://github.com/o/app.git")
        # Only references `check` can resolve: a number, or a PR URL on the app's own GitHub origin.
        for pr in ("http://example.test/1", "file:///tmp/pr", "abc", "0", "https://example.test/pr/9",
                   "https://github.com/other/app/pull/9", "https://github.com/o/app/issues/9"):
            with self.subTest(pr=pr), self.assertRaises(SystemExit):
                ds.record(self.root, "p/one", "feat/p-one", pr=pr, allow_missing=True)
        self.assertFalse(self.status.exists())
        for pr in ("9", "https://github.com/O/App/pull/9/"):
            with self.subTest(pr=pr):
                self.assertEqual(ds.record(self.root, "p/one", "feat/p-one", pr=pr, allow_missing=True)["build"]["pr"], pr)

    def test_concurrent_writers_wait_for_the_status_lock(self):
        with ds.status_lock(self.root, "p"):
            with self.assertRaisesRegex(SystemExit, "another process"):
                with ds.status_lock(self.root, "p", timeout=0.2):
                    pass
        with ds.status_lock(self.root, "p", timeout=0.2):  # released, and no file left in the workspace
            pass
        self.assertEqual([p.name for p in (self.root / "workspace" / "status").iterdir() if p.name.endswith(".lock")], [])

    def test_lock_held_by_another_process_blocks_and_frees_on_exit(self):
        script = ("import sys, time; sys.path.insert(0, sys.argv[1]); import design_status as ds; from pathlib import Path\n"
                  "with ds.status_lock(Path(sys.argv[2]), 'p'):\n    print('held', flush=True); time.sleep(30)\n")
        child = subprocess.Popen([sys.executable, "-c", script, str(Path(ds.__file__).parent), str(self.root)],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            with self.assertRaisesRegex(SystemExit, "another process"):
                with ds.status_lock(self.root, "p", timeout=0.3):
                    pass
        finally:
            child.kill()
            child.wait()
            child.stdout.close()
        with ds.status_lock(self.root, "p", timeout=5):  # the system released the dead process's lock
            pass

    def test_record_keeps_designer_base_sha_on_other_frames(self):
        base = git(self.app, "rev-parse", "main")
        self.set_status({self.key("two"): {"build": {"repo": "../app", "branch": "feat/b", "sha": base,
                                                     "baseSha": base, "recorded": "r"}}})
        ds.record(self.root, "p/one", "feat/p-one", allow_missing=True)
        saved = json.loads(self.status.read_bytes())["frames"][self.key("two")]["build"]
        self.assertEqual(saved["baseSha"], base)

    def test_record_rejects_repo_other_than_app_repo(self):
        with self.assertRaisesRegex(SystemExit, "appRepo"):
            ds.record(self.root, "p/one", "feat/p-one", repo="../designs", allow_missing=True)
        self.assertFalse(self.status.exists())

    def test_clear_removes_empty_entry_and_legacy(self):
        self.set_status({self.key(): {"legacy": "implemented"}})
        ds.clear(self.root, "p/one")
        self.assertEqual(json.loads(self.status.read_text(encoding="utf-8"))["frames"], {})

    def test_full_key_works(self):
        ds.record(self.root, self.key("two"), "feat/q", allow_missing=True)
        self.assertIn(self.key("two"), json.loads(self.status.read_text(encoding="utf-8"))["frames"])

    def test_ambiguous_short_id_lists_candidates(self):
        other = init_repo(Path(self._tmp.name) / "more")
        (other / "a").mkdir()
        (other / "a" / "q.html").write_text(page(("one", "Dup")), encoding="utf-8")
        commit_all(other, "q")
        self.write_projects([{"repo": "../designs", "artifact": "artifacts/p.html"},
                             {"repo": "../more", "artifact": "a/q.html"}])
        with self.assertRaises(SystemExit) as ctx:
            ds.record(self.root, "p/one", "feat/x")
        msg = str(ctx.exception)
        self.assertIn("Ambiguous", msg)
        self.assertIn("designs:artifacts/p.html#one", msg)
        self.assertIn("more:a/q.html#one", msg)
        self.assertFalse(self.status.exists())
        ds.record(self.root, "more:a/q.html#one", "feat/x", allow_missing=True)  # full key resolves it
        self.assertIn("more:a/q.html#one", json.loads(self.status.read_text(encoding="utf-8"))["frames"])

    def test_unknown_frame_lists_candidates(self):
        with self.assertRaises(SystemExit) as ctx:
            ds.record(self.root, "p/on", "feat/x")
        self.assertIn("p/one", str(ctx.exception))

    def test_check_never_writes(self):
        self.set_status({self.key(): {"legacy": "building"}})
        before = self.status.read_bytes()
        ds.check(self.root, gh=FakeGh(False))
        self.assertEqual(self.status.read_bytes(), before)

    def test_cli_json_and_markdown(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ds.main(["check", "--designer", str(self.root), "--json"], gh=FakeGh(False))
        data = json.loads(out.getvalue())
        self.assertEqual(data["projects"][0]["key"], "p")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ds.main(["check", "--designer", str(self.root), "--project", "p"], gh=FakeGh(False))
        self.assertIn("| p/one | published |", out.getvalue())
        self.assertIn("GitHub CLI not available", out.getvalue())


if __name__ == "__main__":
    unittest.main()
