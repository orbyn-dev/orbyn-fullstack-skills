---
name: orbyn-design-status
description: "Check which Orbyn Designer frames are designed, published, building or implemented, and record or clear the app branch for a frame. Use for design-to-build status; use orbyn-ui-design to build the screen."
---

# Orbyn design status

Designer shows two badges per frame, both computed from git: Design (planned, designed,
publishing, published) and Build (building, implemented, check; hidden with no evidence).
This skill reads that evidence and saves the one thing git cannot know: which app branch
implements a frame. It needs the Orbyn Designer checkout (the folder with
`workspace/projects.json`) and Python 3 with git; `gh` is optional.

## Method

1. Run the check. It is read-only:
   `python3 <skill dir>/scripts/design_status.py check --designer <Designer root> [--project <key>] [--json]`
2. Show a short table: frame, design, build, evidence. Lead with frames in `check`, with
   suggested `markers`, or with a branch that is `building`. Pass on the notes, for example
   "GitHub CLI not available" (pull request lookups were skipped, so squash merges may show
   as `check` or `building`).
3. Offer changes and wait for an explicit yes before any write:
   - Starting to build a screen: `design_status.py record --designer <root> --frame <project>/<section> --branch <app branch> [--pr <url>] [--repo <rel>]`.
   - Stale evidence (`check`): `design_status.py clear --designer <root> --frame <id>` removes the recorded branch and any old hand tag.
4. Report the frame key and the file changed. Commit or publish the Designer file only if
   the person asks.

`record` and `clear` write only `<root>/workspace/status/<project>.json`. Nothing here writes
`Implemented`: it appears when the recorded branch merges into the app's default branch.

Frame ids are `<project>/<section>`, for example `notes/clip-failed`. When two sources in
a project share a section id, the command lists the full keys; pass one of those
(`<repo>:<file>#<section>`).

## Marker convention

In the app repo, put `// design: <project>/<section>` next to the code that implements the
frame, and `design: <project>/<section>` in the PR title or body. `check` finds markers and
lists them as suggestions; it does not record them.

Worked failure: `notes/clip-failed` shows `check` because the recorded branch was deleted
and `gh` was missing. Do not hand-edit the file to say implemented. Sign in to `gh` and
re-run; if the work merged, it flips to implemented. If the evidence is wrong, `clear` it
after the person says yes, then `record` the right branch.

## Read when needed

- Implementing a screen from a Designer frame: [designer handoff](../orbyn-ui-design/references/designer-handoff.md).
- Rules behind each state (build rules 1 to 6, design rules 1 to 5): the docstrings of `build_state` and `design_state` in `scripts/design_status.py`.
- Tests: `python3 -m unittest discover -s <skill dir>/scripts`.
