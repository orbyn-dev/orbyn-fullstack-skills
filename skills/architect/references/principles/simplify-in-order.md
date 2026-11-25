# Simplify in order

For any change you cannot check at a glance, answer five questions in this order.
Each one only pays off once the one before it is settled. Tuning code that should
have been deleted, or automating a step nobody needs, is wasted work that looks like
progress.

1. **Is the requirement right?** Treat every requirement as someone's guess. Trace it
   to a person and their reason. "The ticket says so", "the linter wants it", "a
   reviewer asked" and "the old code did it" tell you where a requirement came from,
   not why it should hold. Where the reason does not support part of it, propose
   narrowing or dropping that part to whoever owns the requirement, with your
   evidence, and carry on with reversible work meanwhile. The owner decides; the user
   decides their own scope. Required checks, review requests, safety and permission
   rules stay in force until their owner agrees otherwise.
2. **Can it go?** Before improving anything, try to remove it: the flag, layer,
   retry, field or process step. Check its callers and contracts first. Never remove
   user data, meaningful tests, public compatibility, or a safety, permission or
   review check to make the change smaller. Within those limits, cut boldly: if
   nothing ever has to come back, you probably removed too little
   ([subtract before you add](subtract-before-you-add.md)).
3. **Can what is left be simpler?** Only now simplify. Let one owner do several jobs
   instead of keeping a copy per caller, and look hardest at the seams between owners
   and layers, where wrappers pile up ([laziness protocol](laziness-protocol.md),
   [reader load](minimize-reader-load.md)).
4. **Can the loop be shorter?** Take one change through edit, build and the checks
   you are authorised to run, then into review. Note where you waited, repeated work
   or got sent back, and fix the slowest point first. A faster loop never replaces
   independent review ([verifiable units](../../../orbyn-plan/references/principles/sequence-verifiable-units.md)).
5. **Should it be automated?** Last, and only for what survived the first four steps.
   Automate when it makes the work more precise or easier to review; volume alone is
   not a reason ([build the lever](../../../orbyn-workflow/references/principles/build-the-lever.md)).

Then run all five again on the result before you ship. The second pass usually finds
more to cut.

## Show that you did it

When you cite this principle in a plan, PR or report, name the requirement you
questioned and whose it was, and what you deleted. "Checked and kept, because …" and
"nothing removed, because …" are honest answers; never invent a cut. A citation with
no requirement and no deletion, and no reason why, means you skipped the steps.

## Decision in practice

A ticket asks for a nightly job that retries failed calendar syncs three times with
backoff, plus a dashboard of retry counts. An agent builds the job, tunes the backoff
and adds the dashboard: steps 3 to 5, done first. Going in order instead:

1. The ticket came from support, and their actual need is "users stop seeing stale
   events". Retries and a dashboard are one guess at how to get there.
2. The failures come from an expired token that the sync never refreshes. Refreshing
   it at the sync call removes the cause, so the retry job and the dashboard go.
3. Steps 3 to 5 now apply to one call path instead of a new subsystem.

Citation: "Narrowed support's retry request to 'no stale events'; deleted the retry
job and its dashboard."

## Apply and hand off

Skip this for a small edit whose effect you can see at once. Otherwise state, before
building, the requirement each part serves and what you tried to remove, and keep
that in the plan or PR. Return the narrowed requirement, the deletions and what
remains. Carry execution through [architect](../../SKILL.md) or
[orbyn-plan](../../../orbyn-plan/SKILL.md). This principle refines that task; it
does not create another mandatory phase.

## Source

Our own wording of the five-step engineering process Elon Musk has described in
public interviews, proposed in chat for this library. Each step links to an existing
principle here; no text was taken from elsewhere.
