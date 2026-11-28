# Blueprint, benchmark ledger and PGO

Use this after the source commands are confirmed and the baseline runs. It holds
the blueprint sections, the measurement record and the optional LLVM PGO recipe.

## Blueprint

Write a short Markdown blueprint in the task record. Six sections are enough:

1. **Source and baseline.** Repository or path, revision, language and toolchain,
   build/run/test commands, workload, seeds, hardware and OS, measured wall time,
   peak RSS and any build blocker.
2. **Target architecture and symbol map.** Crate or workspace layout, module
   ownership, public API, error model, concurrency and memory ownership. Map each
   source module to its target module, and mark code that stays behind FFI.
3. **Parity contract.** What counts as equal: exact bytes, normalized output,
   float tolerance, error and exit behavior, logs and written files. Name the
   seeds, edge cases and deliberate failures.
4. **Benchmark plan.** Workload, inputs, warm-up, run count, median, peak RSS,
   hardware and compiler flags. One baseline stage and one final stage are enough;
   add intermediate stages only when they answer a question.
5. **Slice order.** Vertical slices that can be replayed independently, each with
   its fallback and its acceptance case.
6. **Open questions.** Unknown behavior, missing tests, licenses, platform
   differences and data that cannot leave the source environment.

Keep the blueprint in the task record. Do not paste private source, credentials or
customer data into a public skill file.

## Benchmark ledger

Record every measurement in one table, in the task record:

| Stage | Revision | Command and flags | Workload | Runs | Median wall | Peak RSS | Parity |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `legacy_baseline` | source commit | build and run command | named input set | 5 | measured | measured | original recording |
| `rust_safe` | target commit | `cargo` command | same input set | 5 | measured | measured | replay count |
| `rust_optimized` | target commit | changed flags or code | same input set | 5 | measured | measured | replay count |
| `rust_pgo` | target commit | PGO build | same input set | 5 | measured | measured | replay count |

Run the baseline and the final build on the same machine, workload and input.
Warm up, run at least five times, and report the median and spread. A single run is
a sample, not a result. Measure peak RSS with the platform's own tool, such as
`/usr/bin/time -v` on Linux or `/usr/bin/time -l` on macOS, and record which one
was used.

## LLVM PGO

PGO is optional. Use it after the safe port passes parity and the profile shows the
remaining time is in code the compiler can optimize. It does not fix a bad
algorithm, an I/O-bound program or a port that is still behaviorally wrong.

1. **Check the toolchain.** Run `rustc --version`, `cargo --version` and
   `rustc --print sysroot`. The matching `llvm-profdata` lives under
   `$(rustc --print sysroot)/lib/rustlib/<target>/bin/`. If it is missing, install
   the component with `rustup component add llvm-tools-preview`. Prefer that
   binary over an unrelated system LLVM.
2. **Build the instrumented binary.** Use an absolute path and the target triple:
   `rm -rf "$PWD/pgo-data"` then
   `RUSTFLAGS="-Cprofile-generate=$PWD/pgo-data" cargo build --release --target <target>`.
   The `--target` flag keeps the instrumentation flags away from Cargo build
   scripts.
3. **Run the representative workload.** Use the same inputs and number of runs as
   the benchmark. Each run updates the `.profraw` files in the data directory.
4. **Merge the profiles.**
   `"$LLVM_PROFDATA" merge -o "$PWD/pgo-data/merged.profdata" "$PWD/pgo-data"`.
5. **Build with the profile.** Use the same target and release settings, plus the
   profile:
   `RUSTFLAGS="-Cprofile-use=$PWD/pgo-data/merged.profdata -Cllvm-args=-pgo-warn-missing-function" cargo build --release --target <target>`.
6. **Verify and measure.** Re-run the full test suite and parity replay on the PGO
   binary. Benchmark the same workload and record it as `rust_pgo`. Keep
   `pgo-data` out of Git; the profile is workload-specific.

If `llvm-profdata` is unavailable, stop and report the non-PGO result. A build with
`-Ctarget-cpu=native` plus LTO is a different optimization; label it as such and
never report it as PGO.

## Worked failure

A team compares a C++ baseline built with `-O2` against a Rust release build with
`lto = "fat"` and reports a 2.5x speedup. The Rust output is checked on one seed
with a 1e-3 tolerance, while the original's error path returns a different exit
code. The benchmark measures the build settings, not the port, and parity is
unproven. Fix: rebuild the baseline with its documented release flags, use the same
workload and hardware, record several runs, replay the original against its own
recording, and include the error path in the parity cases.
