# Name, create and verify a worktree

Detail for the entry skill's [reuse](../SKILL.md#reuse-before-cloning-or-replacing),
[sibling location and names](../SKILL.md#sibling-location-and-names) and create-and-verify steps. The rules there still apply; this file holds the mechanics.

## Reuse checklist

Before creating a checkout, inspect registered trees, canonical repository/remotes,
host attachments and bindings, and any existing reference cache. Reuse a worktree
only when its repository, task owner, branch, source and local work fit this task.
Confirm that no active session still holds a clean directory before reusing it.

Continue the same task/PR in its owned branch and checkout through review and
corrections. Start a new independent task from the verified current default branch
and a stack from its exact reviewed dependency. Do not use a historical task branch
just because it is current, or create a clone per agent/day/retry.

## Sibling location and names

For a primary clone `<parent>/<repo>`, the default root is its sibling
`<parent>/<repo-slug>-wt/`. Use a stable lowercase hyphenated repo slug; reuse an
existing established sibling root rather than creating a case-only alternative.
Each worktree has one direct child directory named for its purpose slug, not the
branch's slash-separated path:

```text
orbyn-fullstack-skills/repo/
  orbyn/                         primary clone
  orbyn-wt/
    task-rescheduling/           feat/task-rescheduling
    save-retry/                  fix/save-retry
    worktree-policy/             chore/worktree-policy
```

Targets use the library-root ignored `repo/` workspace by default under the
[nested repository contract](../../orbyn-setup/references/repository-workspaces.md):
`orbyn-fullstack-skills/repo/widget` uses sibling `repo/widget-wt/<purpose>/`.
An explicit user location or an established consuming-project policy wins.

Use lowercase letters, digits and hyphens for a short directory slug, normally
under 64 characters. Use neutral branch prefixes such as `feat/`, `fix/`, `chore/`,
`docs/` or `refactor/`. Do not use agent, assistant, vendor or model branding in a
new branch or directory name. Avoid path separators, traversal, spaces, Windows
device names and case-only distinctions.

Inspect collisions before choosing a name. If the existing path/branch is unrelated
or still owned, choose a distinct purpose or a suffix such as `-2`; never overwrite
a directory, reuse a foreign branch or use force to bypass Git's already-checked-out
protection. Keep existing legacy paths/branch names intact.

## Create and verify the selected checkout

Resolve the requested base revision before writes. Continue an existing task/PR
from its actual branch or reviewed commit. For new work, use the project's verified
default branch/ref, not an unrelated current feature branch or a stale local `main`.
Fetch only when available and authorized; label an offline/cached base as such.

Create a **new** branch from a verified base with
`git worktree add -b <branch> <path> <base>`. Reuse an existing branch with
`worktree add <path> <branch>` only after ownership/checkout inspection. Avoid `-B`,
`--force` and duplicate branch checkouts. Verify the final path, branch, base/HEAD
and status before editing.

Use the selected worktree as the working directory for every subsequent edit/check.
After selecting or switching checkout, reread nested instructions and re-resolve
search roots there under [action scope](../../orbyn-setup/references/action-scope.md).

Preserve project environment files; do not automatically copy secrets or run setup
hooks from source. Worktrees share Git objects and may still share databases, ports,
fixtures or external services: assign those separately.
