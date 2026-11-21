# Reversible compression during Aggressive cleanup

Use when an authorized Aggressive cleanup needs physical space on Windows C: or
D: while retaining idle compiler output. This is a separate `compress` action;
Trash cleanup and worktree retirement retain their existing gates. Safe, Moderate,
legacy and off profiles never compress. Compression defaults to disabled.

## Configure exact private roots

Save the user's chosen compiler cache directories in the ignored owner harness,
per repository or for the workspace. Tracked repository policy can disable
compression or intersect those roots; it cannot enable it or add roots. Repository
opt-outs and pinned-base policy still apply. A profile or configuration file is
policy, not permission to act: the current cleanup request must authorize effects.

```sh
python3 <workflow>/scripts/harness.py setup-cleanup --target <repo> --scope repo --profile aggressive --compression on --compression-root "C:/Users/<user>/.cache/<task>/cargo-target/debug/deps" --compression-root "D:/<repo>-wt/<task>/target/release/deps"
python3 <workflow>/scripts/harness.py setup-cleanup --target <repo> --scope workspace --compression off
```

The stored keys are `cleanup.compression_enabled` and
`cleanup.compression_roots`. Exact roots must be absolute C:/D: subdirectories;
whole drives, UNC paths and wildcard discovery are refused. Choose confirmed
private build/cache roots, not source, shared dependency stores or evidence.

## Inspect, compress, verify

For an authorized Aggressive cleanup, run this separate step after assessing
normal cleanup candidates. Use the verified helper by absolute path:

```sh
python3 worktree_retire.py compress --workspace <library> --target <repo> --cache-profile aggressive --dry-run --json
python3 worktree_retire.py compress --workspace <library> --target <repo> --cache-profile aggressive --json
```

Dry run writes no receipts and changes no files. Apply freshly checks policy,
root/file identities, protection and usage. It records the plan before the first
mutation, preserves per-file outcomes, and refuses blind repetition of an existing
receipt. Reconcile an interrupted receipt against the files and current policy
before planning another pass; do not replay stale plans.

Only recognized Cargo compiler objects (including release intermediates), private
Go build entries and Node compile-cache entries qualify. Source, executables,
shared package stores, data volumes and evidence stay. Files must be ordinary,
single-link, untracked, at least 64 KiB and idle for at least seven days. Newer
output in a root, recent source-owner activity, tool locks, unreadable inspection,
budget exhaustion or uncertain usage holds the root. HOLD/KEEP protection,
nested Git, secret-looking paths, junctions/reparse points and hardlinks hold it.
Already compressed, encrypted, sparse and offline files are skipped.

Use native NTFS file-handle compression with ancestors and file identities pinned;
exclude writers while hashing and changing allocation. Never set directory
inheritance or recursively compress a directory. Hash every file before and after,
retain names/content/identity, and stop on unexpected changes or native failure.
No deletion, move, process termination, elevation, Recycle Bin action or permanent
fallback belongs to compression. Windows APIs can block beyond inspection budgets;
keep uncertainty visible and avoid retry loops.

Report actual per-file allocation before/after using `FILE_STANDARD_INFO`, plus
free space by drive. Candidate footprint is an upper bound, not predicted savings.
A junction on D: pointing to C: cannot yield D: savings. Keep source/evidence and
private receipts under `output/cleanup-receipts/`; never commit local inventories.

Disable future compression with `--compression off`. Existing compressed files
remain readable; decompression is a separate authorized operation on the recorded
exact files and requires sufficient free space. Do not decompress a whole tree.

API references, reviewed 2026-11-21 without copying source:
[FSCTL_SET_COMPRESSION](https://learn.microsoft.com/windows/win32/api/winioctl/ni-winioctl-fsctl_set_compression)
and [FILE_STANDARD_INFO](https://learn.microsoft.com/windows/win32/api/winbase/ns-winbase-file_standard_info).
