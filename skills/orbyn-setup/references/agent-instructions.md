# Write and layer a project's AGENTS.md files

Use when a project asks to write, split or tidy its agent instruction files. Setup
itself writes none of them; this is the project's own authorized task, and the
project's existing files and conventions win.

## One file per level

A repository can hold one `AGENTS.md` at the root and one in any folder whose work
needs its own rules. Size each file to the work done inside its folder:

| Level | Holds |
| --- | --- |
| Root | What each top-level package or app is for, how they fit, how to build, run and test the whole thing, and rules every change needs |
| Package, app or service | Its commands, patterns, boundaries and hard-won facts; a link up to the architecture map instead of repeating it |
| A test or tooling folder | How its suites are organised, how to run one case, fixture rules |

Split a file when most of it applies to only one folder. A 3,000-line root file
covering the API, the web app and the tests is three files that have not been split
yet. A small single-package repository needs only the root file.

## What each file holds

Open with the rules a change is most likely to break; hosts can truncate long files.
Write in the imperative ("Run `pnpm test:unit` before pushing"), one rule per
bullet, with a short command or code example where words would be vague. Link to the
architecture map, decision records and READMEs instead of copying them
([where each rule lives](project-skills.md#rules-beside-the-code-they-govern)).

Keep out:

- secrets, keys, internal hostnames and anything else unsafe to commit;
- what a linter, formatter or `.editorconfig` already enforces;
- the PR description format, which belongs in the pull request template;
- one person's working preferences, which belong in their own user-level
  instructions or memory;
- history and stories; keep the rule, not how it was found.

## Record hard-won facts

When something took several failed attempts, record it in one short paragraph: the
fact, why it holds, and the tempting path that does not work, with the PR or issue
that proved it. "The desktop build is flaky" is not a fact; "Run `cargo build`
before `pnpm dev`: the app copies the sidecar binary at startup, so `pnpm dev` alone
runs last week's binary (#231)" is. Remove the
entry when the code change that made it necessary is gone.

## Make nested files agree

Hosts differ in which files they load and in what order, so never rely on
precedence ([action scope](action-scope.md#separate-instruction-lookup-from-code-search)).
Write the levels so they never conflict: a nested file narrows the root for its
folder and does not repeat it. When a folder genuinely needs a different rule, say so
in the nested file and name the root rule it replaces for that folder only. A nested
file never relaxes safety, permission or review rules from a parent.

Where a host reads only its own filename (for example `CLAUDE.md`), add a one-line
pointer file beside each `AGENTS.md` that imports or names it (`@AGENTS.md` where
the host supports imports). Never keep a second copy that can drift. Check each
host's documentation for which names and folders it loads; nesting alone does not
prove a host reads a subtree file, so an agent working there still reads it
explicitly.

## Keep it current

Change the instruction file in the same change as the code that makes a rule true or
false. When an agent repeats the same mistake, add the rule with its example, or
lift it to a check that fails ([structural prevention](../../architect/references/principles/encode-lessons-in-structure.md)).
An outdated rule is worse than none: delete it.

## Worked failure

A monorepo keeps everything in one root `AGENTS.md`, including "use `vitest`" for the
web app and "use `pytest`" for the API. An agent fixing an API bug reads both, runs
`vitest` from the root and reports green while the Python tests never ran. Fix: the
root lists the packages and the command to test everything; `apps/web/AGENTS.md`
and `services/api/AGENTS.md` each name their own test command; each has a
`CLAUDE.md` containing `@AGENTS.md`.

## Source

Reviewed inspiration: SSW Rules, [Do you write an AGENTS.md?](https://www.ssw.com.au/rules/write-agents-md),
observed 17 November 2026. Independently written; no text copied.
