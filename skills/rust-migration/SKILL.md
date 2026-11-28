---
name: rust-migration
description: "Port C, C++, Fortran, Java, Python or shell code to Rust with a recorded baseline, parity-first slices and measured optimization. Use orbyn-plan for plan-only requests, figure-it-out for generic migrations, language-best-practices for Rust edits, and orbyn-stack-selection when Rust is unchosen."
---

# Migrate a codebase to Rust

Use when Rust is already the target and the job is to port existing behavior. Read
the consuming project's instructions, the source build and run commands, and the
target toolchain first. A request for a plan only belongs to
[orbyn-plan](../orbyn-plan/SKILL.md); choosing the language belongs to
[stack selection](../orbyn-stack-selection/SKILL.md). Ordinary Rust changes belong
to [language guidance](../language-best-practices/SKILL.md).

## Method

1. **Confirm the source and authority.** Record the repository or path, revision,
   license, data sensitivity, build system and entry points. Resolve the exact
   build, run and test commands from the project's own files. Show those commands
   and the working directory to the person and get confirmation before compiling
   or running cloned code. Do not run installers, setup hooks or arbitrary scripts
   to inspect a repository. Use a disposable sandbox with no production
   credentials or outbound effects for the baseline.
2. **Record the baseline.** Build the original in its documented release mode and
   run the same workload, seeds and inputs you will use for the port. Record the
   command, compiler flags, toolchain versions, hardware, wall time, peak RSS and
   the output or checksum for each case. Run each case more than once; keep the
   median and the spread. If the original cannot build, record the blocker and do
   not invent a baseline.
3. **Write the blueprint.** Cover the source modules and state, target crate and
   module map, error and ownership model, parity contract, benchmark plan, slice
   order and open questions. Use the [blueprint and PGO reference](references/blueprint-and-pgo.md).
   If the person wants to review the blueprint before code changes, stop here and
   hand it over.
4. **Build the parity harness before the port.** Treat the original's recorded
   behavior as the specification. Follow [rewrite parity](../figure-it-out/references/rewrite-parity.md):
   settle what counts as equal, record from the outside, replay the original
   against its own recording, and keep a front door that falls back to the
   original until each slice is ported. Include error paths, exit codes, edge
   cases and the language traps in the [Rust porting guide](../language-best-practices/references/rust.md#porting-into-rust).
5. **Port vertical slices.** Move one replayable slice at a time. Keep the
   fallback until the slice passes. Preserve the original's observable errors,
   ordering and numeric behavior before making the code more idiomatic. Use safe
   ownership and `Result` for expected failures; do not put `unwrap` or `expect`
   on input or routine I/O. Keep any `unsafe` or FFI block small, documented and
   tested behind a safe wrapper.
6. **Verify every slice.** Run the project's formatter, compiler, tests and
   linter, plus the parity replay. Add property or boundary tests where the
   behavior is numeric, concurrent or input-driven. Use the project's coverage
   threshold if it has one; do not invent a universal percentage. Record each
   failing run and the fix in the task record under
   [show me your work](../show-me-your-work/SKILL.md).
7. **Optimize only a measured hot path.** Profile before changing the design.
   Compare the baseline and the port on the same workload, hardware and compiler
   flags. SoA, SIMD or arena changes need a profile that shows layout, allocation
   or vectorization is the bottleneck. PGO is optional; use the
   [PGO recipe](references/blueprint-and-pgo.md#llvm-pgo) after parity is green.
   Re-run parity after every optimization.
8. **Close out.** Remove the fallback only when the Rust build passes the full
   replay alone. Report the source revision, baseline and final measurements,
   parity counts, remaining behavior differences, unverified edges and whether
   the final build used PGO. The person decides cutover and retirement.

### Worked failure

A team ports a C++ solver to Rust and reports a large speedup. The baseline was
built with different optimization flags, the Rust output was compared on one seed
with a loose tolerance, and the original's error exit code was never recorded.
The benchmark measured build settings rather than the port, and the first edge
seed diverged. Fix: freeze the baseline command and flags, record several runs and
the error paths, replay the original against its own recording, and keep the
fallback until the full replay is green.

## Decisions and boundaries

- **Rust is already chosen.** If the language is still open, use
  [stack selection](../orbyn-stack-selection/SKILL.md) first.
- **Check the source license before porting.** A translation is usually a
  derivative work. If the license does not permit the intended distribution, stop
  and ask; do not copy source text or code into a differently licensed project.
- **A plan is not a port.** A plan-only request belongs to
  [orbyn-plan](../orbyn-plan/SKILL.md). If a port is authorized but the person
  wants to review the blueprint first, stop after step 3. Do not scaffold or
  rewrite code without an execution request.
- **Safe by default, not safe at any cost.** `#![forbid(unsafe_code)]` is a good
  default for a pure-Rust crate. FFI, allocators, SIMD intrinsics and platform
  bindings may need a small reviewed `unsafe` block; isolate it, state its
  invariants and test the safe wrapper. Do not claim memory safety from a lint.
- **Parity is the specification.** A recorded case changes only with the person's
  approval and a written reason. Never loosen a tolerance or add a mask to make a
  failing case pass.
- **Benchmark claims need a method.** Same workload, hardware, toolchain and
  flags; warm-up, repeated runs and a median. A single run or a different
  workload is not a speedup.
- **PGO is not a substitute for a good algorithm.** It is workload-specific and
  optional. If `llvm-profdata` is unavailable, report the non-PGO build. A
  `target-cpu=native` plus LTO build is a different optimization, not PGO.
- **Do not execute untrusted code to inspect it.** Cloned build scripts, hooks and
  installers run only after the confirmation in step 1 and only in disposable
  scope. Keep secrets and production data out of the recording.
- **Stop repeating a failing loop.** If the same failure occurs twice, change the
  approach once; if it still fails, stop and report the failing case and evidence
  under the [loop guard](../orbyn-workflow/references/lead-contract.md#loop-guard).

## Read when needed

- Blueprint sections, benchmark ledger and LLVM PGO: [blueprint and PGO](references/blueprint-and-pgo.md).
- Recording and replaying the original: [rewrite parity](../figure-it-out/references/rewrite-parity.md).
- Rust ownership, numeric, string and regex traps: [Rust guide](../language-best-practices/references/rust.md#porting-into-rust).
- Test design and critical-property checks: [tdd](../tdd/SKILL.md).
- Decision and evidence trail: [show me your work](../show-me-your-work/SKILL.md).
- A replay gate in CI: [CI design](../orbyn-ci-design/SKILL.md).
- Untrusted source or an FFI trust boundary: [security audit](../orbyn-security-audit/SKILL.md).
