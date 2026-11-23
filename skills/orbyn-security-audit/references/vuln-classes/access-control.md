# Access control

Use during an explicitly requested local source audit ([audit protocol](../protocol.md)). Each class lists where it lives in code, what proves it at `path:line`, the traps that make a candidate false, and the fix at the owning boundary ([remediation patterns](../../../orbyn-security-remediation/references/finding-patterns.md)).

## Object-level authorization (IDOR)

- **Where it lives:** handlers and resolvers that load a record by a request-supplied id: `Order.findById(req.params.id)`, GraphQL node lookups, downloads by key, batch endpoints taking id lists, and nested routes where only the parent is checked.
- **Proof:** the caller-supplied id reaches the data access at `path:line` with no predicate tying the row to the caller's principal or tenant, and no policy call on the way. Check the update and delete paths too, not only the read.
- **False-positive traps:** the query is scoped elsewhere (repository base class, default scope, row-level security, tenant middleware); the lookup runs inside the caller's own collection, so a foreign id simply misses; the object is public by design; no lower-trust principal can reach the route.
- **Fix:** put ownership in the data-access query or one policy check at the service boundary, for read, write and delete alike. Return the same not-found response for absent and forbidden objects.

## Authentication and authorization bypass

- **Where it lives:** route registration and middleware order, public-path allowlists matched by prefix or regex, guards declared per method or handler, and controllers missing an annotation their siblings carry.
- **Proof:** a privileged effect at `path:line` that the guard skips for some HTTP method, path spelling (trailing slash, case, encoded separators, matrix parameters) or registration order, shown from the router or middleware configuration. Alternatively, identity taken from a client-settable header with no trusted edge stripping it.
- **False-positive traps:** a global guard or gateway policy covers the route (cite it); the open route is deliberately public and has no privileged effect; the proxy canonicalises or rejects the odd path first (cite its config); deployment evidence you have not seen is unknown, not absent.
- **Fix:** deny by default at the router, attach guards to route groups rather than individual handlers, and canonicalise the path once before matching.

## Shadow and undocumented APIs

- **Where it lives:** old versioned routers still mounted, admin, debug or test controllers compiled into production, generated routes (ORM-backed REST, introspection, service reflection), framework management endpoints, and routes missing from the published API description.
- **Proof:** a route registered at `path:line` on a production build path whose handler has a weaker control than its current counterpart: no guard, no rate limit or looser validation, with the weaker control shown.
- **False-positive traps:** the old version is a thin alias that delegates to the current handler and its guards; the route exists only under a development profile or build flag, proven from build config; it returns a fixed deprecation response and runs nothing; the only difference is response shape.
- **Fix:** keep one route inventory checked in CI against the API description, remove or gate retired versions, and share one guard stack across versions.

## Mass assignment

- **Where it lives:** handlers binding a request body straight onto a model: `user.update(req.body)`, `Object.assign(entity, body)`, unrestricted strong-parameter calls, form or serialiser classes exposing every field, and one schema reused for input and storage.
- **Proof:** a server-owned field (role, admin flag, owner or tenant id, price, verified flag) is writable through the bound object at `path:line`, with no allowlist removing it before persistence.
- **False-positive traps:** the type actually bound at the boundary omits the field; server logic overwrites it after binding and before saving; the column is read-only at the ORM level; only the caller's own unprivileged fields are writable.
- **Fix:** a dedicated input type per operation with an explicit allowlist, and server-owned fields set from the principal, never from the body.

## Excessive data exposure

- **Where it lives:** responses that serialise whole entities (`res.json(user)`, default `toJSON`, GraphQL types mirroring every column), list endpoints, verbose error bodies, and clients that filter a full response for display.
- **Proof:** the response built at `path:line` includes a field the reaching caller is not entitled to (password hash, tokens, another user's contact details, internal flags), and you name that caller class.
- **False-positive traps:** a view model or serialiser drops the field, so check what is returned rather than the entity; the field is public by product decision; only administrators reach the route; the data is the caller's own.
- **Fix:** explicit response schemas per audience at the API boundary; never rely on the client to hide fields.

## SPA-backed APIs trusting the client

- **Where it lives:** single-page apps whose role checks, route guards and hidden controls live in the bundle (`if (user.role === "admin")`), backend-for-frontend layers that forward without checking, direct database clients governed by security rules, and privileged keys shipped to the browser.
- **Proof:** the client-only check at `path:line` and the server handler or database rule for the same operation at `path:line`, showing no matching server-side decision.
- **False-positive traps:** the server enforces the same rule (cite it); the bundled key is publishable by design and the store's row rules are on; requiring sign-in proves nothing either way, so read the handler; hidden UI over a protected API is fine.
- **Fix:** authorise every operation on the server or in database rules, and treat everything in the bundle as public.

## Source

Adapted from Claude-BugHunter by elementalsouls (https://github.com/elementalsouls/Claude-BugHunter at 9ba66b1b3a7d84c501202a527cdc22b2051c5711; code MIT, content CC BY 4.0), rewritten for read-only source review. Licence: [claude-bughunter-LICENSE.txt](claude-bughunter-LICENSE.txt).
