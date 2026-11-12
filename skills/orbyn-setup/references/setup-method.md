# Setup method details

Detail for the numbered method in the [entry skill](../SKILL.md). The steps and
authority rules there still apply; this file holds the mechanics, the harness and
model notes, and conflict and recovery handling.

## Steps 5 to 9 in detail

5. **Search roots.** Instruction lookup includes applicable outer, root and nested
   rules even when code filters exclude Markdown or hidden files. Refresh relevant
   locations when paths, rules or available context change. Check
   [ripgrep readiness and fallback](offline.md#ripgrep-readiness-and-fallback) in the
   actual agent environment before relying on `rg`; a missing tool does not authorize
   an install, so use bounded inspection with an available alternative.
6. **Target guidance.** `workspace.py check --target repo/<project>` reports
   overlapping names so you can distinguish target-specific rules from shared methods
   instead of installing a competing copy. It also registers the target as a memory
   project unless memory is opted out. Read the target's root and applicable nested
   instructions, contribution guide, existing skills and actual manifests.
7. **Root the session** in the outer library and state the target path in the task.
   Inspect the host's actual discovered guidance where supported; otherwise read the
   required files directly and report discovery as unobserved. Parent-directory
   placement alone does not prove a host loaded either instruction set.
9. **Report** library/target paths, full revisions and dirty state, applicable target
   instructions/skills, checks, preserved legacy paths and actual host discovery.
   Distinguish located files, returned contents, observed originating-request inclusion
   and actual execution enforcement; unavailable levels stay unknown. Never call a
   filesystem check a live host trial.

## What setup leaves alone, in detail

- Workspace setup writes no target files, instructions, skills, context maps, models
  or Git excludes. New project development changes follow their own authorized task.
  Reading a target's existing map is optional and read-only; use
  [offline resources](offline.md).
- Keep upstream files and host discovery paths unchanged. A target may add
  project-owned AGENT.md, AGENTS.md or local skills later; preserve and read those under
  the [instruction contract](repository-workspaces.md#project-owned-instructions-now-and-later).
  Follow [project ownership](project-skills.md) and [project context](project-context.md);
  no map or namespace is required.
- Existing external checkouts are migration inputs unless registered in place. Preserve
  them and their active processes/output until relocation is explicitly authorized and
  their owners release the path. See
  [retiring old installations](repository-workspaces.md#retiring-old-installations).
- Report the resolved harness for each target with the `harness.py` in the loaded
  `orbyn-workflow` skill's directory, by absolute path
  (`python3 <orbyn-workflow directory>/scripts/harness.py resolve --target <target>`;
  a missing script means stop and report). To tune one repository, the owner may add
  `.orbyn/harness.json` there (a target change needing normal authority) or a local
  `repo/.harness/<target-slug>.json`; setup writes neither. If the result prints
  `work mode: not set`, ask the user once for the mode (`simple`, `isolated` or
  `parallel`, suggesting `isolated`) and save it with
  `harness.py setup --target <target> --mode <mode>`, which writes only the ignored
  local or in-place owner file under `repo/.harness/`; unattended, use `isolated` and
  save nothing. See [harness settings](../../orbyn-workflow/references/harness-settings.md).
- Setup does not select a provider, model, effort, speed or subscription. Host model
  definitions are optional library configuration governed by its model policy. Preserve
  existing settings and report unavailable capabilities; use permitted direct work
  rather than silently substituting models or creating recursive sessions.
- Reading groups in [presets.json](../assets/presets.json) help choose relevant methods;
  they no longer control installation or hide source files.

## Resolve instruction conflicts

Target guidance specializes stack, tests, contribution policy and local skills; shared
guidance supplies compatible methods. User scope and host permissions remain higher
priority. Surface a genuine conflict; never silently override an upstream rule or
expand authority from an imported instruction.

If host boundaries prevent reading both roots, stop dependent execution and report the
limitation. Do not solve it by copying instructions or editing user-wide settings.

## Resume after a workspace or runtime change

After migration/resumption, a failed working directory or a runtime mismatch, reconcile
the actual selected checkout with the host's saved session binding where observable. An
explicit command workdir, a moved folder or project membership does not prove the
session was rebound. Use a supported authorized host mechanism if needed; otherwise
report the stale/unknown binding, use a valid explicit workdir for supported work and
retain that distinction. Do not recreate a deleted checkout or rewrite global session
settings/PATH to hide the problem.

Inspect the next required command's documented runner/version and resolve a compatible
already-installed runtime in the actual execution environment before an expensive run.
A harmless version/capability check is sufficient when available. Do not execute
untrusted setup hooks or install a runtime implicitly. If no runtime is supported,
report the dependent check blocked and continue independent work. Reuse current matching
readiness evidence. This is conditional recovery, not a full setup ritual on every
action or a reason to repeat passed suites.
