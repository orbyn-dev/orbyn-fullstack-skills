"""Tests for design_status.py. Run: python3 -m unittest discover -s skills/orbyn-design-status/scripts"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_status as ds  # noqa: E402


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                       capture_output=True, text=True, check=True)
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
        prs = [{"headRefName": "design/x", "state": "OPEN", "url": "https://example.test/pr/2"}]
        self.assertEqual(self.state(gh=FakeGh(True, prs)), "publishing")
        self.assertEqual(self.state(gh=FakeGh(False)), "designed")

    def test_clean_checkout_on_open_pr_branch_is_publishing(self):
        git(self.designs, "checkout", "-q", "-b", "design/y")
        self.edit("y")
        commit_all(self.designs, "y")
        git(self.designs, "update-ref", "refs/remotes/origin/design/y", "design/y")
        prs = [{"headRefName": "design/y", "state": "OPEN", "url": "u"}]
        self.assertEqual(self.state(gh=FakeGh(True, prs)), "publishing")

    def test_section_only_on_open_pr_branch_is_an_extra_publishing_frame(self):
        git(self.designs, "checkout", "-q", "-b", "design/z")
        self.file.write_text(page(("one", "One"), ("two", "Two"), ("three", "Three")), encoding="utf-8", newline="\n")
        commit_all(self.designs, "three")
        git(self.designs, "update-ref", "refs/remotes/origin/design/z", "design/z")
        git(self.designs, "checkout", "-q", "main")
        prs = [{"headRefName": "design/z", "state": "OPEN", "url": "https://example.test/pr/3"}]
        _, result = self.frame(gh=FakeGh(True, prs))
        extra = result["projects"][0]["extra"]
        self.assertEqual([(e["id"], e["design"], e["branch"]) for e in extra], [("p/three", "publishing", "design/z")])


class RecordClearTests(Fixture):
    def test_record_round_trip_preserves_other_fields_and_writes_lf(self):
        base = git(self.app, "rev-parse", "main")
        git(self.app, "branch", "feat/p-one")
        self.set_status({self.key("two"): {"group": "g", "planned": True},
                         self.key(): {"group": "g", "legacy": "building"}})
        before = json.loads(self.status.read_text(encoding="utf-8"))
        out = ds.record(self.root, "p/one", "feat/p-one", pr="https://example.test/pr/9", now="2026-11-15T10:00:00Z")
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
                                          "pr": "https://example.test/pr/9", "recorded": "2026-11-15T10:00:00Z"})
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
        for pr in ("http://example.test/1", "file:///tmp/pr", "abc"):
            with self.subTest(pr=pr), self.assertRaises(SystemExit):
                ds.record(self.root, "p/one", "feat/p-one", pr=pr, allow_missing=True)
        self.assertFalse(self.status.exists())

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
