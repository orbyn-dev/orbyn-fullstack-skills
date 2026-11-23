# Audit an existing test suite

Use when asked to review, prune or speed up tests that already exist, or when a suite
grew through generated tests. For the next regression, use the method in
[SKILL.md](../SKILL.md). Audit first and change nothing: the output is a list, and
deleting or rewriting is a separate step the person approves. Respect test exclusions
and run only the project's own test command.

## Ask what mistake each test would catch

For every test or group, name the wrong behavior that would make it fail. If there is
none, it is a candidate. Use the [two edits](test-strategy.md#judge-a-test-by-two-edits):
break the behavior and see whether it fails, change the internals and see whether it
survives.

## Five weak patterns

| Pattern | The tell | Fix |
| --- | --- | --- |
| Tautological | The expected value comes from the same helper, constant or path the code under test uses | Take it from the requirement or a worked example; delete if nothing independent exists |
| Empty setup | A boundary test where nothing exists to cross the boundary, such as an empty list for "only my data" | Create an allowed item and a forbidden one; assert both outcomes |
| Fake unlike the real thing | The mock or fake behaves differently from the real dependency, such as an HTTP error status that makes a fake reject when the real client resolves | Make it return what the real one returns, or check it once against the real one |
| Structure-sensitive | Asserts class names, call order, private calls or internal counts, so a same-behavior refactor breaks it | Assert the behavior the requirement states, or delete |
| Broad error | Any error satisfies it, including a setup failure | Name the type, code or message that is the contract, and confirm setup reaches the behavior |

Tighten before deleting: a weak test over a real risk is worth more fixed than removed.

## Move behaviour to the edge, then cut

Once the person approves the audit list, work through it. Most generated suites are
unit tests of helpers. For each group, name the behaviour it
protects and check whether a [golden case](golden-cases.md) at the program's edge covers
it. If none does, add that case first, see it fail on the broken behaviour, then delete
the unit tests it covers in the same change. Keep a unit test only when it meets the
three conditions in [SKILL.md](../SKILL.md#choose-the-test-black-box-first). A behaviour
must never be left with no test between the two steps, and required repository
checks stay unless their owner agrees.

## Keep by risk, not by count

Before judging tests, list the risks this suite must guard, using the project's own:
access across tenants or users, permissions, secrets in logs, money, external input,
recovery after a crash without repeating an effect. Keep or rewrite every test tied to
a listed risk, and every test that caught a real bug in recent history (look in Git, not
memory). Justify a deletion per group, not per file. Merge files only where layer and
fixtures match.

## Record what you give up

Keep a short ledger: group removed, why, the risk it touched, what still covers it and
what is now untested. Behavior outside the risk list that loses its test is a decision
to state, not a hidden loss. Measure before you cut: which tests take the time, and
which overlap. When claiming speed, compare test count, required-check wall time and
runner minutes before and after, and say the gain came from the whole cleanup (deleting,
merging, rebalancing), not from deletion alone.

## Worked example (synthetic)

A `listNotes('alice')` test starts with an empty store and expects `[]`. Returning every
note would pass it. Rewrite: create one note for Alice and one for Bob, and assert the
result is exactly Alice's. Ledger line: "notes isolation, rewritten, covers cross-user
access, still untested: ordering."

Static reading cannot prove a test is tautological. Before deleting a test that guards a
listed risk, make the break-the-behavior edit and see that it still passes.
