#!/usr/bin/env python3
"""Check and record design and build status for Orbyn Designer frames.

Reads: <Designer root>/workspace/projects.json and workspace/status/<project>.json, the
source design repos and the app repos (read-only git commands, `gh pr list` when signed in).
Writes: only `record` and `clear`, and only <Designer root>/workspace/status/<project>.json.
Python 3 standard library only. Rules follow the Designer status file v2 contract.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

GH_MISSING = "GitHub CLI not available: pull request lookups skipped"


# ---------------------------------------------------------------- section parser

def _blank(match: re.Match) -> str:
    return " " * len(match.group(0))  # keeps offsets stable


def section_ranges(html: str) -> list[dict]:
    """Top-level `<section id>` elements, found by balancing section tags.

    Comments, scripts, styles and templates are ignored. Same rule as sectionRanges()
    in Orbyn Designer's server/status/frames.ts. Title is the first `<h2>` text, else the id.
    """
    text = re.sub(r"<!--.*?-->", _blank, html, flags=re.S)
    text = re.sub(r"<(script|style|template)\b.*?</\1\s*>", _blank, text, flags=re.S | re.I)
    out: list[dict] = []
    stack: list[bool] = []
    open_: dict | None = None
    for m in re.finditer(r"<(/?)section\b([^>]*)>", text, flags=re.I):
        if not m.group(1):
            idm = re.search(r"\bid\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", m.group(2), flags=re.I)
            sid = (idm.group(1) or idm.group(2) or idm.group(3) or "") if idm else ""
            if sid and not any(stack):
                open_ = {"id": sid, "start": m.start(), "from": m.end()}
                was = re.search(r'''\sdata-was\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''', m[2], re.I)
                if was:
                    open_["was"] = next(v for v in was.groups() if v is not None)
            stack.append(bool(sid))
        elif stack:
            had_id = stack.pop()
            if had_id and open_ and not any(stack):
                h2 = re.search(r"<h2\b[^>]*>(.*?)</h2\s*>", html[open_["from"]:m.start()], flags=re.S | re.I)
                title = ""
                if h2:
                    title = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", h2.group(1)))).strip()
                out.append({"section": open_["id"], "title": title or open_["id"],
                            **({"was": open_["was"]} if open_.get("was") else {}),
                            "start": open_["start"], "end": m.end()})
                open_ = None
    return out


def norm(text: str) -> str:
    """Designer normalizeSection: canonical tags, significant raw/pre contents."""
    def tag(raw):
        if raw.startswith("</"):
            return re.sub(r"\s+>", ">", raw.lower())
        opening = re.match(r"<([\w:-]+)", raw)
        if not opening:
            return raw
        inside = re.sub(r"/?\s*>$", "", raw[opening.end():])
        attrs = []
        for m in re.finditer(r'''([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''', inside):
            attrs.append((m[1].lower(), next((v for v in m.groups()[1:] if v is not None), "")))
        attrs.sort(key=lambda a: a[0])
        return "<" + opening[1].lower() + "".join(" " + k + "=" + json.dumps(v, ensure_ascii=False) for k, v in attrs) + ">"
    pattern = r'''<!--[\s\S]*?(?:-->|$)|<(script|style|pre|textarea)\b(?:"[^"]*"|'[^']*'|[^'">])*?>[\s\S]*?</\1\s*>|</?[a-z][\w:-]*\b(?:"[^"]*"|'[^']*'|[^'">])*?>|[^<]+|<'''
    visible = [m[0] for m in re.finditer(pattern, text.replace("\r\n", "\n"), re.I)
               if not m[0].startswith("<!--")]
    block = re.compile(r"^</?(?:address|article|aside|blockquote|div|dl|dt|dd|fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|th|thead|tr|ul)\b", re.I)
    out = []
    for index, token in enumerate(visible):
        raw = re.fullmatch(r'''(<(script|style|pre|textarea)\b(?:"[^"]*"|'[^']*'|[^'">])*?>)([\s\S]*?)(</\2\s*>)''', token, re.I)
        if raw:
            out.append(tag(raw[1]) + raw[3] + tag(raw[4]))
        elif token.startswith("<"):
            out.append(tag(token))
        else:
            value = re.sub(r"\s+", " ", token)
            if index == 0 or block.match(visible[index - 1]):
                value = value.lstrip()
            if index == len(visible) - 1 or block.match(visible[index + 1]):
                value = value.rstrip()
            out.append(value)
    return "".join(out)


def section_html(html: str, section: str) -> str | None:
    for r in section_ranges(html):
        if r["section"] == section:
            return norm(html[r["start"]:r["end"]])
    return None


# ---------------------------------------------------------------- git and gh

# `git rev-parse --local-env-vars`: an inherited value (as in a Git hook) would override `git -C`.
LOCAL_GIT_ENV = frozenset({"GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                           "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
                           "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
                           "GIT_INTERNAL_SUPER_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR"})


def child_env(**extra: str) -> dict:
    """The inherited environment without repository-location variables or a `gh` repository or host override.

    Origins are always github.com (see `github_origin`), so `GH_HOST` must not send `gh` elsewhere.
    """
    env = {k: v for k, v in os.environ.items() if k not in LOCAL_GIT_ENV and k not in ("GH_REPO", "GH_HOST")}
    env.update(extra)
    return env


class Git:
    """Read-only git with a per-run cache. Callers only pass read commands."""

    def __init__(self) -> None:
        self.cache: dict[tuple, tuple[int, str]] = {}

    def run(self, repo, *args: str) -> tuple[int, str]:
        key = (str(repo), *args)
        if key not in self.cache:
            env = child_env(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GH_PROMPT_DISABLED="1")
            try:
                p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env, timeout=60)
                self.cache[key] = (p.returncode, p.stdout.decode("utf-8", "replace"))
            except (OSError, subprocess.SubprocessError):
                self.cache[key] = (127, "")
        return self.cache[key]

    def rev(self, repo, ref: str) -> str | None:
        rc, out = self.run(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
        return out.strip() if rc == 0 and out.strip() else None

    def is_ancestor(self, repo, a: str, b: str) -> bool:
        return self.run(repo, "merge-base", "--is-ancestor", a, b)[0] == 0

    def show(self, repo, ref: str, file: str) -> str | None:
        rc, out = self.run(repo, "show", f"{ref}:{file}")
        return out if rc == 0 else None

    def default_ref(self, repo, branch=None, gh=None) -> str | None:
        _, listing = self.run(repo, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/")
        available = listing.splitlines()
        configured = "refs/remotes/origin/" + branch if branch and valid_branch(branch) else None
        if configured in available:
            return configured
        rc, out = self.run(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
        ref = out.strip()
        if rc == 0 and ref.startswith("refs/remotes/origin/") and valid_branch(ref[20:]) and ref in available:
            return ref
        name = gh.default_branch(repo) if gh else None
        if name and valid_branch(name) and "refs/remotes/origin/" + name in available:
            return "refs/remotes/origin/" + name
        for ref in ("refs/remotes/origin/main", "refs/remotes/origin/master"):
            if ref in available:
                return ref
        return None

    def branch_tip(self, repo, branch: str) -> str | None:
        if not valid_branch(branch):
            return None
        return self.rev(repo, "refs/heads/" + branch) or self.rev(repo, "refs/remotes/origin/" + branch)

    def current_branch(self, repo) -> str | None:
        rc, out = self.run(repo, "symbolic-ref", "--short", "-q", "HEAD")
        return out.strip() if rc == 0 and out.strip() else None

    def file_clean(self, repo, file: str) -> bool:
        rc, out = self.run(repo, "status", "--porcelain", "--", file)
        return rc == 0 and not out.strip()


def valid_branch(branch: str) -> bool:
    return bool(re.fullmatch(r"[\w./-]+", branch, re.ASCII)) and not (
        branch.startswith(("-", "/")) or branch.endswith("/") or ".." in branch)


def split_head(head: str) -> dict | None:
    """Designer splitHead: validate the optional fork owner independently."""
    parts = head.split(":")
    if len(parts) == 1:
        return {"branch": head} if valid_branch(head) else None
    if len(parts) == 2 and re.fullmatch(r"[\w-]+", parts[0], re.ASCII) and valid_branch(parts[1]):
        return {"owner": parts[0], "branch": parts[1]}
    return None


def github_origin(url: str) -> str | None:
    m = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com(?::\d+)?/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url.strip(), re.I)
    return f"{m[1]}/{m[2]}" if m else None


def pr_number(pr, origin: str | None) -> str | None:
    """A positive PR number, or a PR URL on `origin`'s GitHub repository; else None. Shared by `record` and `check`."""
    text = str(pr).strip()
    if re.fullmatch(r"[1-9]\d*", text):
        return text
    m = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/([1-9]\d*)/?", text, re.I)
    return m[2] if m and origin and m[1].lower() == origin.lower() else None


def same_sha(a: str, b: str) -> bool:
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def valid_sha(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{7,40}", value, re.I))


def pull_ref(raw: dict) -> dict:
    if "branch" in raw:
        return raw
    out = {"number": raw.get("number", 0), "url": raw.get("url", ""), "title": raw.get("title", ""),
           "branch": raw.get("headRefName", ""), "state": raw.get("state", "OPEN").lower()}
    if valid_sha(raw.get("headRefOid")):
        out["headSha"] = raw["headRefOid"]
    if raw.get("isCrossRepository"):
        out.update(branch=f"{raw.get('headRepositoryOwner', {}).get('login', '')}:{out['branch']}", fork=True)
    if raw.get("mergeCommit"):
        out["mergeCommit"] = raw["mergeCommit"]["oid"]
    if raw.get("mergedAt"):
        out["mergedAt"] = raw["mergedAt"]
    return out


class Gh:
    """`gh pr list` when the GitHub CLI is installed and signed in. Tests replace this class."""

    def __init__(self) -> None:
        self._exe = shutil.which("gh")
        self._ok: bool | None = None

    @property
    def available(self) -> bool:
        if self._ok is None:
            try:
                self._ok = bool(self._exe) and subprocess.run(
                    [self._exe, "auth", "status"], capture_output=True, timeout=30).returncode == 0
            except (OSError, subprocess.SubprocessError):
                self._ok = False
        return self._ok

    def query(self, repo, *args):
        if not self.available:
            return None
        try:
            p = subprocess.run([self._exe, *args], capture_output=True, cwd=str(repo), timeout=60,
                               env=child_env(GH_PROMPT_DISABLED="1"))
            return json.loads(p.stdout.decode("utf-8", "replace")) if p.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def origin(self, repo):
        return github_origin(Git().run(repo, "remote", "get-url", "origin")[1])

    def default_branch(self, repo):
        origin = self.origin(repo)
        value = self.query(repo, "repo", "view", "github.com/" + origin, "--json", "defaultBranchRef") if origin else None
        ref = value.get("defaultBranchRef") if isinstance(value, dict) else None
        return ref.get("name") if isinstance(ref, dict) else None

    def prs(self, repo, head=None, state="all", pr=None):
        origin = self.origin(repo)
        if not origin:
            return None
        fields = "number,url,title,headRefName,headRefOid,state,mergedAt,mergeCommit,isCrossRepository,headRepositoryOwner"
        if pr is not None:
            number = pr_number(pr, origin)
            if not number:
                return None
            raw = self.query(repo, "pr", "view", number, "--repo", "github.com/" + origin, "--json", fields)
            return [pull_ref(raw)] if isinstance(raw, dict) else None
        if state == "open":
            result = []
            page = 1
            while True:
                raw = self.query(repo, "api", "--hostname", "github.com", f"repos/{origin}/pulls?state=open&per_page=100&page={page}")
                if not isinstance(raw, list):
                    return None
                for p in raw:
                    fork = not p["head"].get("repo") or p["head"]["repo"]["full_name"].lower() != p["base"]["repo"]["full_name"].lower()
                    result.append(pull_ref({"number": p["number"], "url": p["html_url"], "title": p["title"],
                        "headRefName": p["head"]["ref"], "headRefOid": p["head"].get("sha"),
                        "state": p["state"].upper(), "isCrossRepository": fork,
                        "headRepositoryOwner": p["head"]["user"], "mergedAt": p.get("merged_at"),
                        "mergeCommit": {"oid": p["merge_commit_sha"]} if p.get("merge_commit_sha") else None}))
                if len(raw) < 100:
                    return result
                page += 1
        if head and split_head(head) and split_head(head).get("owner"):
            return None  # `gh pr list --head` takes a branch only; a fork head needs its PR number.
        args = ["pr", "list", "--state", state, "--repo", "github.com/" + origin, "--json", fields, "--limit", "100"]
        if head:
            args += ["--head", head]
        raw = self.query(repo, *args)
        return [pull_ref(p) for p in raw if not head or pull_ref(p)["branch"] == head] if isinstance(raw, list) else None

    def runs(self, repo, workflow, branch):
        origin = self.origin(repo)
        if not origin:
            return None  # Deployment evidence stays unverified rather than read from another repository.
        return self.query(repo, "run", "list", "--repo", "github.com/" + origin, "--workflow", workflow, "--branch", branch,
                          "--status", "success", "--json", "headSha", "--limit", "100")


# ---------------------------------------------------------------- projects and frames

@dataclass
class Frame:
    key: str
    project: str
    section: str
    title: str
    file: str
    repo: Path  # resolved source repo
    was: str | None = None


def load_projects(root: Path) -> list[dict]:
    path = root / "workspace" / "projects.json"
    if not path.is_file():
        raise SystemExit(f"Not a Designer root (missing workspace/projects.json): {root}")
    return json.loads(path.read_text(encoding="utf-8"))["projects"]


def get_project(root: Path, key: str) -> dict:
    for p in load_projects(root):
        if p["key"] == key:
            return p
    raise SystemExit(f"Unknown project: {key}")


def status_path(root: Path, key: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]+", key):
        raise SystemExit(f"Bad project key: {key}")
    return root / "workspace" / "status" / f"{key}.json"


def migrate_frame(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    if isinstance(raw.get("group"), str):
        out["group"] = raw["group"]
    if raw.get("planned") is True or raw.get("status") == "planned":
        out["planned"] = True
    b = raw.get("build")
    if isinstance(b, dict) and isinstance(b.get("repo"), str) and isinstance(b.get("branch"), str):
        out["build"] = {"repo": b["repo"], "branch": b["branch"],
                        "recorded": b.get("recorded") if isinstance(b.get("recorded"), str) else ""}
        for key in ("sha", "pr"):
            if isinstance(b.get(key), str) or (key == "pr" and type(b.get(key)) in (int, float)):
                out["build"][key] = b[key]
        if valid_sha(b.get("baseSha")):  # Designer records it; dropping it here would erase it on the next write.
            out["build"]["baseSha"] = b["baseSha"]
        if isinstance(b.get("screens"), list):
            out["build"]["screens"] = [s for s in b["screens"] if isinstance(s, str)]
    evidence = raw.get("evidence")
    markers = raw.get("markers") if isinstance(raw.get("markers"), list) else evidence.get("paths", []) if isinstance(evidence, dict) else []
    clean = [m for m in markers if isinstance(m, str)] if isinstance(markers, list) else []
    if clean:
        out["markers"] = clean
    legacy = raw.get("legacy") if raw.get("legacy") is not None else raw.get("status")
    if "build" not in out and legacy in ("building", "implemented"):
        out["legacy"] = legacy
    return out


def load_status(root: Path, key: str) -> dict:
    path = status_path(root, key)
    if not path.is_file():
        return {"groups": [], "frames": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data = data if isinstance(data, dict) else {}
    frames = data.get("frames") if isinstance(data.get("frames"), dict) else {}
    return {"groups": data.get("groups") if isinstance(data.get("groups"), list) else [],
            "frames": {k: entry for k, v in frames.items() if (entry := migrate_frame(v))}}


def write_status(root: Path, key: str, data: dict) -> None:
    """2-space JSON, UTF-8, LF, trailing newline, written atomically inside workspace/status."""
    path = status_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".status-", suffix=".tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
    os.replace(tmp, path)


def source_files(repo: Path, source: dict) -> list[str]:
    files: list[str] = []
    if source.get("artifact"):
        files.append(source["artifact"])
    if source.get("dir"):
        folder = repo / source["dir"]
        if folder.is_dir():
            files += sorted(f"{source['dir'].rstrip('/')}/{p.name}" for p in folder.iterdir()
                            if p.is_file() and p.suffix.lower() == ".html")
    return files


def frames_of(root: Path, project: dict) -> list[Frame]:
    out: list[Frame] = []
    for source in project.get("sources", []):
        repo = (root / source["repo"]).resolve()
        for rel in source_files(repo, source):
            path = repo / rel
            if not path.is_file():
                continue
            file = Path(os.path.relpath(path, repo)).as_posix()
            html = path.read_text(encoding="utf-8", errors="replace")
            for r in section_ranges(html):
                out.append(Frame(f"{repo.name}:{file}#{r['section']}", project["key"], r["section"],
                                 r["title"], file, repo, r.get("was")))
    return out


def carry_renames(saved: dict, frames: list[Frame]) -> dict:
    result = dict(saved)
    live = {f.key for f in frames}
    aliases = [(f.key, f.key.rsplit("#", 1)[0] + "#" + f.was) for f in frames if f.was]
    for key, old in aliases:
        if (old in live or key in saved or old not in saved
                or sum(o == old for _, o in aliases) != 1 or sum(k == key for k, _ in aliases) != 1):
            continue
        result[key] = saved[old]
        result.pop(old, None)
    return result


def resolve_frame(root: Path, ident: str) -> tuple[dict, Frame]:
    """A short id `<project>/<section>` or a full key. Errors list the candidates."""
    projects = load_projects(root)
    if ":" in ident and "#" in ident:
        hits = [(p, f) for p in projects for f in frames_of(root, p) if f.key == ident]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise SystemExit(f"Ambiguous frame {ident}; it is in projects: " + ", ".join(p["key"] for p, _ in hits))
        raise SystemExit(f"Frame not found: {ident}")
    if "/" not in ident:
        raise SystemExit(f"Use <project>/<section> or a full key: {ident}")
    pkey, section = ident.split("/", 1)
    project = get_project(root, pkey)
    frames = frames_of(root, project)
    hits = [f for f in frames if f.section == section]
    if len(hits) == 1:
        return project, hits[0]
    if hits:
        raise SystemExit(f"Ambiguous frame {ident}; use a full key:\n" + "\n".join(f"  {f.key}" for f in hits))
    near = [f for f in frames if section in f.section][:10] or frames[:10]
    raise SystemExit(f"Frame not found: {ident}. Candidates:\n" + "\n".join(f"  {pkey}/{f.section}" for f in near))


# ---------------------------------------------------------------- build state

def build_state(frame: dict, facts: dict | None = None) -> dict:
    """Pure Designer buildState (server/state/build.ts); null pulls mean unverified.

    A pull request counts only when its head commit is the branch tip (or either is unknown), so a merged PR
    from an earlier use of a reused branch name cannot mark newer work implemented. An open PR comes first.
    """
    build = frame.get("build")
    if not build:
        return {"state": "check", "reason": f"Tagged {'Implemented' if frame['legacy'] == 'implemented' else 'Building'} by hand, with no branch. Record a branch or clear it."} if frame.get("legacy") else {"state": "none"}
    facts = facts or {}
    pulls = facts.get("pulls")
    tip, sha = facts.get("tip"), build.get("sha")
    current = lambda p: not p.get("headSha") or not tip or same_sha(p["headSha"], tip)  # noqa: E731
    merged = next((p for p in pulls or [] if p["state"] == "merged" and current(p)), None)
    opened = next((p for p in pulls or [] if p["state"] == "open" and current(p)), None)
    if opened:
        return {"state": "building", "reason": "Pull request open", "pr": opened}
    if tip and facts.get("diverged"):
        # The branch was reset or rewritten: neither its commits nor a PR merged from its new tip describe the
        # recorded build, so no merge evidence is accepted until the branch is recorded again.
        return {"state": "building", "reason": f"Branch {build['branch']} no longer contains the recorded commit; record it again"}
    own = bool(tip and sha and not same_sha(tip, sha) and facts.get("onDefault") and facts.get("ownCommits"))
    if facts.get("recordedMerged") or own or merged:
        out = {"state": "implemented", "reason": "Branch merged into main" if facts.get("recordedMerged") or own else "Pull request merged"}
        if merged:
            out["pr"] = merged
            if merged.get("mergedAt"):
                out["merged"] = merged["mergedAt"]
        return out
    if tip:
        return {"state": "building", "reason": "Branch not merged yet"}
    closed = next((p for p in pulls or [] if p["state"] == "closed"), None)
    if closed:
        return {"state": "check", "reason": f"Branch gone and pull request #{closed['number']} closed without a merge", "pr": closed}
    return {"state": "check", "reason": "unverified" if pulls is None else "Branch deleted without a merge"}


def has_own_commits(commits: str, first_parent: str) -> bool:
    return bool(commits.strip() and first_parent.strip() and set(commits.split()) - set(first_parent.split()))


def build_facts(git: Git, gh, repo: Path, build: dict, default_branch=None) -> dict:
    branch = build["branch"]
    if build.get("pr") is not None:
        pulls = gh.prs(repo, pr=build["pr"]) if gh.available else None
    else:
        pulls = gh.prs(repo, head=branch, state="all") if gh.available else None
    pulls = [pull_ref(p) for p in pulls] if pulls is not None else None
    current = pulls[0]["branch"] if build.get("pr") is not None and pulls else branch
    head = split_head(current)
    tip = git.branch_tip(repo, current) if head and not head.get("owner") and not (pulls and pulls[0].get("fork")) else None
    base = git.default_ref(repo, default_branch, gh)
    sha = build.get("sha", "")
    # Commits after the recorded one describe that build only when the recorded commit is still in the
    # branch's history; a branch reset elsewhere is not merge evidence for it.
    in_history = bool(tip and valid_sha(sha) and git.is_ancestor(repo, sha, tip))
    commits = git.run(repo, "rev-list", f"{sha}..{tip}")[1] if in_history and base else ""
    first = git.run(repo, "rev-list", "--first-parent", base)[1] if sha and base else ""
    # A recorded commit identical to the tip (or a deleted branch) is merged when it reaches the default
    # branch through a non-first-parent edge, or was cut from a distinct recorded base. Designer's buildFacts.
    recorded_on_default = bool(valid_sha(sha) and base and git.is_ancestor(repo, sha, base))
    unchanged_or_deleted = bool(sha and (not tip or same_sha(tip, sha)))
    non_first_parent = bool(recorded_on_default and unchanged_or_deleted and first.strip()
                            and not any(same_sha(item, sha) for item in first.split()))
    base_sha = build.get("baseSha")
    distinct_base = bool(recorded_on_default and unchanged_or_deleted and valid_sha(base_sha) and valid_sha(sha)
                         and not same_sha(base_sha, sha) and git.is_ancestor(repo, base_sha, sha))
    return {"tip": tip, "onDefault": bool(tip and base and git.is_ancestor(repo, tip, base)),
            "ownCommits": has_own_commits(commits, first), "recordedMerged": non_first_parent or distinct_base,
            "diverged": bool(tip and valid_sha(sha) and not in_history),
            "pulls": pulls, **({"renamedBranch": current} if current != branch else {})}


def design_state(section: str, planned: bool, versions: dict) -> dict:
    """Pure Designer designState rules 1 to 5; maps are JSON objects."""
    v = versions
    extra = {"sharedStylesChanged": True} if v.get("sharedStylesChanged") else {}
    if planned:
        return {"state": "planned", **extra}
    work = v["work"].get(section)
    if (work is not None and v["main"].get(section) == work) or (not v["dirty"] and v["behind"] and section in v["main"]):
        return {"state": "published", **({"sha": v["mainSha"]} if v.get("mainSha") else {}),
                **({"date": v["mainDate"]} if v.get("mainDate") else {}), **extra}
    match = next((p for p in v["pulls"] if work is not None and p["sections"].get(section) == work), None)
    if match is None and not v["dirty"]:
        match = next((p for p in v["pulls"] if p["pr"]["branch"] == v.get("branch")), None)
    where = {"dirty": True} if v["dirty"] else {}
    if match:
        return {"state": "publishing", "branch": match["pr"]["branch"], "pr": match["pr"], **where, **extra}
    return {"state": "designed", **({"branch": v["branch"]} if v.get("branch") else {}), **where, **extra}


def section_map(html: str | None) -> dict:
    return {r["section"]: norm(html[r["start"]:r["end"]]) for r in section_ranges(html or "")}


def current_pull_ref(git: Git, repo: Path, pr: dict) -> str | None:
    """The commit a same-repository PR's remote-tracking ref points at, only when it is the PR's head commit.

    After a force-push the local ref keeps the old commit until the next fetch; its content is not PR evidence.
    A PR without a valid head SHA cannot be verified, so its content is never read.
    The commit, not the ref name, is returned so a fetch during the check cannot change what is read next.
    """
    if pr.get("fork") or not valid_branch(pr.get("branch", "")) or not valid_sha(pr.get("headSha")):
        return None
    tip = git.rev(repo, "refs/remotes/origin/" + pr["branch"])
    if not tip or not same_sha(tip, pr["headSha"]):
        return None
    return tip


def gather_design(git: Git, gh, repo: Path, file: str, section: str, planned: bool,
                  pulls: list[dict], default_branch=None) -> dict:
    path = repo / file
    work = section_map(path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None)
    base = git.default_ref(repo, default_branch, gh)
    main = section_map(git.show(repo, base, file) if base else None)
    versions = {"work": work, "main": main, "pulls": [], "dirty": not git.file_clean(repo, file),
                "behind": bool(base and git.is_ancestor(repo, "HEAD", base)), "branch": git.current_branch(repo)}
    if base:
        log = git.run(repo, "log", "-1", "--format=%h %cI", base, "--", file)[1].strip().split()
        if len(log) == 2:
            versions.update(mainSha=log[0], mainDate=log[1])
        folder = str(Path(file).parent).replace("\\", "/")
        changed = git.run(repo, "diff", "--name-only", "-z", base, "--", folder)[1]
        changed += "\0" + git.run(repo, "ls-files", "--others", "--exclude-standard", "-z", "--", folder)[1]
        versions["sharedStylesChanged"] = any(Path(p).suffix.lower() in (".css", ".js") and str(Path(p).parent).replace("\\", "/") == folder for p in changed.split("\0") if p)
    for raw in pulls:
        pr = pull_ref(raw)
        ref = current_pull_ref(git, repo, pr)
        if not ref:
            continue
        changed = git.run(repo, "diff", "--name-only", "-z", f"{base}...{ref}", "--")[1] if base else ""
        if file not in changed.split("\0"):
            continue
        html = git.show(repo, ref, file)
        if html is not None:
            versions["pulls"].append({"pr": pr, "sections": section_map(html)})
    return design_state(section, planned, versions)


def extra_frames(git: Git, root: Path, project: dict, known: set[str], prs_by_repo: dict, gh=None,
                 notes: list[str] | None = None) -> list[dict]:
    """Sections that exist only on an open pull request branch whose local ref matches the PR head."""
    out: dict[str, dict] = {}
    for source in project.get("sources", []):
        repo = (root / source["repo"]).resolve()
        for pr in prs_by_repo.get(str(repo), []):
            pr = pull_ref(pr)
            branch = pr.get("branch")
            ref = current_pull_ref(git, repo, pr)
            if not ref:
                # Missing ref, missing or invalid head SHA, or a ref behind the PR head: say so instead of skipping quietly.
                if branch and not pr.get("fork") and valid_branch(branch) and notes is not None:
                    notes.append(f"{project['key']}: PR #{pr.get('number')} content unverified; run `git fetch origin` in {repo}")
                continue
            base = git.default_ref(repo, project.get("defaultBranch"), gh)
            changed = git.run(repo, "diff", "--name-only", "-z", f"{base}...{ref}", "--")[1].split("\0") if base else []
            files = []
            if source.get("artifact"):
                files.append(source["artifact"])
            if source.get("dir"):
                d = source["dir"].rstrip("/")
                rc, listing = git.run(repo, "ls-tree", "--name-only", "-r", "-z", ref, "--", d + "/")
                if rc == 0:
                    files += [f for f in listing.split("\0") if f.lower().endswith(".html")]
            for file in files:
                if file not in changed:
                    continue
                for r in section_ranges(git.show(repo, ref, file) or ""):
                    key = f"{repo.name}:{file}#{r['section']}"
                    if key not in known and key not in out:
                        out[key] = {"key": key, "id": f"{project['key']}/{r['section']}", "title": r["title"],
                                    "design": "publishing", "branch": branch, "pr": pr.get("url")}
    return list(out.values())


# ---------------------------------------------------------------- markers

MARKER = re.compile(r"\bdesign:[ \t]*(\S+)")
SKIP_DIRS = {".git", "node_modules"}


def tracked_files(git: Git, repo: Path) -> list[str]:
    rc, listing = git.run(repo, "ls-files", "-z")
    if rc == 0:
        return [f for f in listing.split("\0") if f]
    files: list[str] = []
    for folder, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        files += [Path(os.path.relpath(os.path.join(folder, n), repo)).as_posix() for n in names]
    return files


def scan_markers(git: Git, repo: Path) -> list[tuple[str, str]]:
    """(token, 'path:line') for every `design:` marker in the app repo's tracked files."""
    found: list[tuple[str, str]] = []
    for rel in tracked_files(git, repo):
        path = repo / rel
        try:
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data or b"design:" not in data:
            continue
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            for token in marker_tokens(line):
                found.append((token, f"{rel}:{n}"))
    return found


# Comment terminators and sentence punctuation after a marker; a lone `-` is a valid id character.
MARKER_TAIL = re.compile(r"""(?:-->|\*/|[.,;:)*/>'"`])+$""")


def marker_tokens(line: str) -> list[str]:
    """`design:` marker values on one line, with or without a space after the colon."""
    return [MARKER_TAIL.sub("", m.group(1)) for m in MARKER.finditer(line)]


def confirmed_markers(git: Git, repo: Path, sha: str, key: str, short: str | None) -> list[str]:
    """Locations on the default commit that name this frame: its full key, or a `design:` marker with its short id.

    Short markers are parsed exactly as `scan_markers` suggests them, so `design:p/one` and `design: p/one` both count.
    """
    needles = [key] + (["design:"] if short else [])
    args = [a for needle in needles for a in ("-e", needle)]
    _, output = git.run(repo, "grep", "-n", "-I", "-F", *args, sha, "--")
    found = []
    for line in output.splitlines():
        m = re.match(r"^(.*?):(\d+):(.*)$", line[len(sha) + 1:])
        if not m:
            continue
        full = re.search(r'''(?:^|[\s'"`*(>])''' + re.escape(key) + r'''(?=$|[\s'"`*<>),;])''', m[3])
        if full or (short and short in marker_tokens(m[3])):
            found.append(f"{m[1]}:{m[2]}")
    return list(dict.fromkeys(found))


def matches_tag(tag: str, pattern: str) -> bool:
    """Designer shell-style tag globs, including invalid-class failure."""
    source, i = "^", 0
    while i < len(pattern):
        c = pattern[i]
        end = pattern.find("]", i + 1)
        if c == "*":
            source += ".*"
        elif c == "?":
            source += "."
        elif c == "[" and end > i + 1:
            chars = pattern[i + 1:end]
            chars = "^" + chars[1:] if chars.startswith("!") else "\\^" + chars[1:] if chars.startswith("^") else chars
            source += "[" + chars.replace("\\", "\\\\") + "]"
            i = end
        else:
            source += re.escape(c)
        i += 1
    try:
        return bool(re.fullmatch(source + "$", tag))
    except re.error:
        return False


def shipped(git: Git, gh, repo: Path, merge: str, project: dict, base: str | None) -> bool:
    pattern = project.get("releaseTagPattern")
    if pattern:
        tags = git.run(repo, "tag", "--contains", merge)[1]
        if any(matches_tag(t, pattern) for t in tags.splitlines() if t):
            return True
    if project.get("deployWorkflow") and base:
        branch = re.sub(r"^refs/(remotes/origin|heads)/", "", base)
        runs = gh.runs(repo, project["deployWorkflow"], branch)
        for run in runs or []:
            sha = run.get("headSha")
            if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha, re.I) and git.is_ancestor(repo, merge, sha):
                return True
    return False


# ---------------------------------------------------------------- check

def check(root: Path, project_key: str | None = None, git: Git | None = None, gh=None) -> dict:
    git = git or Git()
    gh = gh or Gh()
    notes: list[str] = []
    projects = [get_project(root, project_key)] if project_key else load_projects(root)
    if not gh.available:
        notes.append(GH_MISSING)
    result = {"designer": str(root), "projects": [], "notes": notes}
    for project in projects:
        frames = frames_of(root, project)
        saved = carry_renames(load_status(root, project["key"])["frames"], frames)
        prs_by_repo = {}
        for source in project.get("sources", []):
            repo = (root / source["repo"]).resolve()
            if str(repo) not in prs_by_repo:
                prs = gh.prs(repo, state="open") if gh.available else None
                if prs is None:
                    notes.append(f"{project['key']}: pull request lookup unverified for {repo}")
                prs_by_repo[str(repo)] = [pull_ref(p) for p in prs or [] if pull_ref(p)["state"] == "open"]
        app = (root / project["appRepo"]).resolve() if project.get("appRepo") else None
        base = git.default_ref(app, project.get("defaultBranch"), gh) if app else None
        default_sha = git.rev(app, base) if base else None
        suggestions = scan_markers(git, app) if app and app.is_dir() else []
        counts = {}
        for f in frames:
            counts.setdefault(f.section, set()).add(f.key)
        # A short marker must be unique across local and PR-only frames.
        extra = extra_frames(git, root, project, {f.key for f in frames}, prs_by_repo, gh, notes)
        for f in extra:
            counts.setdefault(f["key"].rsplit("#", 1)[1], set()).add(f["key"])
        rows = []
        for f in frames:
            entry = saved.get(f.key, {})
            design = gather_design(git, gh, f.repo, f.file, f.section, bool(entry.get("planned")),
                                   prs_by_repo.get(str(f.repo), []), project.get("defaultBranch"))
            facts = None
            build = entry.get("build")
            if build and build["repo"] == project.get("appRepo"):
                # Never substitute the project's repo for the one the record names.
                facts = build_facts(git, gh, (root / build["repo"]).resolve(), build, project.get("defaultBranch"))
            info = build_state(entry, facts)
            if build and (facts is None or facts["pulls"] is None):
                notes.append(f"{project['key']}/{f.section}: GitHub merge evidence unverified")
            if facts and facts.get("renamedBranch"):
                info["renamedBranch"] = facts["renamedBranch"]
            ident = f"{project['key']}/{f.section}"
            short = ident if len(counts[f.section]) == 1 else None
            markers = confirmed_markers(git, app, default_sha, f.key, short) if info["state"] == "implemented" and app and default_sha else []
            if info["state"] == "implemented":
                info["unconfirmed"] = not markers
                merged = info.get("pr") if isinstance(info.get("pr"), dict) and info["pr"].get("state") == "merged" else {}
                # The proven commit: the PR's merge, a live tip on the default branch, or the recorded commit
                # that recordedMerged showed on the default branch (its branch may be deleted).
                merge = (merged.get("mergeCommit") or (facts.get("tip") if facts and facts["onDefault"] else None)
                         or (build.get("sha") if facts and facts.get("recordedMerged") else None))
                if app and merge and shipped(git, gh, app, merge, project, base):
                    info["state"] = "shipped"
            mine = sorted({loc for tok, loc in suggestions if tok == f.key or (short and tok == short)})
            rows.append({"key": f.key, "id": short or f.key, "title": f.title,
                         "design": design["state"], "designInfo": design,
                         "build": info["state"], "buildInfo": info, "evidence": info.get("reason", ""),
                         "buildEvidence": build, "screens": build.get("screens", []) if build else [],
                         "markers": markers, "suggestedMarkers": mine,
                         **({"unconfirmed": info["unconfirmed"]} if "unconfirmed" in info else {})})
        if app is None:
            notes.append(f"{project['key']}: no appRepo, build state not tracked")
        elif not app.is_dir():
            notes.append(f"{project['key']}: app repo not found at {project['appRepo']}")
        elif not base:
            notes.append(f"{project['key']}: app default branch not found")
        result["projects"].append({"key": project["key"], "appRepo": project.get("appRepo"),
                                   "frames": rows, "extra": extra})
    return result


def render_markdown(result: dict) -> str:
    lines: list[str] = []
    for p in result["projects"]:
        tally: dict[str, int] = {}
        for r in p["frames"]:
            tally[r["design"]] = tally.get(r["design"], 0) + 1
            if r["build"] != "none":
                tally["build " + r["build"]] = tally.get("build " + r["build"], 0) + 1
        lines += [f"## {p['key']}", "", ", ".join(f"{k} {v}" for k, v in sorted(tally.items())) or "no frames", "",
                  "| Frame | Design | Build | Evidence |", "| --- | --- | --- | --- |"]
        for r in p["frames"]:
            ev = r["evidence"]
            if r.get("unconfirmed"):
                ev += "; unconfirmed: no marker on default branch"
            if r.get("screens"):
                ev += "; screens: " + ", ".join(r["screens"])
            if r["markers"]:
                ev = (ev + "; " if ev else "") + "markers: " + ", ".join(r["markers"])
            if r.get("suggestedMarkers") and not r["markers"]:
                ev = (ev + "; " if ev else "") + "suggested markers: " + ", ".join(r["suggestedMarkers"])
            lines.append(f"| {r['id']} | {r['design']} | {r['build'] if r['build'] != 'none' else ''} | {ev} |")
        for r in p["extra"]:
            lines.append(f"| {r['id']} (on branch only) | publishing | | {r['branch']} {r['pr'] or ''} |")
        lines.append("")
    if result["notes"]:
        lines += ["Notes:"] + [f"- {n}" for n in result["notes"]]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- record and clear

def _try_lock(fh) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def status_lock(root: Path, key: str, timeout: float = 30.0):
    """Serialize one project's status read-modify-write across `record` and `clear` processes.

    An OS file lock (flock, or msvcrt on Windows) held on an open handle: only its holder can release it, and
    the system releases it when a process exits, so no run ever has to judge or delete an abandoned lock.
    The lock file lives in the temp directory, never in the Designer workspace, and is never removed.
    """
    digest = hashlib.sha256(str(status_path(root, key).resolve()).encode("utf-8")).hexdigest()[:24]
    lock = Path(tempfile.gettempdir()) / f"orbyn-design-status-{digest}.lock"
    deadline = time.monotonic() + timeout
    with open(lock, "a+b") as fh:
        while not _try_lock(fh):
            if time.monotonic() > deadline:
                raise SystemExit(f"Status for {key} is being changed by another process; try again")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(fh)


def record(root: Path, ident: str, branch: str, repo: str | None = None, pr: str | None = None,
           git: Git | None = None, now: str | None = None, allow_missing: bool = False,
           screens: list[str] | None = None) -> dict:
    git = git or Git()
    head = split_head(branch)
    if not head:
        raise SystemExit(f"Bad branch name: {branch!r}")
    if head.get("owner") and pr is None:
        raise SystemExit("A fork head (owner:branch) needs --pr: its branch cannot be looked up by name")
    project, frame = resolve_frame(root, ident)
    repo = repo or project.get("appRepo")
    if not repo:
        raise SystemExit(f"Project {project['key']} has no appRepo")
    if repo != project.get("appRepo"):
        raise SystemExit("--repo must equal the project's appRepo")
    app = (root / repo).resolve()
    if pr is not None and not pr_number(pr, github_origin(git.run(app, "remote", "get-url", "origin")[1])):
        raise SystemExit("--pr must be a pull request number or a https://github.com/<owner>/<repo>/pull/<n> URL "
                         "for the app repository's origin")
    build: dict = {"repo": repo, "branch": branch}
    sha = git.branch_tip(app, branch) if not head.get("owner") else None
    if sha:
        build["sha"] = sha
    elif not allow_missing and not head.get("owner"):
        raise SystemExit(f"Branch {branch!r} does not exist; use --allow-missing to record it anyway")
    if pr:
        build["pr"] = pr
    if screens is not None:
        build["screens"] = screens
    build["recorded"] = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with status_lock(root, project["key"]):
        data = load_status(root, project["key"])
        data["frames"] = carry_renames(data["frames"], frames_of(root, project))
        entry = data["frames"].setdefault(frame.key, {})
        entry["build"] = build
        entry.pop("legacy", None)
        write_status(root, project["key"], data)
    return {"project": project["key"], "key": frame.key, "build": build}


def clear(root: Path, ident: str) -> dict:
    project, frame = resolve_frame(root, ident)
    with status_lock(root, project["key"]):
        data = load_status(root, project["key"])
        data["frames"] = carry_renames(data["frames"], frames_of(root, project))
        entry = data["frames"].get(frame.key, {})
        removed = [k for k in ("build", "legacy") if entry.pop(k, None) is not None]
        if not entry:
            data["frames"].pop(frame.key, None)
        write_status(root, project["key"], data)
    return {"project": project["key"], "key": frame.key, "removed": removed}


# ---------------------------------------------------------------- cli

def main(argv: list[str] | None = None, gh=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="read-only report of design and build state")
    c.add_argument("--designer", required=True, type=Path)
    c.add_argument("--project")
    c.add_argument("--json", action="store_true")
    r = sub.add_parser("record", help="save the app branch that implements a frame")
    r.add_argument("--designer", required=True, type=Path)
    r.add_argument("--frame", required=True)
    r.add_argument("--branch", required=True)
    r.add_argument("--repo")
    r.add_argument("--pr")
    r.add_argument("--allow-missing", action="store_true")
    r.add_argument("--screens", nargs="+", help="frame keys or short ids implemented by this branch")
    x = sub.add_parser("clear", help="remove recorded build evidence and legacy tag for a frame")
    x.add_argument("--designer", required=True, type=Path)
    x.add_argument("--frame", required=True)
    args = ap.parse_args(argv)
    root = args.designer.resolve()
    if args.cmd == "check":
        result = check(root, args.project, gh=gh)
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n" if args.json
                         else render_markdown(result))
    elif args.cmd == "record":
        out = record(root, args.frame, args.branch, args.repo, args.pr,
                     allow_missing=args.allow_missing, screens=args.screens)
        print(f"Recorded {out['build']['branch']} for {out['key']} in workspace/status/{out['project']}.json")
    else:
        out = clear(root, args.frame)
        print(f"Cleared {', '.join(out['removed']) or 'nothing'} for {out['key']}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
