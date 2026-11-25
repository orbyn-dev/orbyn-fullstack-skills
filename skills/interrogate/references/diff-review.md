# Review a diff file by file

Use this to review a diff, PR, commit or branch range for defects. It is a fixed
pipeline: the steps that must not go wrong (which files, which rules, where a
finding points, whether it is true) are mechanical, and judgement goes only into
the review itself. A general agent left to "review this" skips files on large
diffs, drifts line numbers and varies with the prompt; each step below closes one
of those gaps.

Every step runs every time; on a small diff grouping is one group and the fact
check takes seconds.

## 1. Select files and open the ledger

Resolve the mode first and keep it for every later command:

| Mode | Base | Diff command |
| --- | --- | --- |
| Range (branch or PR) | `git merge-base <from> <to>` | `git --no-pager diff --no-ext-diff --no-textconv --no-color <merge_base>..<to> -- "<path>"` |
| Commit | the commit's parent | `git --no-pager show --no-ext-diff --no-textconv --no-color <commit> -- "<path>"` |
| Workspace | `HEAD` | `git --no-pager diff --no-ext-diff --no-textconv --no-color HEAD -- "<path>"`; read untracked files whole |

`<to>` is `HEAD` unless given. A range compares the merge-base with the target and
ignores staged, unstaged and untracked work. Always pass `--no-ext-diff`,
`--no-textconv` and `--no-color` so you read plain patches, not a configured diff
tool's output. List the files in the same mode with two calls,
`git diff --name-status` and `git diff --numstat` (one call with both flags prints
only the first); in workspace mode add `git ls-files --others --exclude-standard`
for untracked files.

Exclude a file only for one of these reasons, and record the reason:

| Reason | Rule |
| --- | --- |
| `binary` | Git reports it as binary. |
| `secret` | The old or new path is a credential file: `.env` or `.env.*` (but not `.env.example`, `.env.sample` or `.env.template`), `.ssh/`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `.netrc`, `_netrc`, `.npmrc`, `.pypirc`, `.dockercfg`. No include rule admits it. Never open it. |
| `user-excluded` | Matches a pattern the user or project gave. An explicit include beats the next two rules. |
| `extension` | Not a source, config, query, markup or build file. |
| `default-path` | Test files, snapshots, fixtures and `testdata/`; generated code (`*.generated.*`, `*.pb.go`, `*.gen.go`, minified bundles); dependency and build output (`node_modules/`, `vendor/`, `dist/`, `target/`, `.venv/`, `Pods/`, framework caches); lockfiles. Review tests only when the user or project includes them. |
| `deleted` | Removed. Not reviewed, but its old side stays available as context, because a removed interface can still break callers. |
| `too-large` | Too big to read at once. Write the diff to a file outside the repository with `--output=<absolute path>`, read it all in chunks, then delete it. If Git fails or times out, retry, then mark the row `skipped: <error>`. |

Open a ledger with one row per remaining file, keyed by `(path, status)`; a
workspace can list one path twice, as a staged deletion and an untracked
re-creation. Every row must end as `reviewed` or `skipped: <concrete reason>`
(for example, the diff command failed and a retry also failed). Never omit a row
silently. If a diff comes back unexpectedly empty, list the files again before
believing it.

## 2. Group related files

Put files that must be read together into one review unit:

- the same module or feature;
- producer and consumer, such as an interface and its implementation, a header and
  its source, a schema and its migration;
- variants of one resource, such as `messages_en.properties` and
  `messages_zh.properties`;
- the same directory working on one concern.

Every file belongs to exactly one group, a group may hold one file, and no group
holds more than ten. Split a group that would not fit one reader's context. Each
group is reviewed with its own context, so groups can run as parallel reviewer
children within the [harness limits](../../orbyn-workflow/references/project-policy.md#delegation-and-host-configuration),
or one after another. A small diff is one group (OCR labels it "small change
set"), still split at ten files.

## 3. Match the rules to each group

Give each group only the checklist for its file types: the affected guide from
[language best practices](../../language-best-practices/SKILL.md), the
[vulnerability classes](../../orbyn-security-audit/references/vuln-classes.md) for
code that touches input, auth or files, and the project's own review rules. Files
that share a checklist share one rule group. Where nothing narrower applies, use
this default:

- **Correctness:** is the logic right, are boundary conditions handled, are
  errors handled, is it safe under concurrency?
- **Security:** injection or XSS, sensitive data handling, complete permission
  checks.
- **Performance:** N+1 queries, needless loops, resources released.
- **Maintainability:** clear code, names that say what they mean, the project's
  existing style and architecture.
- **Tests:** do critical paths and boundary conditions have tests?

Matching rules to files keeps the reviewer's attention on the risks that file can
have, instead of a generic list on every file.

## 4. Plan, then review each group

Give the reviewer of each group: the group's diffs, the list of other changed files
not in this group, the requirement background if there is one, the group's rules
and the findings already confirmed in earlier groups. Then write a short plan
before reading context:

```text
Summary: what this change does and how far it reaches

Issues
1. [high] where, what is wrong, what it would cause
   -> read internal/queue/worker.go -- confirm the retry key matches the one used at submit
2. [medium] ...
```

Sort issues high to medium to low: **high** is security, data loss, crashes or
broken core function; **medium** is performance, maintainability or edge cases;
**low** is style and readability. Only new and modified code is in scope. If
nothing is risky, write `(none)`; never invent issues to fill the plan. The `->`
lines say which context to fetch and why; they are not run yet.

Then review the group under these rules:

- Review every file in the group on its own pass. Reviewing an implementation does
  not cover its header, interface, config or test counterpart, and being the
  smaller file is no reason to skip one.
- Comment only on added or changed code. Deleted lines are context. Do not comment
  on correct code, unchanged code, code comments or generated markers unless asked.
- Look across the group for inconsistencies, missing updates and broken contracts.
- Use context tools (read, search, `git log`, `git blame`,
  `git show <ref>:<path>`) to confirm, not to widen the review: every finding must
  target a file in this group, never one outside it.
- When the context is unclear, read more instead of assuming.
- Keep going after the first high-severity finding.
- For each diff, ask which existing caller or user workflow now behaves
  differently, and record the before and after with its location.

Mark the file's ledger row once its own pass is done.

## 5. Write each finding in a fixed shape

| Field | Required | Content |
| --- | --- | --- |
| `path` | yes | File path relative to the repository root |
| `snippet` | yes | The lines the finding is about, copied from the diff |
| `start_line`, `end_line` | when known | Lines in the new file |
| `severity` | yes | `critical`, `high`, `medium` or `low` |
| `category` | yes | `bug`, `security`, `performance`, `maintainability`, `test`, `style`, `documentation` or `other` |
| `content` | yes | What is wrong, the scenario that triggers it and the impact |

## 6. Check every location

Before reporting, find each finding's `snippet` in its file's diff. If it does not
match, re-locate it: copy, word for word from the diff, the smallest contiguous run
of lines the finding is about; strip the leading `+`, `-` or space; take no
surrounding context; and if several places fit, pick the single most relevant one.
Then recompute `start_line` and `end_line` from that snippet. A finding whose code
is not in its file's diff at all goes to the fact check under Ground A.

## 7. Fact-check the findings

For a diff of more than a few files, or before findings are posted, give the
findings and the group's diffs to a reader who did not write them, and run the
[fact check](diff-review/fact-check.md). It removes only findings the diff proves
wrong and keeps everything else, because a dropped real finding never reaches
anyone. On a small diff, run the same check yourself.

## 8. Report

1. **Coverage:** `total_files`, `reviewed_files`, `skipped_files` and
   `coverage_rate`, with every skip and exclusion and its reason. Check the ledger
   first: no row may be open.
2. **Findings by severity.** Report critical and high always; medium with its
   context; low only when clearly worth it.
3. **Behaviour changes:** a material change in what a caller or user sees, even an
   intended one with no defect. Name the workflow, what happened before, what
   happens now and the location; for example, removing a public option breaks
   scripts that still pass it. Keep it apart from the findings and the coverage,
   mention it once, qualify what is uncertain, and leave the section out when
   nothing changed.

If the user asked to review and fix: fix critical and high findings, describe the
medium ones that need a person, and skip low ones unless trivial. A review-only
request edits nothing, under the [review-only limits](../SKILL.md#review-only-limits).

## Source

Adapted from Alibaba's [Open Code Review](https://github.com/alibaba/open-code-review/tree/9007ae84e0de7b84d72f9833f6b12a2a731d5cbd)
at commit `9007ae8`: the delegation-mode skill, the planning, review, grouping,
re-location and review-filter prompts, the default rule document and the file
selection logic. Apache-2.0; see [the licence](diff-review/open-code-review-LICENSE.txt).
Changed for Orbyn: rewritten as host-agent steps with no `ocr` CLI or model
provider, mapped to our language, security and harness owners; `snippet`, `severity`
and `category` made required; OCR's "discard likely false positives silently"
dropped, because removal belongs to the fact check, which keeps what it cannot
disprove. The native pipeline
drops a too-large file; we use the delegation skill's chunked read instead.
