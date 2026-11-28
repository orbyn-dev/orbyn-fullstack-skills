---
name: orbyn-design-status
description: "Check Orbyn Designer frame status and record or clear the app branch implementing a frame. Use for design-to-build status; use orbyn-ui-design to build the screen."
---

# Orbyn design status

Designer computes Design (`planned`, `designed`, `publishing`, `published`) and Build
(`none`, `check`, `building`, `implemented`, `shipped`) per frame. A muted Check with
reason `unverified` means GitHub could not answer. Implemented stays Implemented when its
marker is missing on main, with `unconfirmed: true`; it does not mean the marker was found.
Shipped requires project deployment settings. This skill uses the Orbyn Designer checkout
(with `workspace/projects.json`) and Python 3 with git; `gh` is optional.

## Method

1. Run the read-only report:
   `python3 <skill dir>/scripts/design_status.py check --designer <Designer root> [--project <key>] [--json]`
2. Show a short table of frame, design, build, and evidence. Call out Check/unverified,
   unconfirmed markers, and branches still building. Pass through relevant notes about
   unavailable GitHub lookups.
3. With the person's go-ahead, record a frame:
   `python3 <skill dir>/scripts/design_status.py record --designer <root> --frame <project>/<section> --branch <app branch> [--pr <number-or-url>]`
   The recorded repo must be exactly the project's `appRepo`; `--repo` is only for an
   explicit matching value. A branch must exist unless `--allow-missing` is used. `--pr`
   is a PR number or a pull request URL on the app repo's GitHub origin. A fork head
   (`owner:branch`) needs `--pr`.
   To clear stale evidence, use `clear --designer <root> --frame <id>` after the person agrees.
4. Report the frame and file changed. Commit or publish the Designer file only if asked.

`record` and `clear` write only `<root>/workspace/status/<project>.json`. They do not set
Implemented; Designer derives it from merge evidence. One branch may implement several
screens: pass `--screens <id> <id> ...` to save `build.screens`, and add a marker
for each screen. Record the branch for each frame that it implements.

Frame ids are `<project>/<section>`, for example `notes/clip-failed`. If a project has
duplicate section ids, use the full key shown by the command (`<repo>:<file>#<section>`).

## Marker and project settings

In the app repo, put `// design: <project>/<section>` next to the code implementing each
screen; use the file's comment syntax. When the project has the same section id in more
than one file, the short form confirms neither: write the full key instead,
`// design: <repo>:<file>#<section>`. `check` searches the app default branch for each
screen's marker. A missing marker leaves the build Implemented but unconfirmed.

Projects may set `defaultBranch`, `releaseTagPattern` (for example `v*`) and
`deployWorkflow` in `workspace/projects.json`. The default branch falls back through
`origin/HEAD`, GitHub's default branch, `origin/main`, then `origin/master`. A matching
release tag containing the merge, or a successful configured deploy workflow on the
default branch, upgrades Implemented to Shipped. Without release/deploy settings, builds
stop at Implemented.

## Read when needed

- Implementing a Designer frame: [designer handoff](../orbyn-ui-design/references/designer-handoff.md).
- Parser and state rules: `scripts/design_status.py` and its tests.
- Tests: `python3 -m unittest discover -s <skill dir>/scripts`.
