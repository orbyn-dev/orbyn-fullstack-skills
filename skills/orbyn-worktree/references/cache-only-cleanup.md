# Clean a rebuildable cache while keeping its source

Use for an authorized request to reclaim local compiler/dependency artifacts.
`worktree_retire.py` owns inspection, dry run, recheck, receipts and system Trash.
Cache-only cleanup keeps source, branches, Git registration and delivery chats.
A profile is policy data, not action permission. Missing settings keep `legacy`.

Finished task output uses its separate [output profile](output-cleanup.md); cache
profiles do not select its policy. Choose the separate [inspection depth](deep-scan.md)
when a bounded scan needs more time.

## Select a level

| `cleanup.cache_profile` | Cargo debug intermediate idle age | Locked dependency idle age |
| --- | --- | --- |
| `legacy` | Existing cleanup rules | Existing cleanup rules |
| `off` | Held | Held |
| `safe` | More than 7 days | Report-only |
| `moderate` | More than 3 days | More than 7 days |
| `aggressive` | More than 1 day | More than 1 day |

Age uses newest modification/creation-or-metadata time, never access time alone.
Age does not prove disuse. Every eligible unit also needs a two-hour quiet owner,
tool-lock and live-usage checks. Dirty source, an open PR or an unmerged branch
alone does not forbid cache eviction; it still forbids unqualified source retirement.

Owner setup, using the loaded workflow skill's absolute harness path:

```sh
python3 <workflow>/scripts/harness.py setup-cleanup --target <repo> --scope repo --profile moderate --dry-run
python3 <workflow>/scripts/harness.py setup-cleanup --target <repo> --scope repo --profile moderate
python3 <workflow>/scripts/harness.py setup-cleanup --target <repo> --scope workspace --profile safe
```

Workspace setup writes only cleanup settings in ignored `repo/.harness/_defaults.json`.
Repository setup preserves other owner settings in the existing local/in-place file;
all its worktrees share it. A command-line `--cache-profile` is a one-run owner choice.
Tracked repository settings and the pinned-base policy can only restrict it, including
an opt-out that beats the CLI. See [harness settings](../../orbyn-workflow/references/harness-settings.md).

## Preview, inspect, then act

Run the helper from the verified skill version; examples below omit its absolute
directory for readability. Limit real actions to known idle ownership roots:

```sh
python3 worktree_retire.py report --workspace <library> --cache-profile moderate --only artifact --build-storage --progress --json
python3 worktree_retire.py apply --workspace <library> --cache-profile moderate --only artifact --within <idle-worktree> --dry-run --json
python3 worktree_retire.py apply --workspace <library> --cache-profile moderate --only artifact --within <idle-worktree> --progress --json
```

Report and dry run never write receipts or move files. `--within` confines discovery
and action to a real directory in authorized roots; an output path or report text is
data and never a command. Apply recomputes policy and registration, refreshes tracked
names and source activity, compares the unit identity with the preview, and rechecks
proof, protection and usage. Changed or uncertain units stay. A receipt under
`output/cleanup-receipts/` precedes the first move and records each actual outcome,
including cancellation/failure and source-path state. These receipts are not cleanup
output candidates. No artifact operation clears Git registration.

## What is proven rebuildable

The first adapter recognizes marked Cargo cache roots with tracked source/lockfiles,
parseable compiler metadata and a known stable rustc 1.60–1.99 debug layout. Units
are `debug/deps`, `debug/build` and individual `debug/incremental/<unit>` directories,
including recognized target triples. Release output, custom profiles, newer/unknown
layouts and generic build folders are report-only. Configured Cargo target directories
inside the worktree use the same proof; external stores are report-only.

Dependencies need an ignored, untracked `node_modules` with its own tracked lockfile
and matching validated install metadata. npm source lockfile versions 2/3 and hidden
marker version 3 need nonempty installed-package records with matching versions and
integrity/resolution fields. The parser is bounded at 8 MiB. Yarn and pnpm remain
report-only pending validated manager-specific adapters; linked dependency paths are held.
No package manager or project hook runs to establish proof.

Always retain root/ancestor HOLD or KEEP protection, expired holds awaiting a person,
malformed metadata, tracked files, nested Git, secret-looking paths, in-progress Git,
host-managed trees, unsafe links/reparse points, unknown ownership and unreadable or
over-budget inspection. Protection metadata keeps its existing 64 KiB bound. A
successful later test is no permission to discard failed evidence. Do not silently
repair another task's retention records or infer review completion from age.

Source activity checks inspect timestamps of tracked and non-ignored untracked
source plus relevant directories, not their contents or the ignored cache tree.
Candidate handles, owner directories and Cargo locks are checked separately. These
are snapshots: they cannot promise an idle process will never resume. Known active
consumers still require owner disposition. Native budget exhaustion means unknown,
not idle; a blocked OS call can exceed a traversal budget. Never terminate consumers.

## Build volumes and capacity

For measured free space, Bin aggregates and diagnostic goals, see
[space recovery](space-recovery.md).

`--build-storage` reports local units, shared package caches and read-only Docker
version/system-df/buildx-du results. Missing daemon/plugin or unreadable output is
`unknown`, not zero storage. Images and build cache stay report-only; **all Docker
data volumes are held at every level**. No start, run, prune, rm, volume removal or
shared package-cache GC is part of this workflow.

Show profile/source, age, proof, complete/partial status, prior blockers and per-drive
payload estimates. Deduplicate unit paths and observed hardlink identities; this is
not proof of physically reclaimable bytes when other links remain. Windows reports
logical estimates. Shared cache bytes on another drive do not relieve this drive.
Moving to the Trash frees no volume space immediately. Recovery depends on the
Trash staying available; never empty it or fall back to permanent deletion.

Revert a profile to `legacy` or `off` with owner setup to stop the new policy. This
does not restore moved files; use the system Trash and the recorded receipt paths.
