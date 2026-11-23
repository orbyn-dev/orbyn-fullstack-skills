# Audit checklist: failures and evidence

Use every applicable category for each assigned flow. Where a topic already has an
owner, apply it rather than this summary: [accessibility](../accessibility.md),
[design rules](../design-rules.md), [async states](../async-state.md),
[localization and RTL](../localization-rtl.md) and [stress fixtures](../stress-fixtures.md). Record the target's stated
contract, current design system or applicable standard before calling an observation
a defect. Use heuristics as questions, not universal requirements. Log a category
as blocked or not tested when its environment, data or permission is unavailable.

A read-only audit never changes product code, configuration or durable application
data. Submit forms, change network conditions or exercise recovery only with an
explicitly approved disposable sandbox/mock that prevents durable product-data
effects. Disposable test records alone do not authorize production writes.
Otherwise mark that check blocked. Do not
start external messages, purchases, irreversible actions or product fixes.

## Coverage categories

1. **platform** — Compare supported OS, browsers and window configurations. Look for
   incorrect shortcuts, blurry scaled text or missing native controls. Record the real
   OS, app/browser version, scale and input device; viewport or user-agent emulation
   alone does not prove native behavior. Unsupported platform combinations are not
   applicable, not failures.
2. **responsive** — Resize through breakpoints, between them and while content is
   open. Check clipping, overlaps, wrapping, hidden actions and text enlargement. Record
   viewport, zoom, screenshot and whether all content works. At 320 CSS pixels check
   reflow; a data table or map may need local two-dimensional scrolling without forcing
   unrelated content to scroll horizontally. Apply the product's supported range.
3. **interaction** — Exercise pointer, touch and keyboard for each control's default,
   hover, focus, pressed, selected, disabled and busy states. Check visible feedback,
   hit target and actual action; a decorative element is not a missing interaction.
   Capture the input sequence, state and observed result.
4. **navigation** — Follow links, menus, deep links and the browser or app back/forward
   path. Check broken targets, lost context and loops; count steps only against the task
   and supported permissions. Record origin, destination, state and safe reproduction.
   External destinations remain untested when access or authorization is absent.
5. **scroll** — Check page and nested scrolling with supported inputs, sticky headers,
   scroll restoration, pagination and focused controls. Record clipping, trapped scroll
   or a lost position with viewport and interaction sequence; intentional local
   scrolling is not automatically a defect.
6. **forms** — In an approved disposable sandbox/mock with product-data effects prevented, try required and optional fields,
   valid variants, boundary values, invalid formats and correction. Check missing or
   misleading errors, field association, focus and retained input. Record exact inputs
   using safe synthetic data and expected/observed outcomes. Native validation is not
   automatically a failure; apply the product contract and relevant criterion.
7. **recovery** — Simulate approved interruption, cancellation, retry or a delayed
   response. Check draft loss, stale/out-of-order results, duplicate pending actions,
   conflicting save status and inaccessible recovery. Capture the sequence and final
   state; use approved isolated mocks/sandboxes with product-data effects prevented for
   any operation that could write data.
8. **feedback** — Inspect loading, empty, no-results, partial, stale, offline and error
   states when applicable. Check whether a person can distinguish them and find a next
   action. Capture status, timing and task consequence; require product or user
   evidence before labeling a presentation confusing.
9. **visual** — Compare resolved colors, contrast, alignment, spacing and fonts with
   the governing design rules across relevant themes and states. Record rendered
   evidence and the token, variant or direct contradiction that governs it. A visual
   difference alone is not a defect; missing design documentation is not a finding.
10. **icons** — Check clipped or misleading icons, inconsistent meaning, missing labels
    and unreadable icon contrast where applicable. Capture the icon in its task context
    and accessible name. Treat unclear meaning as a usability hypothesis unless user
    evidence or the product's stated convention establishes the problem.
11. **overlays** — Open relevant menus, pop-ups, drawers and dialogs. Check obstruction,
    dismissal, initial focus, keyboard containment, focus return and background
    interaction for actual modal behavior. Keeping Tab inside an open modal is the
    ARIA APG dialog pattern, not a WCAG rule; WCAG SC 2.1.2 (No Keyboard Trap, A) is
    the separate requirement that focus can always leave a component. Capture keyboard steps and state. Initial
    focus depends on content; do not demand one fixed target for every dialog.
12. **accessibility** — Check keyboard reachability, visible focus, focus hidden by
    sticky content (SC 2.4.11, AA), names and roles, error identification (SC 3.3.1,
    A), text and non-text contrast (SC 1.4.3 and 1.4.11, AA), target size of at least
    24 CSS px (SC 2.5.8, AA) and reflow at 320 CSS px (SC 1.4.10, AA) as relevant.
    Reduced motion is WCAG only at AAA (SC 2.3.3, interaction-triggered animation);
    otherwise report it as a usability proposal or against the product's own rule. Cite each applicable WCAG criterion and level with
    its exceptions. Inspect the accessibility tree; mark screen-reader or other
    assistive-technology behavior untested unless actually exercised. Automated or
    source-only checks cannot establish overall WCAG conformance.
13. **locale** — For supported languages and formats, try long translations, mixed
    right-to-left and left-to-right text, input-method composition, dates, time zones
    and number formatting. Capture the locale, text direction, input and rendered
    result. Apply only declared supported locales; avoid assuming all scripts or
    regional conventions behave alike.
14. **data-and-speed** — Where relevant, inspect search, filters, lists, pagination,
    uploads, expired sessions and permission-denied states using safe fixtures. Check
    unreachable results, stale counters, unreadable failures and unexpected access
    changes against the product contract. Measure slow loads or interactions under
    stated device, network, cache and build conditions; repeat and report spread.
    Distinguish lab timings from field data and its sample/window/percentile. A single
    slow run is a lead, not proof of a general regression; web metrics are not a
    universal threshold for native apps.

For each checked case record environment, build/revision, state, viewport, input
method, observed result and evidence. Capture sanitized screenshots or short recordings
only when they add proof; redact credentials, personal data and sensitive content.
