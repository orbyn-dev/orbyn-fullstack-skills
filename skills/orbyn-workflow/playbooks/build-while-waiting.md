# Keep building while a PR waits

Use when a lead has pushed a PR and it now waits on CI or review, and more work is
queued. Waiting idle wastes the wait; starting unrelated work in the same tree
mixes changes. This playbook overlaps them safely. It grants no merge, push or
publication authority beyond the delivery scope already given.

## Hand over, then start the next item

1. **Leave PR N watched, not polled.** Once N is pushed and its reviewer child (if
   any) is running, arm the host's PR watcher (for example desktop Auto-fix) or
   `pr_watch.py wait` in the background ([watch the PRs you own](pr-iteration.md#watch-the-prs-you-own),
   [wait without polling](../references/waiting.md)).
2. **Check the overlap stops below.** If any applies, wait for N, or take a
   different item that none apply to.
3. **Start item N+1 in its own sibling worktree** cut from freshly fetched default
   branch ([orbyn-worktree](../../orbyn-worktree/SKILL.md)). Never build N+1 in N's
   tree or on N's branch.
4. **When the watcher wakes, finish N first:** read the event, fix on N's branch,
   re-push and, only within the merge authority you already have, land it. Then
   return to N+1. A waiting PR always outranks starting new work.

## Stack only what depends on N

- Work that needs N's code is cut from N's current head, and its PR names N as its
  base. Never cut it from main and copy N's changes in.
- When N merges (often as a squash), rebase or retarget the child onto the new
  default branch and repeat its affected checks; the earlier results no longer
  apply ([dependent stacks](pr-iteration.md#dependent-stacks-and-current-context)).
- Defaults a project policy may lower or raise: a stack at most **2** PRs deep, and
  at most **3** open PRs per lead. Past either limit, finish and land before
  starting more.

## Slots

Each worktree counts toward the harness's `max_active`; workers stay within
`max_children`. The reviewer child for PR N uses the separate reviewer slot, so
it can run while a worker builds N+1. The lead integrates and reviews; it does
not write in a tree a child owns, and a reviewer reads a frozen head, not one that
is still being pushed to.

## Overlap stops

Wait for N, or serialize, when:

- N+1 would edit any file N edits;
- both touch a shared migration, schema, lockfile, generated bindings or other
  generated file that only one branch can own at a time;
- the default branch is red, so a new tree would start from a broken base;
- N's review could change a design or contract that N+1 builds on;
- the open-PR or stack limit is reached, or `max_active` has no free worktree.

## Shorter waits

The best overlap is a short wait. Make the required checks cheaper and let the
host merge on green: affected checks only, a merge queue or native auto-merge,
per [making CI faster](../../orbyn-ci-design/references/ci-speed.md).

## Example

A lead has four queued items. A is pushed and waiting on CI; its reviewer child
runs. B is independent, so a worker builds B in a fresh tree from main. C extends
A's new endpoint, so C is cut from A's head with A as its base. D changes the
database migration A adds, so D waits. The watcher wakes on A's review comment:
the lead fixes A and re-pushes, then reviews B. A merges as a squash; C is
rebased onto main and its checks rerun before its own PR goes up.

**Failure it prevents:** relaunching B into A's tree after a slow CI run, so B's
edits land in A's PR and A's verified head changes under its reviewer.
