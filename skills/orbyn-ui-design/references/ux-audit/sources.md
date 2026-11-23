# Research basis and applicability

Reviewed 2026-11-22. This is an original synthesis of primary guidance, not a
copied checklist, a prevalence study or evidence of defects in a specific product.
Use offline descriptions below when links are unavailable; current online facts
then remain unverified. Record the source/criterion and product applicability for
each finding rather than automatically applying every recommendation.

| Primary source | Scope | Audit implication |
| --- | --- | --- |
| [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/) | Accessibility criteria | Criterion-specific checks for keyboard access, focus, reflow, target sizes, errors, redundant entry and authentication. Check level, applicability and exceptions; partial automation is not conformance. |
| [W3C ARIA APG modal dialogs](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) | Widget pattern guidance | Probe focus entry/containment/return, background interaction and accessible naming. APG patterns inform implementation; distinguish them from normative WCAG requirements. |
| [W3C form validation](https://www.w3.org/WAI/tutorials/forms/validation/) | Form behavior | Probe required/invalid/boundary input, supported formats and corrective feedback. Client validation does not establish server validation or security. |
| [W3C error identification](https://www.w3.org/WAI/WCAG22/Understanding/error-identification) | Accessible error evidence | Identify what failed and the affected input with useful text. Do not declare native validation invalid merely because a custom inline message is preferred. |
| [W3C reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow) | Responsive accessibility | Inspect lost content/functions and page-level two-dimensional scrolling. A necessary two-dimensional table/map exception does not extend to neighboring controls. |
| [W3C internationalization](https://www.w3.org/International/questions/qa-html-dir.en) | Supported locale coverage | Probe mixed-direction text, punctuation and direction semantics for supported languages; language metadata alone does not determine text direction. |
| [Nielsen Norman Group usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) | Expert usability inspection | Investigate unclear state, inconsistent meanings, poor escape/recovery and excessive memory demands. Heuristics generate contextual hypotheses, not automatic defect verdicts. |
| [Nielsen Norman Group error messages](https://www.nngroup.com/articles/error-message-guidelines/) | Recovery and content | Inspect whether errors are noticeable, understandable, actionable and preserve effort. Confirm product impact rather than imposing one wording or layout. |
| [GOV.UK error summary](https://design-system.service.gov.uk/components/error-summary/) | Conditional design-system pattern | In a product using this pattern, probe summary/field agreement, links to affected controls and focus management. Do not require GOV.UK components or exact headings elsewhere. |
| [GOV.UK back link](https://design-system.service.gov.uk/components/back-link/) | Multi-step journey recovery | Probe returning to the prior state without losing entered work. Component placement and specific navigation rules apply to its design system, not universally. |
| [IBM Carbon empty states](https://www.carbondesignsystem.com/building-blocks/core/patterns/empty-states) | Conditional state patterns | Distinguish first use, no search results and unavailable/error content; check that context and a relevant next step are available. Avoid demanding decorative onboarding everywhere. |
| [Microsoft Windows app guidance](https://learn.microsoft.com/en-us/windows/apps/get-started/best-practices) | Actual Windows environments | Inspect resizing/Snap, keyboard and pointer input, and applicable framework/DPI behavior on real target configurations. |
| [Apple layout guidance](https://developer.apple.com/design/human-interface-guidelines/layout) | Actual Apple environments | Inspect text growth, available window size, safe areas and supported multitasking. Treat platform conventions in the context of the target app. |
| [Android window size classes](https://developer.android.com/develop/adaptive-apps/guides/use-window-size-classes) | Actual Android environments | Probe available-window changes, split screen and folding where supported rather than classifying layout solely by device name. |
| [Playwright emulation](https://playwright.dev/docs/emulation) | Execution evidence boundary | Viewport, input, locale and browser settings are useful emulations; they do not prove another OS, physical keyboard or hardware behavior. |
| [web.dev Core Web Vitals](https://web.dev/articles/defining-core-web-vitals-thresholds) | Web performance guidance | Measure loading, interaction responsiveness and layout stability with conditions and distributions. Field-percentile thresholds are not a verdict from one lab run or a native-app SLA. |

## Research-derived additions

Alongside color, alignment, typography, iconography, navigation, responsiveness,
scrolling and hit areas, inspect recovery and retained work; loading/empty/partial/
error states; stale or out-of-order feedback; supported localization and input;
authentication/permission transitions; and measured responsiveness. Add conditional
probes for uploads, search/list state and offline behavior only where those features
exist. Fault examples are test hypotheses until reproduced safely.

Use three evidence bases: product behavior/contracts, applicable standards and
contextual conventions/heuristics. A heuristic concern remains a usability proposal
unless evidence establishes the defect. Record automated/static, emulated, native
and observed-user evidence separately. Never claim all bugs found or complete
accessibility compliance from this checklist. Actual user studies are separately
scoped; expert inspection cannot invent user frustration or task success rates.

## Attribution and redistribution

No source code, screenshots, diagrams or checklist prose from these pages is
bundled. Page licensing has not been qualified for redistribution; treat it as
review-only. The local method and synthetic examples are independently written.
Future copied/adapted material needs a reviewed revision/licence, permission where
needed, notices and provenance under the library's authoring policy.
