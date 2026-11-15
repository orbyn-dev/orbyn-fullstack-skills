# Designer handoff

Read when the screen you implement has an Orbyn Designer frame key such as
`notes/clip-failed` (shown in the Designer inspector; `<project>/<section>`).

1. Cut the app branch from the app's default branch: `feat/<project>-<section>`, for
   example `feat/notes-clip-failed`. The target repo's naming rules win.
2. Add the marker next to the implementing code: `// design: notes/clip-failed`
   (use the file's own comment syntax).
3. Record the branch, with the person's go-ahead, using
   [orbyn-design-status](../../orbyn-design-status/SKILL.md):
   `python3 <skill dir>/scripts/design_status.py record --designer <Designer root> --frame notes/clip-failed --branch feat/notes-clip-failed`.
   This writes only the project's file under Designer's `workspace/status/`. The Designer
   file is a separate repo, so leave committing it to the person.
4. Put `design: notes/clip-failed` in the PR title or body.

After the PR merges, Designer shows Implemented by itself. Never hand-write Implemented
or edit status fields to say so. If the branch is deleted without a merge, Designer shows
Check; ask before clearing it with `design_status.py clear`.
