# Source comments

Use this when writing, reviewing or editing comments inside source code.

Prefer clear names and straightforward code for local mechanics. Keep a non-obvious
invariant, ordering requirement, ownership boundary, compatibility reason or
deliberate tradeoff beside the code whose safe modification depends on it. Explain
the consequence of breaking the constraint. Leave out narration of an obvious
assignment, and do not use a comment to excuse unnecessary complexity.

“Keep pending work visible so later submissions cannot reuse partial state after an
interrupted run” preserves a reason; “set the pending flag” merely repeats the
operation.

- Recheck nearby claims when behavior changes: correct stale explanations and remove
  obsolete narration.
- Preserve legal notices, generated-code warnings and useful safety/compatibility
  rationale under the project's conventions.
- A lint rule may flag comments that only restate the code. Comments for constraints the code cannot express stay; do not ban comments.
- Do not apply blanket comment deletion or a required comment count.
- Keep changing CI status and long experiment history in existing evidence/docs.
- A link may supplement essential rationale; it does not replace it.
- When a comment claims a constraint ("do not remove", "must run before X"), offer to turn it into a type, an assertion or a test, and delete the comment only once that check exists; if it cannot be enforced, the comment stays.
- Comments are claims, not enforcement. Critical behavior still needs the
  appropriate code structure, assertion or regression.

## Comment pass on a diff

Run this only when a review is asked for or the project's rules call for it. It is a
separate read of the diff, by a reviewer who did not write it, looking only at
comments and suppressions. Every comment the diff adds or touches gets one verdict:

- **Cut** when it restates the code, narrates history ("changed from X"), tracks
  work ("TODO later" with no owner or issue) or explains a name that could be
  renamed instead.
- **Keep** only with a reason the reviewer can state: it records something the code
  cannot change or show, such as an external API's quirk, a protocol or compatibility
  constraint, a legal notice or a measured trade-off. "It might help someone" is not
  a reason.
- **Fix the code instead** when the comment describes a workaround ("retry because
  this sometimes fails", "guard against null here just in case"). A workaround note
  points at a cause; find it with [fix root causes](../../how/references/principles/fix-root-causes.md)
  rather than keeping the guard and its explanation. A retry with backoff for a
  failure the boundary really has, such as a network timeout or a rate limit, is
  recovery, not a workaround: keep it and name the failure it handles.

Suppressions are comments with power. Check every `eslint-disable`, `@ts-ignore`,
`@ts-expect-error`, `# noqa`, `# type: ignore`, `#[allow(...)]`, `//nolint` and the
like that the diff adds. One that hides a correctness, type-safety or security rule
is a finding to fix, not to justify. A negative type test that uses
`@ts-expect-error` to prove a bad call is rejected is the check itself, not a
suppression. One that silences a style rule needs a short
reason beside it and the narrowest scope the tool allows: one line and one rule,
never a whole file.

Report the verdicts as review findings; the author applies them. A clean pass may
remove most new comments, and that is a normal result, not a target.

With authorized edits, move durable review reasoning into clearer code or a source
comment; review-only work changes neither. Use the
[review feedback contract](../../orbyn-workflow/references/engineering-scope.md#actionable-review-feedback)
for findings and delivery authority.

Reviewed inspiration, 27 October 2026:
[Google review criteria for comments](https://github.com/google/eng-practices/blob/3bb3ec25b3b0199f4940b1aa75f0ac5c5753301c/review/reviewer/looking-for.md#comments).
This independently authored guidance imports no upstream text or tooling.
