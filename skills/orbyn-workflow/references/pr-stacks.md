# Stacked pull requests

A stack is a chain of PRs in one repository: the bottom PR targets the trunk (usually
the default branch) and each PR above targets the branch below it, so every PR shows
only its own layer's diff. Use one when the next change depends on code that is not
merged yet; independent work goes in its own PR from the trunk instead
([build while waiting](../playbooks/build-while-waiting.md)).

## Design the layers

- One concern per layer, ordered by dependency: shared types and schema at the
  bottom, the code that uses them (API, then UI) above, tests with the layer they
  test.
- A layer may depend only on its own branch or lower ones. Each layer is reviewable,
  builds and passes its checks on its own.
- Keep stacks short: at most two PRs deep by default, and within the lead's open-PR
  limit ([stack limits](../playbooks/build-while-waiting.md#stack-only-what-depends-on-n)).
- Stacks are linear. Two pieces of work that both depend on the same base but not on
  each other are two stacks, or one waits.

## Choose the mechanism

1. **GitHub native stacks, when available.** GitHub links the PRs as a stack, shows a
   stack map, runs the trunk's required checks and branch rules on every layer, and
   rebases the remaining layers after a lower one merges. All branches must be in the
   same repository (no forks). Use the `gh stack` extension when it is already
   installed; installing it (`gh extension install github/gh-stack`) is a tool
   install, so ask the person first. Its non-interactive commands, exit codes and
   recovery steps are in the pinned [gh-stack skill](upstream/gh-stack/SOURCE.md).
   Exit code 9 means stacks are not enabled for the repository: tell the person.
2. **Plain Git otherwise.** Cut each branch from the one below, open each PR with the
   branch below as its base, and keep them current with
   `git rebase --update-refs <trunk>` from the top branch (Git 2.38 or later), then
   `git push --force-with-lease` for each changed branch. Name the base PR in each
   description, since GitHub shows no stack map.

## Authority

- Opening a stack opens several PRs at once; it needs the same PR authority as one.
  With `gh stack submit --auto`, new PRs open as drafts unless `--open` is passed.
- **Merging a layer merges every unmerged layer below it** (native stacks merge from
  the bottom up; merging a mid-stack PR takes the ones below with it). Check that
  merge authority covers every PR that would land. In this skill library, no PR an
  agent asked for may be merged by an agent, so a stack containing one waits for a
  human.
- With a merge queue, merging queues the stack instead; queued is pending, not landed.
- Restructuring commands that rewrite or dissolve someone else's stack (`unstack`,
  `modify`) need that stack owner's agreement.

## Keep it current

- After the trunk moves or a lower layer merges (often as a squash), sync or rebase
  the stack (`gh stack sync`, or the plain-Git rebase with `--onto` for a squashed
  parent), then repeat each moved layer's affected checks: earlier results no longer
  apply ([dependent stacks](../playbooks/pr-iteration.md#dependent-stacks-and-current-context)).
- A fix that belongs to a lower layer goes on that layer, then the layers above are
  rebased. Never patch a lower concern on the top branch.
- Stack branches may live in separate worktrees; never steal another worktree's
  checkout ([orbyn-worktree](../../orbyn-worktree/SKILL.md)).

## Review

Each layer gets its review before its PR opens, like any PR
([review before the PR](lead-contract.md#review-before-the-pr)); the reviewer reads that
layer's diff against the branch below, not against the trunk. Re-review a layer whose
contents changed in a rebase beyond a clean replay.
