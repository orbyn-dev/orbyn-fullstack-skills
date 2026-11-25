# Fact-check review findings

Step 7 of the [diff review](../diff-review.md). The checker sees only the group's
diffs and the findings. The reviewer may have read the whole codebase, so anything
the checker cannot see, the reviewer may well have seen.

The job is narrow: remove only findings that the diff **proves** wrong. Usefulness,
priority and whether the finding is worth a reader's time are out of scope. The two
mistakes do not cost the same. Keeping a wrong finding costs a reader a few
seconds. Removing a right one destroys a real defect silently: it never reaches
anyone and nobody learns it was dropped. So when the evidence falls short of proof,
keep it. "Suspicious", "I cannot verify this", "low value", "the code looks fine to
me" and "I would not have raised this" all mean keep. On most reviews the right
answer is to keep everything.

Each finding names a `path`. The diff for that path is its subject; the other files
in the group are context. They can supply evidence a cross-file finding rests on,
but they never stand in for the subject file.

## The only two grounds for removal

**Ground A: the code the finding describes is not in its subject file's diff.**
Judge this against that file alone; the same construct in a sibling file does not
save it. For example:

- it discusses a function body, on a file that only declares or calls the function;
- it discusses program logic, on a query, build, markup or config file that has none;
- it says code was removed or an error is handled, and the subject diff has no such
  change.

**Ground B: one diff line plainly contradicts the finding's central claim.** The
line may be in any file of the group, and the contradiction must be readable
straight off the diff, not reached by a chain of reasoning. For example:

- it calls an identifier unused, and the diff uses it;
- it says a check, assertion or branch is missing, and the diff has it;
- it says a value is hard-coded, and the diff reads it from a variable;
- it says a name is declared twice or shadows an outer one, and the diff has one
  declaration;
- it states a condition or type relationship that the diff's own text refutes.

If you cannot point at the diff line that establishes Ground A or B, keep the
finding.

## Protected subjects: never remove

These veto removal before correctness is even considered. Keep the finding,
whatever you conclude about it, when its subject is:

- **memory safety:** allocation size, buffer length, index bounds, off-by-one,
  use-after-free, null dereference;
- **concurrency:** locks and lock modes, atomics, data races, synchronisation
  arguments that are not honoured;
- **linkage and declaration consistency:** `static` against non-`static`, a
  declaration that disagrees with its definition, a missing `extern`;
- **behaviour or compatibility change:** a message, field, status or default the old
  code produced and the new code no longer does, an altered error path, a counter
  whose update moved to another point in the lifecycle;
- **a parameter the function accepts and never uses.**

These are where a wrong removal costs most and where the checker's confidence is
least reliable, including confidence that the language, compiler or runtime does
not behave as the finding claims.

## Not grounds for removal

- It is about style, formatting, naming, blank lines, comment wording or
  readability, and what it says is true. Low value is not incorrectness.
- It reasons about runtime behaviour, business rules or code the checker cannot
  see.
- You disagree with the recommendation or think the code is fine as written.
- You cannot confirm it. Unverifiable is not wrong.
- It finds a real problem but quotes a slightly wrong line. Judge the claim, not the
  citation.
- It is imprecise in passing while its central claim holds.

## Method

Run these in order for each finding and stop at the first that applies:

1. **Protected subject?** Keep it and stop, even if you are sure it is wrong.
2. **Style or readability, and true of this diff?** Keep it and stop.
3. **Ground A?** Remove it.
4. **Ground B?** Before deciding, search every file in the group for what the
   finding describes, not only the snippet it quoted; a finding that cites the
   wrong line but describes something the diff does contain is right and stays. If
   one line contradicts it with no chain of reasoning, remove it. If you need more
   than one inference to reach the contradiction, there is none.
5. Otherwise keep it.

Return either the removed findings, each with the diff line that disproves it, or
"keep all". Nothing else: no new findings, no rewording, no reprioritising.
