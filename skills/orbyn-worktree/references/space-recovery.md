# Measure space recovery

A move to the system Trash changes its location and provides **no guaranteed
immediate free-space gain**. Report logical payload moved separately from measured
free space before and after. The observed difference can include unrelated activity;
it is not proof that cleanup caused the change. Compression, sparse files and shared
hard links also prevent a logical byte count from proving physical recovery.

The read-only Recycle Bin aggregate is a volume-wide observation, not a list of safe
items to purge. It can include unrelated user data. Unknown or unsupported capacity
and Bin measurements remain unknown; do not display them as zero or as evidence
that an item can be removed permanently. This workflow grants no authority to empty
the Trash, purge an item, or broaden cleanup scope.

For a synthetic 10 GiB diagnostic target:

```sh
python3 worktree_retire.py report --workspace <library> --reclaim-gib 10 --json
python3 worktree_retire.py apply --workspace <library> --reclaim-gib 10 --only artifact --within <idle-worktree> --dry-run --json
```

`--reclaim-gib` only describes the requested target. It does not change profiles,
age thresholds, protection, action authority or the set of candidates, and the
Trash-only workflow cannot guarantee the target. Inspect selected, excluded and
unknown-size paths and the drive-group estimates; an estimate on another drive does
not meet this drive's need. Windows drive groups do not identify mounted-volume
boundaries, and POSIX paths share the `/` group; these are logical reporting groups,
not filesystem-level capacity accounting. Size coverage counts eligible estimates
only; inspect held candidates separately for missing or incomplete measurements. A dry run moves nothing. A real apply still requires
ordinary authority, a reviewed exact scope and fresh checks.

Native usage inspection reports bounded status and reason codes when available,
including busy, access failure and budget exhaustion. Unknown remains held. Performance
changes must preserve the same bounded inspection, live handle checks, identity and
reparse protections; a faster probe or older timestamp is not proof of idleness. Do
not silently increase budgets, terminate consumers or discard safety checks merely to
reach a diagnostic target. An owner can explicitly select [bounded deep inspection](deep-scan.md)
without changing cleanup eligibility or guaranteeing recovery.
