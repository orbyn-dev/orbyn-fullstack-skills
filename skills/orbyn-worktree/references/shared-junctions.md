# Preserve shared junction targets when retiring a worktree

Use on native Windows for an authorized whole-worktree retirement whose ignored
dependency or Cargo build directories are junctions to storage outside that tree.
The default remains hold. Cache-only and output cleanup continue to hold links;
this option never evicts their shared targets.

## Inspect the exact tree

Confirm the finished source, actual landing, owner release, dependent work, host
attachments and important evidence through the normal retirement gate. Then use
the explicit whole-worktree option on a scoped report and dry run:

```sh
python3 worktree_retire.py report --workspace <library> --only worktree --within <finished-tree> --preserve-shared-junctions --scan-depth deep --json
python3 worktree_retire.py apply --workspace <library> --only worktree --within <finished-tree> --preserve-shared-junctions --scan-depth deep --dry-run --json
```

Only exact Git-ignored, untracked generated-cache references with a verified source
manifest/lockfile are considered. The native plan records the link object identity,
raw reparse data, parent identities and external target identity. Root/ancestor
reparses, source links, unknown tags, volume mount points, UNC, relative, dangling,
internal or ambiguous targets stay held. A directory name alone is insufficient.
Current and pinned repository restrictions, holds, hidden Git work and source
ownership still apply.

Report and usage checks treat validated entries as leaves. They do not enumerate
the shared target or include its bytes in the retirement estimate. Shared target
storage remains with its existing owner. Incomplete scans and changed bindings
remain held; increasing a scan budget grants no disposal permission.

## Retire with reference preservation

After reviewing the fresh report and dry run, repeat the scoped `apply` without
`--dry-run`. The documented exact-path `retire` closeout also accepts
`--preserve-shared-junctions`; its usual idle-age exception changes no other guard.

Before writing, the helper rejects archive overlap with the source or shared targets.
It requires an existing ordinary, nonempty archive root and pins its ancestors and
an existing ordinary child across receipt creation. Missing, empty or unsafe roots
remain held without creating anything. The helper writes a uniquely prefixed
`RETIRED.json` and recovery journal directly into that root before moving a link.
Using a no-follow native handle, it renames the junction object into the same root
as a uniquely prefixed `junction-*` entry. It never passes the target path to a mutation API,
unlinks the junction or asks the Shell to recurse through it. The now ordinary
worktree folder is recycled through the existing guarded native method. Confirmed
recycling and source disappearance precede clearing that one Git registration.

Shared target namespace pins permit ordinary file readers and writers. A conflicting
directory deletion/rename holds retirement; another directory rename can be denied
briefly while the namespace is pinned. Keep this operation short and exclude known
active owners. Attribute writes can still retarget a reparse point, so identity and
raw data are checked around moves. These checks are snapshots, not fencing for an
uncoordinated source writer.

## Failure and recovery

On refusal or cancellation, the helper restores staged link objects only into
their original verified vacant parents. A changed parent, occupied destination or
missing source is a recovery conflict: retain the archive and journal, preserve
Git registration, report actual state and stop. Never overwrite a replacement,
delete an archived link or retry an uncertain teardown blindly.

The recovery archive is protected by `KEEP.txt` and `HOLD.json`; it contains actual
junction objects plus uniquely prefixed `junction-escrow.jsonl` journals, not copies
of shared data. Existing root retention markers are preserved; the recovery hold
protects the archive root until reviewed. Restoring
the ordinary folder from the Bin does **not** restore those references or Git's
former worktree metadata. Reattachment needs review of the saved source/target
identities and the journal before moving the retained link objects back. Preserve
that archive until the owner resolves recovery; an expired hold remains report-only.

### Synthetic failure

A finished tree has `web/node_modules` pointing to another checkout's dependency
folder. During retirement its original `web` parent is replaced. The helper keeps
the link object and journal in the protected archive instead of overwriting the new
parent. The shared dependency folder remains untouched, and registration remains
until the incomplete retirement is reconciled.
