# Choose bounded inspection depth

`cleanup.scan_depth` selects an inspection budget independently of cache and output
profiles. The default `standard` allows up to 400,000 entries and 60 seconds for
metadata, source activity or discovery inspection, and 30 seconds for native usage.
`deep` allows up to 2,000,000 entries and 300 seconds for those inspections and
native usage. Limits apply per stage, unit and native callback, not to the whole
command; a blocked operating-system call can exceed a wall-time budget.

Select deep deliberately when bounded inspection needs more time, using an owner
file or a one-run command:

```sh
python3 <workflow>/scripts/harness.py setup-cleanup --target <repo> --scope repo --scan-depth deep
python3 <workflow>/scripts/harness.py setup-cleanup --target <repo> --scope workspace --scan-depth deep
python3 <workflow>/scripts/harness.py setup-cleanup --target <repo> --scope repo --class output --profile moderate --scan-depth deep
python3 worktree_retire.py report --workspace <library> --only artifact --within <idle-worktree> --scan-depth deep --json
python3 worktree_retire.py apply --workspace <library> --only artifact --within <idle-worktree> --scan-depth deep --dry-run --json
```

A target's current, primary, linked or pinned policy can restrict deep to standard;
repository data cannot raise an owner's selected depth. Missing or unreadable policy
proof holds the relevant candidate. Report requested and effective depth, source,
bounds and incomplete inspections. Fresh pre-move checks resolve policy again.

Depth adds inspection effort only. It never changes age or cleanup profiles, grants
disposal permission, expands supported kinds or weakens identity, handle, source,
HOLD/KEEP, reparse or provider checks. Budget exhaustion, busy and unknown still hold.
Do not silently choose deep to reach a reclaim target, terminate consumers or empty
the Trash. Even a complete eligible scan does not guarantee physical free space.

Windows usage probes inspect metadata with delete-sharing checks rather than
requesting file-content reads. Keep the bounds in force when diagnosing a slow
folder: a timeout still means incomplete inspection. The optional whole-worktree
[junction preservation](shared-junctions.md) treats approved references as leaves;
it does not expand cache eviction into their shared targets.
