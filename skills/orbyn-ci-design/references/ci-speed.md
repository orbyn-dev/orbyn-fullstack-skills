# Making CI faster

Use when a pipeline is slow and the [make it fast](pipeline-structure.md#make-it-fast)
basics are in place. Every rule here came from a measured run; measure yours the
same way before and after.

## Measure where the time goes

List each job's steps and their times from the run itself (for GitHub,
`gh api repos/<owner>/<repo>/actions/runs/<id>/attempts/<n>/jobs`), for a cold and a
warm run. The time is often not where you guess: one slow Windows test job was
spending it on process start-up across 1,600 tests (summed test time 4,620 s
against 396 s on macOS), not on compiling.

Report the required check's wall time, the PR's time to green and the main
branch's time separately. Say what each change costs too, such as runner minutes
added to PRs that used to skip a platform.

## Build once, fan out

Build the binaries or service once in one job, upload them as an artifact with
short retention, and have every test shard download them. Shards that each compile
the same thing multiply the slowest step.

## Shard by measured duration

- Split by recorded duration, not file count: greedy longest-first over a checked-in
  durations file. Each shard uploads a timing report so the file can be refreshed.
- Fail a shard that receives no tests.
- More shards help only until fixed setup (checkout, toolchain, download, often
  1-1.5 min) dominates. Stop adding shards when the setup is bigger than the test.
- Use the runner's own partitioning when it has one (for example cargo-nextest
  `--partition count:N/M`). Run once-only work such as doc tests on one shard.
- After sharding, confirm the summed test count across shards equals the old
  single job's count.

## Keep a required check stable

A sharded required check sits behind one aggregate job with the exact required name.
It runs with `if: always()` and passes only when every job it needs is `success`.
In GitHub rules a skipped job counts as passing, so the aggregate must never skip.
A PR with nothing relevant goes green through steps that skip inside each job, not
through the job itself skipping.

Splitting a job can make the required check slower: each new job adds queue and
setup time on the critical path. Compare the required check's wall time before and
after, not just the work per job. Close the change if the critical path got longer.

## Caches and build settings

- **Warm every new cache key on the default branch.** PRs cannot read each
  other's caches, so a new key or profile is cold on every PR until a main-branch job
  fills it. Add a main-only job that builds it. Use a key that does not embed the
  job id (for example rust-cache `shared-key`). Without a warm job, one first PR run
  went from 8 to 13 min.
- **Settings that change only for test jobs** (linker flags, debug info level) change
  the compiler's fingerprint. Give those jobs their own cache key so they do not
  thrash a cache shared with other jobs.
- **A CI-only build profile** may trade runtime speed for build time on PRs, for
  example inheriting release with link-time optimisation fully off and more codegen
  units. Check what "off" means for the tool (in Cargo, `lto = false` still does
  thin local LTO; `"off"` disables it). Main, nightly and release keep the real
  profile. Name what the PR build no longer covers, such as a different linker.
- **Cache downloaded build tools** (installers, bundler helpers), keyed on the tool's
  version. This also removes a flaky download from the path.

## Path filters

List exactly which paths trigger each platform and job, and review the list when a
new area appears; a missing pattern can silently skip a platform's tests for weeks.
A test in one job that reads another job's sources breaks when the other job is
filtered out: either add those paths to its trigger or remove the dependency.

## Merging on green

An automated merge waits for the whole workflow run to finish (for example
`gh run watch`), not only for the required list. A required check whose job does not
exist yet, such as an aggregate waiting on a re-run shard, is missing from the
required list, so a watcher that sees nothing pending merges too early.

## Re-runs

The [rerun rule](pipeline-structure.md#make-it-fast) holds: only for a named
infrastructure failure, after the run finishes, with the cause in the PR. A flaky
test gets an issue and a real fix. When the owner explicitly asks for speed, one
re-run of a known flake that already has an issue is acceptable; the fix still
lands separately.
