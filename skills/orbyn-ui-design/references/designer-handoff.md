# Designer handoff

Use this when implementing a screen with an Orbyn Designer frame key such as
`notes/clip-failed` (`<project>/<section>`).

1. Cut the app branch from its default branch, usually `feat/<project>-<section>`; follow
   the target repo's naming rules.
2. Add `// design: <project>/<section>` next to the code implementing that screen, using
   the file's comment syntax. For a branch implementing multiple screens, add one marker
   per screen and list each frame key in `build.screens`.
3. With the person's go-ahead, record the branch using
   [orbyn-design-status](../../orbyn-design-status/SKILL.md):
   `python3 <skill dir>/scripts/design_status.py record --designer <Designer root> --frame notes/clip-failed --branch feat/notes-clip-failed [--pr <number-or-https-url>]`.
   `record --repo`, if supplied, must equal the project's `appRepo`. The branch must exist
   unless `--allow-missing` is supplied. This writes only the Designer status file.
4. Put `design: <project>/<section>` in the PR title or body.

Designer derives Implemented after merge. It remains unconfirmed until its marker appears
on main. A matching release tag containing the merge or a successful configured deploy
workflow can show Shipped. Project settings are `defaultBranch`, `releaseTagPattern` and
`deployWorkflow` in `workspace/projects.json`; without release/deploy settings the state
stops at Implemented. Never hand-write Implemented or change status fields to claim it.
