# Client side

Use during an explicitly requested local source audit ([audit protocol](../protocol.md)). Each class lists where it lives in code, what proves it at `path:line`, the traps that make a candidate false, and the fix at the owning boundary ([remediation patterns](../../../orbyn-security-remediation/references/finding-patterns.md)).

## Reflected and stored XSS

- **Where it lives:** server templates that print request or database values (Jinja, Razor, ERB, Blade, JSP), string-built HTML in handlers, error and search pages, email and PDF templates rendered in a browser, admin views of user content, and responses that set `text/html` for data that should be JSON.
- **Proof:** trace an attacker-writable value (query parameter, profile field, comment, filename, imported record) to an HTML output point and show the escaping turned off or wrong for that context: raw filters such as `|safe`, `Html.Raw` or `{!! !!}`, values placed in an attribute without quotes, inside a `<script>` block, or in an `href`. For stored XSS, name who views the value and on which origin.
- **False-positive traps:**
  - The engine escapes by default and the value is printed in plain text context.
  - The response is `application/json` or `text/plain` with `nosniff`, so the browser never renders it.
  - Input blocking (a request validator or WAF) is not output encoding, but neither is it a sink; find a path that reaches output unescaped.
  - The value passes through a maintained sanitizer with a strict allowlist before rendering.
- **Fix:** keep auto-escaping on and escape for the exact output context in the template layer; sanitize only where rich HTML is a product need. A strict CSP limits damage but does not replace encoding.

## DOM XSS

- **Where it lives:** client code reading `location`, `document.referrer`, `window.name`, storage, `postMessage` data or API responses, and writing them to `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, `setTimeout` with a string, jQuery `.html()` or `$(...)` with markup, or a navigation sink such as `location.href` or `src`.
- **Proof:** show the source, every transformation, and the sink at `path:line`, all in shipped code. For a URL sink, show the scheme is not restricted, so a `javascript:` URL reaches it. For DOM clobbering, show the clobbered global actually reaches a sink.
- **False-positive traps:**
  - The value goes through `textContent`, `setAttribute` on a non-URL attribute, or a framework binding that escapes.
  - The source is not attacker-controlled, such as a constant or a server value the user cannot set.
  - Trusted Types or a sanitizer runs before the sink.
  - The code is dead or only in a development build.
- **Fix:** replace the sink with a text API or framework binding; when markup is required, sanitize at the sink and allowlist URL schemes.

## HTML injection

- **Where it lives:** the same output points as XSS where scripts are blocked or stripped but markup survives: notification emails, rendered Markdown, invitation and share pages, and sanitizers configured to allow forms, links, `<meta>` or `<base>`.
- **Proof:** show attacker markup rendered on a trusted page or email and what it achieves without script: a fake login form posting elsewhere, a link that looks like the product, `<base>` or `<meta refresh>` changing where relative URLs or the page go, or dangling markup capturing later page content.
- **False-positive traps:**
  - Only harmless formatting tags survive and links are rewritten or marked external.
  - The content is shown in a sandboxed iframe or a plain-text email part.
  - The user can only inject into their own view.
- **Fix:** escape by default; if rich content is a feature, use a sanitizer allowlist that drops forms, `base`, `meta` and unsafe URL schemes.

## Unsafe rendering in frameworks

- **Where it lives:** escape hatches: React `dangerouslySetInnerHTML`, Vue `v-html`, Angular `bypassSecurityTrustHtml`, `bypassSecurityTrustUrl` and related `bypassSecurityTrust*` calls, Svelte `{@html}`, Markdown renderers with HTML enabled, and `href` or `src` bound to user data (React and Vue do not block every `javascript:` URL).
- **Proof:** show attacker-controlled content reaching the escape hatch at `path:line` with no sanitizer on that path. For client-side template injection, show user input compiled as a template (Angular or Vue rendering server HTML that contains template syntax).
- **False-positive traps:**
  - The bound value is a constant, translation string or trusted CMS field only staff can edit; state who can write it.
  - A sanitizer such as DOMPurify runs immediately before the hatch with a sound config.
  - The Markdown renderer has HTML disabled and links filtered.
  - A search hit in a test, story or demo file that never ships.
- **Fix:** remove the escape hatch or move sanitization into one shared rendering component, so every call site passes through it.

## postMessage and client-side trust

- **Where it lives:** `message` event listeners, OAuth and payment popups, embedded widgets, browser extensions, iframes talking to a parent, and any client code that grants authority from data the client holds: hidden fields, role flags in storage, feature checks done only in the browser.
- **Proof:** show a listener that acts on `event.data` without checking `event.origin` against an exact value, or with a weak check (`indexOf`, `endsWith` without a dot, a regex without anchors), and what it does: writes HTML, navigates, stores a token, calls an API. For client trust, show the server endpoint accepting the action without its own check.
- **False-positive traps:**
  - The listener only accepts a fixed message type with no effect beyond display.
  - The origin is compared exactly to a configured constant.
  - The server re-checks the authority the client claims; a hidden button is not a bypass.
  - `postMessage` sends with a `*` target but the data is not sensitive.
- **Fix:** check the exact origin and message schema in the listener, send with an explicit target origin, and enforce every authority decision on the server.

## Source

Adapted from Claude-BugHunter by elementalsouls (https://github.com/elementalsouls/Claude-BugHunter at 9ba66b1b3a7d84c501202a527cdc22b2051c5711; code MIT, content CC BY 4.0), rewritten for read-only source review. Licence: [claude-bughunter-LICENSE.txt](claude-bughunter-LICENSE.txt).
