#!/usr/bin/env python3
"""Check and record design and build status for Orbyn Designer frames.

Reads: <Designer root>/workspace/projects.json and workspace/status/<project>.json, the
source design repos and the app repos (read-only git commands, `gh pr list` when signed in).
Writes: only `record` and `clear`, and only <Designer root>/workspace/status/<project>.json.
Python 3 standard library only. Rules follow the Designer status file v2 contract.
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
            stack.append(bool(sid))
        elif stack:
            had_id = stack.pop()
            if had_id and open_ and not any(stack):
                h2 = re.search(r"<h2\b[^>]*>(.*?)</h2\s*>", html[open_["from"]:m.start()], flags=re.S | re.I)
                title = ""
                if h2:
                    title = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", h2.group(1)))).strip()
                out.append({"section": open_["id"], "title": title or open_["id"],
                            "start": open_["start"], "end": m.end()})
                open_ = None
    return out


def norm(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def section_html(html: str, section: str) -> str | None:
    for r in section_ranges(html):
        if r["section"] == section:
            return norm(html[r["start"]:r["end"]])
    return None


# ---------------------------------------------------------------- git and gh

class Git:
    """Read-only git with a per-run cache. Callers only pass read commands."""

    def __init__(self) -> None:
        self.cache: dict[tuple, tuple[int, str]] = {}

    def run(self, repo, *args: str) -> tuple[int, str]:
        key = (str(repo), *args)
        if key not in self.cache:
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
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

    def default_ref(self, repo) -> str | None:
        rc, out = self.run(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
        if rc == 0 and out.strip():
            ref = out.strip().replace("refs/remotes/", "", 1)
            if self.rev(repo, ref):
                return ref
        for ref in ("origin/main", "origin/master"):
            if self.rev(repo, ref):
                return ref
        return None

    def branch_tip(self, repo, branch: str) -> str | None:
        return self.rev(repo, "refs/heads/" + branch) or self.rev(repo, "refs/remotes/origin/" + branch)

    def current_branch(self, repo) -> str | None:
        rc, out = self.run(repo, "symbolic-ref", "--short", "-q", "HEAD")
        return out.strip() if rc == 0 and out.strip() else None

    def file_clean(self, repo, file: str) -> bool:
        rc, out = self.run(repo, "status", "--porcelain", "--", file)
        return rc == 0 and not out.strip()


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

    def prs(self, repo, head: str | None = None, state: str = "all") -> list[dict] | None:
        """Pull requests as {state, url, headRefName}, or None when unavailable or failed."""
        if not self.available:
            return None
        cmd = [self._exe, "pr", "list", "--state", state, "--json", "state,url,headRefName", "--limit", "100"]
        if head:
            cmd += ["--head", head]
        try:
            p = subprocess.run(cmd, capture_output=True, cwd=str(repo), timeout=60)
            return json.loads(p.stdout.decode("utf-8", "replace")) if p.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None


# ---------------------------------------------------------------- projects and frames

@dataclass
class Frame:
    key: str
    project: str
    section: str
    title: str
    file: str
    repo: Path  # resolved source repo


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


def load_status(root: Path, key: str) -> dict:
    path = status_path(root, key)
    if not path.is_file():
        return {"groups": [], "frames": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("frames", {})
    return data


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
                                 r["title"], file, repo))
    return out


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

def build_state(git: Git, gh, repo: Path, build: dict | None, legacy: str | None) -> tuple[str, str]:
    """Rules 1 to 6 of the contract. Returns (state, short evidence text)."""
    if not build:
        return ("check", f"hand tag '{legacy}' with no branch recorded") if legacy else ("none", "")
    branch = build["branch"]
    if not repo.is_dir():
        return "check", f"app repo not found: {build.get('repo', repo)}"
    default = git.default_ref(repo)
    tip = git.branch_tip(repo, branch)
    exists = tip is not None
    # A freshly cut branch is also an ancestor of main, so rule 3 needs a recorded sha the tip has moved past.
    if tip and build.get("sha") and tip != build["sha"] and default and git.is_ancestor(repo, tip, default):
        return "implemented", f"{branch} merged into {default}"
    prs = gh.prs(repo, head=branch, state="all") if gh.available else None
    mine = [pr for pr in prs or [] if pr.get("headRefName", branch) == branch]
    for pr in mine:
        if pr.get("state") == "MERGED":
            return "implemented", f"{branch} merged by pull request {pr.get('url', '')}".strip()
    for pr in mine:
        if pr.get("state") == "OPEN":
            return "building", f"{branch} has open pull request {pr.get('url', '')}".strip()
    if exists:
        return "building", f"{branch} exists, not merged"
    return "check", f"{branch} is gone and no merged pull request was found"


# ---------------------------------------------------------------- design state

def design_state(git: Git, repo: Path, file: str, section: str, planned: bool,
                 open_branches: list[str]) -> str:
    """Rules 1 to 5 of the contract for one frame (rule 5's extra frames are found separately)."""
    if planned:
        return "planned"
    path = repo / file
    working = section_html(path.read_text(encoding="utf-8", errors="replace"), section) if path.is_file() else None
    default = git.default_ref(repo)
    if default:
        remote = git.show(repo, default, file)
        if working is not None and remote is not None and section_html(remote, section) == working:
            return "published"
        if git.file_clean(repo, file) and git.rev(repo, "HEAD") and git.is_ancestor(repo, "HEAD", default):
            return "published"
    for branch in open_branches:
        ref = f"origin/{branch}" if git.rev(repo, f"origin/{branch}") else branch
        theirs = git.show(repo, ref, file)
        if working is not None and theirs is not None and section_html(theirs, section) == working:
            return "publishing"
        if git.current_branch(repo) == branch and git.file_clean(repo, file):
            return "publishing"
    return "designed"


def extra_frames(git: Git, root: Path, project: dict, known: set[str], prs_by_repo: dict) -> list[dict]:
    """Sections that exist only on an open pull request branch."""
    out: dict[str, dict] = {}
    for source in project.get("sources", []):
        repo = (root / source["repo"]).resolve()
        for pr in prs_by_repo.get(str(repo), []):
            branch = pr.get("headRefName")
            if not branch:
                continue
            ref = f"origin/{branch}" if git.rev(repo, f"origin/{branch}") else branch
            if not git.rev(repo, ref):
                continue
            files = []
            if source.get("artifact"):
                files.append(source["artifact"])
            if source.get("dir"):
                d = source["dir"].rstrip("/")
                rc, listing = git.run(repo, "ls-tree", "--name-only", ref, d + "/")
                if rc == 0:
                    files += [f for f in listing.split("\n") if f.lower().endswith(".html")
                              and "/" not in f[len(d) + 1:]]
            for file in files:
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
            for m in MARKER.finditer(line):
                found.append((m.group(1).rstrip(".,;:)*/>'\"`-"), f"{rel}:{n}"))
    return found


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
        saved = load_status(root, project["key"])["frames"]
        frames = frames_of(root, project)
        prs_by_repo: dict[str, list[dict]] = {}
        for source in project.get("sources", []):
            repo = (root / source["repo"]).resolve()
            if str(repo) not in prs_by_repo:
                prs = gh.prs(repo, state="open") if gh.available else None
                prs_by_repo[str(repo)] = [p for p in prs or [] if p.get("state", "OPEN") == "OPEN"]
        app = (root / project["appRepo"]).resolve() if project.get("appRepo") else None
        markers = scan_markers(git, app) if app and app.is_dir() else []
        counts: dict[str, int] = {}
        for f in frames:
            counts[f.section] = counts.get(f.section, 0) + 1
        rows = []
        for f in frames:
            entry = saved.get(f.key, {})
            branches = [p["headRefName"] for p in prs_by_repo.get(str(f.repo), []) if p.get("headRefName")]
            design = design_state(git, f.repo, f.file, f.section, bool(entry.get("planned")), branches)
            build, evidence = "none", ""
            if app:
                build, evidence = build_state(git, gh, app, entry.get("build"), entry.get("legacy"))
            ident = f"{project['key']}/{f.section}"
            mine = sorted({loc for tok, loc in markers if tok in (ident, f.key)})
            rows.append({"key": f.key, "id": ident if counts[f.section] == 1 else f.key, "title": f.title,
                         "design": design, "build": build, "evidence": evidence,
                         "buildEvidence": entry.get("build"), "markers": mine})
        extra = extra_frames(git, root, project, {f.key for f in frames}, prs_by_repo)
        if app is None:
            notes.append(f"{project['key']}: no appRepo, build state not tracked")
        elif not app.is_dir():
            notes.append(f"{project['key']}: app repo not found at {project['appRepo']}")
        elif not git.default_ref(app):
            notes.append(f"{project['key']}: no origin/HEAD, origin/main or origin/master in {project['appRepo']}")
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
            if r["markers"]:
                ev = (ev + "; " if ev else "") + "suggested markers: " + ", ".join(r["markers"])
            lines.append(f"| {r['id']} | {r['design']} | {r['build'] if r['build'] != 'none' else ''} | {ev} |")
        for r in p["extra"]:
            lines.append(f"| {r['id']} (on branch only) | publishing | | {r['branch']} {r['pr'] or ''} |")
        lines.append("")
    if result["notes"]:
        lines += ["Notes:"] + [f"- {n}" for n in result["notes"]]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- record and clear

def record(root: Path, ident: str, branch: str, repo: str | None = None, pr: str | None = None,
           git: Git | None = None, now: str | None = None) -> dict:
    git = git or Git()
    if not branch.strip() or branch.startswith("-"):
        raise SystemExit(f"Bad branch name: {branch!r}")
    project, frame = resolve_frame(root, ident)
    repo = repo or project.get("appRepo")
    if not repo:
        raise SystemExit(f"Project {project['key']} has no appRepo; pass --repo")
    build: dict = {"repo": repo, "branch": branch}
    sha = git.branch_tip((root / repo).resolve(), branch)
    if sha:
        build["sha"] = sha
    if pr:
        build["pr"] = pr
    build["recorded"] = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = load_status(root, project["key"])
    entry = data["frames"].setdefault(frame.key, {})
    entry["build"] = build
    entry.pop("legacy", None)
    write_status(root, project["key"], data)
    return {"project": project["key"], "key": frame.key, "build": build}


def clear(root: Path, ident: str) -> dict:
    project, frame = resolve_frame(root, ident)
    data = load_status(root, project["key"])
    entry = data["frames"].get(frame.key, {})
    removed = [k for k in ("build", "legacy") if entry.pop(k, None) is not None]
    if not entry:
        data["frames"].pop(frame.key, None)
    if removed:
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
        out = record(root, args.frame, args.branch, args.repo, args.pr)
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
