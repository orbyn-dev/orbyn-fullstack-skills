# Request trust

Use during an explicitly requested local source audit ([audit protocol](../protocol.md)). Each class lists where it lives in code, what proves it at `path:line`, the traps that make a candidate false, and the fix at the owning boundary ([remediation patterns](../../../orbyn-security-remediation/references/finding-patterns.md)).

## Cross-site request forgery

- **Where it lives:** state-changing routes authenticated by ambient credentials (cookies, HTTP authentication, client certificates), CSRF middleware and its exemption list, GET handlers with side effects, and body parsers.
- **Proof:** a cookie-authenticated state change at `path:line` with no token, `Origin` or `Sec-Fetch-Site` check that a cross-site page can trigger: a GET effect (top-level navigation sends `Lax` cookies), cookies set `SameSite=None`, or a body a plain form can send (simple requests skip preflight, so a JSON handler that also accepts form or `text/plain` bodies counts). `SameSite` is per site, so sibling subdomains are same-site.
- **False-positive traps:** authentication uses a header set by script; global middleware covers the route (check the exemptions); the handler rejects non-JSON content types; the token is checked against a per-session value (a constant token is not one).
- **Fix:** framework CSRF tokens or origin checks on every unsafe method, no side effects on GET, and `SameSite=Lax` or stricter as a second layer.

## CORS misconfiguration

- **Where it lives:** CORS middleware configuration, hand-written `Access-Control-Allow-*` headers and origin allowlist matching code.
- **Proof:** at `path:line`, the response reflects the request `Origin`, or matches it by suffix, prefix, unanchored regex or the `null` origin, together with `Access-Control-Allow-Credentials: true` on a route returning caller-specific data.
- **False-positive traps:** a literal `*` cannot be combined with credentials, so browsers refuse it; `*` without credentials exposes only what an anonymous request already gets; reflection without credentials over bearer-token auth leaks nothing new; an exact allowlist. A trusted-subdomain list is a finding only with a named precondition.
- **Fix:** an exact origin allowlist, credentials only for those origins, and `Vary: Origin` on dynamic responses.

## Open redirect

- **Where it lives:** `next` and `returnTo` parameters on sign-in and sign-out, OAuth callbacks, link trackers, and calls like `res.redirect(req.query.url)` or client-side `location` assignment.
- **Proof:** a request-controlled value reaches a redirect or navigation at `path:line` with no allowlist, or with a check that parses differently from the browser: `startsWith("/")` accepting protocol-relative or backslash forms, or a host test by substring.
- **False-positive traps:** the value is parsed and its origin compared to a fixed list; only a path is accepted and joined to a fixed origin; the parameter is signed by the server; a chain to token leakage is claimed but not shown.
- **Fix:** map to named destinations, or parse and compare the origin against an exact allowlist.

## Host header trust

- **Where it lives:** absolute URL building for reset links, emails, canonical tags and redirects, `request.host` and forwarded-host readers, host allowlist settings, and virtual-host routing.
- **Proof:** the request `Host` or a forwarded-host header flows into a generated link, cache key or routing decision at `path:line`, and neither the application nor the proxy pins it.
- **False-positive traps:** the proxy sets `Host` from its configuration and drops client-sent forwarding headers (cite it); a framework host allowlist is set; the link base comes from configuration.
- **Fix:** take the base URL from configuration, enforce a host allowlist, and trust forwarding headers only from known proxies.

## Clickjacking and frame policy

- **Where it lives:** security-header middleware (`X-Frame-Options`, CSP `frame-ancestors`), per-route overrides and embed routes.
- **Proof:** an authenticated HTML page with a sensitive action reachable in one or a few clicks at `path:line`, served without a frame policy, whose session cookie is still sent in a third-party frame.
- **False-positive traps:** the page has no sensitive action; the response is JSON or another non-HTML type; `Lax` or `Strict` cookies mean a framed page is signed out; the route is a deliberate embed with no privileged actions; a CDN sets the header (cite it).
- **Fix:** `frame-ancestors 'none'`, or an explicit list, set globally with named per-route exceptions.

## Web cache poisoning

- **Where it lives:** CDN and reverse-proxy cache rules and keys, `Vary`, application `Cache-Control`, and handlers reflecting inputs the cache does not key on (forwarded headers, excluded query parameters, cookies).
- **Proof:** an unkeyed input at `path:line` changes a response that the cache configuration stores for shared reuse, and the change is harmful: a script or asset host, a redirect target, or an error page.
- **False-positive traps:** the response is `private` or `no-store`; the input is part of the key or listed in `Vary`; the reflection is inert; the cache stores per user.
- **Fix:** key on every input that changes the output, or stop reading unkeyed inputs; cache only responses marked public.

## Web cache deception

- **Where it lives:** cache rules by file extension or path prefix, and application routing that ignores path suffixes, delimiters or encoding the cache treats differently.
- **Proof:** a route returning per-user data at `path:line` also answers a path the cache classifies as static (an extra segment or delimiter the router tolerates), and the cache rule overrides the origin's caching headers.
- **False-positive traps:** the cache honours `private` and `no-store` from the origin; the router rejects extra suffixes; the cache key includes the session credential.
- **Fix:** strict routing, `Cache-Control: no-store` on authenticated responses, and cache rules that respect origin headers.

## HTTP request smuggling

- **Where it lives:** front-end and back-end pairs (CDN, load balancer, ingress, application server) with their versions and configuration, HTTP/2 to HTTP/1.1 downgrade, connection reuse settings, and hand-written HTTP parsers.
- **Proof:** evidence that two hops disagree on where a message ends: the front end forwards requests carrying both `Content-Length` and `Transfer-Encoding`, or a malformed encoding; a downgrade path that does not reject header line breaks or conflicting lengths; a back-end parser version that accepts what the front end passes; and back-end connection reuse enabled. Static evidence rarely confirms this, so expect `needs_validation`.
- **False-positive traps:** the front end rejects ambiguous framing in the pinned version; HTTP/2 runs end to end or back-end connections are not reused; there is only one server; unproven versions are unknown, not vulnerable.
- **Fix:** normalise and reject ambiguous requests at the edge, use HTTP/2 end to end or disable back-end reuse, and patch both parsers.

## Source

Adapted from Claude-BugHunter by elementalsouls (https://github.com/elementalsouls/Claude-BugHunter at 9ba66b1b3a7d84c501202a527cdc22b2051c5711; code MIT, content CC BY 4.0), rewritten for read-only source review. Licence: [claude-bughunter-LICENSE.txt](claude-bughunter-LICENSE.txt).
