# Prove a rewrite against the original

Use when a unit, service or whole app is rewritten, in another language or the same
one, and "done" means "behaves like before". The original's recorded behaviour is the
specification. A rewrite with no recording can only be argued about, never checked.

## Settle the equivalence bar first

Ask the user before recording, and write the answers into the task record:

- What counts as equal: exact bytes, or equal after named normalisation (key order,
  float formatting, whitespace, error text).
- Which edges are contract: exit codes, error messages, log lines, config and wire
  formats, flags, routes and status codes.
- For numeric code, a float tolerance the user chose. Integers, strings and `3` versus
  `3.0` still match exactly.

## Record from the outside

- A CLI: arguments, stdin and environment in; stdout, stderr, exit code and written
  files out. A server: requests in; status, the headers that matter and bodies out. A
  library: calls to the public API with their results and errors.
- Take inputs from the existing tests, documented examples, real workloads and every
  deliberate failure (`throw`, `raise`, an error return). Add the traps of the
  [source and target languages](../../language-best-practices/references/rust.md#porting-into-rust),
  such as non-ASCII text, negative division and large integers.
- Run the original twice on every case. Where its own runs differ, the difference is
  noise: a changed value (time, id), the same lines in another order, or a changed
  number of lines. Normalise noise in the recorded case, never in the new code, and ask
  the user whether each order or line-count difference is contract. Those are exactly
  the cases a rewrite can really break.
- The original must replay 100% against its own recording before any unit moves.
- When the original has no tests, keep the recording: it is now its characterization
  suite.

## Move one unit at a time behind a front door

1. The new entry point owns the edge early and hands everything not yet ported to the
   original: re-run the old CLI with the same input, or proxy unported routes. It must
   replay 100% before the first unit moves.
2. Track the **fallback rate**, the share of cases the original still answers. It
   only goes down.
3. Port each unit faithfully first, with the original's structure and error paths.
   Make it idiomatic only after replay passes, then replay again.
4. Keep a per-unit switch back to the original until the last fallback is removed.
5. When replay goes red, rerun only the named case, change one thing, get it green,
   then rerun everything.

Units with no dependency on each other can go to children in parallel; the lead wires
and replays them one at a time, and turns each repeated mistake into a recorded rule
or test.

## Rules that keep the proof honest

- A mismatch is a bug in the new code. Change a recorded case only with the user's
  approval, and write down why.
- Never widen a tolerance or normaliser to make a mismatch pass.
- Every deliberate failure in the original is reached by at least one recorded
  failing case. A crash in the new code never matches a recorded error.
- Recordings cover only what they exercised. Report the inputs and edges they did not
  reach, next to the replay counts.
- After the last fallback is gone, run replay in CI against the new build alone. Only
  then may structure diverge from the original's.

Report replay counts, fallback rate, speed against the original where speed was a
goal, and every known behaviour difference. The user decides the cutover and when the
original is retired.

## Tools

Use the project's existing recording or snapshot tool first. A project that already
has [rust-anything](https://github.com/erkamyaman/rust-anything) installed for a
port to Rust can use its `record`, `diff`, `app replay` and `gate` steps as this
method's evidence. Installing it adds an MCP server to the user's agent configs, so it
needs the user's approval like any other install.

## Source

Adapted from rust-anything by Erkam Yaman, MIT,
[`skills/rust-anything`](https://github.com/erkamyaman/rust-anything/tree/94a9f2d012c68cd1ad8067f21037357cdd6bcf5f/skills/rust-anything)
at `94a9f2d`, observed 18 November 2026; licence in
[`rust-anything-LICENSE.txt`](rust-anything-LICENSE.txt). Rewritten for any
language pair, with the tool optional.
