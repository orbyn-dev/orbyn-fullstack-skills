# Findings, coverage and handoff

The coordinator owns one audit package for the selected revision. Keep it in the
project's documented artifact location or one the user approves, outside tracked
source and never in a commit.
Agents return separate drafts and sanitized evidence; only the coordinator combines
and writes the canonical records. Do not include secrets, credentials, personal data,
raw request/response bodies, private logs or unredacted captures.

## Package

- `audit.md`: target and build/revision, scope, environment matrix, method, limitations,
  severity summary, priority recommendations, full verified finding index and coverage
  summary. Link evidence by relative path. Mark assumptions and missing access.
- `findings.json`: complete machine-readable inventory, including findings needing
  verification and dispositions for duplicates or rejected candidates. Preserve all
  verified findings; a summary may highlight the highest priorities.
- `coverage.csv`: one row for each planned flow/category/environment/state/input
  combination, including outcomes and reasons.
- `evidence/`: sanitized screenshots, recordings or other minimal proof under the audit
  package; never include raw network bodies. Record unavailable evidence explicitly.
- `fix-queue.md`: a prioritized backlog linked to finding IDs and verification criteria.
  It proposes later work; it is not implementation scope, an issue or a PR.

Use relative evidence paths that stay within this package. Avoid names that encode
credentials or identifying data. Each artifact records the target revision and time;
record the actual time zone where a local timestamp is used.

## Stable finding records

The coordinator verifies candidates and allocates IDs (`UX-001`, `UX-002`, ...).
Keep an ID across reruns when the same root cause and affected experience are verified;
use a root-cause fingerprint to match, but do not merge solely on a guessed fingerprint.
Preserve prior IDs and dispositions, link newly confirmed duplicates to the canonical
finding, and allocate a new ID for a distinct cause. If the cause is unknown, record an
observation fingerprint and `needs_verification`; do not invent a source location.

Each record includes:

- `id`, `fingerprint`, `classification` (`confirmed_defect`, `usability_proposal` or
  `needs_verification`), and `status` (`open`, `resolved`, `deferred`, `rejected` or
  `duplicate`, with canonical ID where relevant).
- `category`, affected flow/screen and state, build or revision, environment (actual OS,
  browser/app version, viewport/scale, theme, locale and input method) and observation
  mode (`native`, `emulated` or `static`). Record unknown fields as unknown.
- Expected behavior and its basis: product contract, applicable standard and criterion,
  documented design convention or clearly labeled heuristic. Explain impact, severity
  and confidence separately; a high-severity unverified concern is not a confirmed bug.
- Safe reproduction steps, observed result, relative sanitized evidence paths, suspected
  owner if proven, a proposed correction and an observable acceptance check. Cite the
  criterion level and applicable exception where claiming a WCAG failure.

A compact synthetic example, with no real account or product data:

```json
{
  "id": "UX-001",
  "fingerprint": "settings/save/duplicate-submit/confirmed-button-handler",
  "classification": "confirmed_defect",
  "status": "open",
  "category": "interaction",
  "flow": "settings save",
  "state": "pending",
  "revision": "synthetic-revision",
  "environment": {"os": "Windows", "browser": "example", "viewport": "1280x800", "input": "keyboard", "mode": "native"},
  "expected": "One save is issued while pending, as specified by the product contract.",
  "observed": "Pressing Enter twice starts two saves in the disposable fixture.",
  "severity": "major",
  "confidence": "high",
  "reproduction": ["Open the disposable settings fixture", "Press Enter twice while save is pending"],
  "evidence": ["evidence/ux-001-pending.png", "evidence/ux-001-synthetic-request-count.json"],
  "owner": "settings submit handler, confirmed in the fixture",
  "proposal": "Prevent a second submit while the first is pending.",
  "acceptance": "One save is issued for repeated Enter while pending."
}
```

## Severity and confidence

Use the product's own severity policy when it has one. Otherwise, severity describes
observed impact: critical blocks a critical journey or risks
loss of work; major materially prevents or misleads a core task with a limited
workaround; minor adds recoverable friction; cosmetic concerns presentation
without demonstrated task impact. Confidence describes evidence: high means repeatable observation
and a binding expected behavior, medium means a reproduced but narrowly qualified
claim, low means a hypothesis needing verification. High potential impact does
not turn a low-confidence hypothesis into a confirmed defect.

## Coverage and decisions

Use `flow, category, environment, state, input, outcome, reason, evidence` columns.
Outcome is `pass`, `finding`, `blocked`, `not_tested` or `not_applicable`. A pass
means the specific combination was actually checked and its expected behavior met;
`blocked`, `not_tested` and `not_applicable` require a reason. Mark incomplete
inspection explicitly rather than reporting a pass. Keep native, emulated and
static inspection distinct and state which OS/browser/device combinations remain
unverified. Never infer a clean application or complete WCAG conformance from this
bounded matrix.

Order fixes by user impact, reach, severity, confidence, dependencies and effort;
separate the severity of impact from confidence in the evidence. Show all verified
findings in the backlog, including low-priority ones; identify blocked cases and
which evidence or decision would unblock them. Deduplicate confirmed common causes
without erasing distinct impacts or coverage rows.

The audit ends at the read-only evidence boundary. Do not modify product code,
configuration or durable data, create issues or PRs, message others, or apply fixes
as an audit side effect. Later implementation uses separately authorized scope and
maps selected finding IDs to verification. When reproduction needs a mutation, use an
approved disposable sandbox/mock that prevents effects on product data; otherwise record the case as blocked.
