# Vulnerability classes in source

Use during an explicitly requested local source audit. Each file lists, per class,
where it lives in code, what proves it at `path:line`, the false positives to rule out
and the fix at the owning boundary.

| File | Classes |
| --- | --- |
| [access control](vuln-classes/access-control.md) | Object-level authorization, auth bypass, shadow APIs, mass assignment, excessive data exposure, client-held authority |
| [accounts and sessions](vuln-classes/accounts-sessions.md) | Account takeover, password reset, MFA, sessions, JWT, OAuth, SAML, brute force and bot controls |
| [request trust](vuln-classes/request-trust.md) | CSRF, CORS, open redirect, host header, framing, cache poisoning and deception, request smuggling |
| [injection](vuln-classes/injection.md) | SQL, NoSQL, LDAP, template injection, XXE, deserialization, command execution |
| [server fetch and files](vuln-classes/server-fetch-and-files.md) | SSRF, file upload, path traversal, source and secret leaks |
| [client side](vuln-classes/client-side.md) | XSS, DOM XSS, HTML injection, unsafe framework rendering, postMessage |
| [state and logic](vuln-classes/state-and-logic.md) | Race conditions, business logic, fail-open errors, payment flows |
| [platforms](vuln-classes/platforms.md) | GraphQL, gRPC, WebSocket, LLM and RAG, CI/CD, Kubernetes and cloud, framework pitfalls, TLS |

Adapted from Claude-BugHunter (MIT code, CC BY 4.0 content); see each file's source note.
