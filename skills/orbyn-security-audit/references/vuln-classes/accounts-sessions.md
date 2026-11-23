# Accounts and sessions

Use during an explicitly requested local source audit ([audit protocol](../protocol.md)). Each class lists where it lives in code, what proves it at `path:line`, the traps that make a candidate false, and the fix at the owning boundary ([remediation patterns](../../../orbyn-security-remediation/references/finding-patterns.md)).

## Account takeover paths

- **Where it lives:** email and phone change, social sign-in that merges accounts by email, invitation acceptance, sign-up that can claim an address already on file, and support impersonation.
- **Proof:** a path at `path:line` that changes an account's identity or credential without proof from its current owner: an email change with no reauthentication or confirmation to the old address, an external login linked by an unverified email claim, or an unverified sign-up later merged into an existing account.
- **False-positive traps:** the change requires recent reauthentication (cite it); the provider's email claim is accepted only when marked verified by a trusted issuer; the existing account must approve the link; only the caller's own unverified account is affected.
- **Fix:** bind identity changes to reauthentication and confirmation through the existing channel; link external identities by issuer and subject, never email.

## Password reset

- **Where it lives:** reset token generation, storage, lookup, expiry and consumption, the code that builds the reset link, and the handler that sets the new password.
- **Proof:** at `path:line`, a token from a non-cryptographic source (`Math.random`, a timestamp), stored in plain form, without expiry or single use, not bound to the account (password applied to a user id from the request), a link host taken from the request, or the token in a response.
- **False-positive traps:** the generator is a CSPRNG of adequate length (check the call, not the variable name); the token is hashed and found by lookup; a generic "if the account exists" message is not enumeration; the link base comes from configuration.
- **Fix:** a CSPRNG token, hashed at rest, short-lived, single-use and bound to one account, a configured link base, and session revocation on reset.

## MFA bypass

- **Where it lives:** the state between the first and second factor, guards that test "signed in" rather than "fully authenticated", the OTP verify handler, recovery codes, remember-device cookies, and MFA enrol and disable endpoints.
- **Proof:** a session issued after the password step reaches protected routes at `path:line` because the guard ignores the pending-MFA state; or OTP verification discards its result, accepts reused codes, has no attempt bound, or is skipped by a client-sent flag; or MFA can be disabled without the second factor.
- **False-positive traps:** the token you traced was only issued after a completed MFA step; an attempt limit exists in a shared limiter keyed per account; a one-step TOTP clock tolerance is standard.
- **Fix:** one authentication state machine, guards on the fully-authenticated state, per-account attempt limits, single-use codes and reauthentication for MFA changes.

## Session handling

- **Where it lives:** session id generation, login and privilege-change handlers, logout, password change, cookie attributes, refresh-token rotation and the server-side session store.
- **Proof:** at `path:line`, a session id kept across login or privilege change (fixation), logout or password change leaving server-side sessions or refresh tokens valid, refresh-token reuse going undetected, ids from a weak source, or tokens placed in URLs or logs.
- **False-positive traps:** the framework regenerates the id on login in the configured version; short-lived stateless tokens without revocation are a design choice, a finding only against a stated invariant; a missing `HttpOnly` or `SameSite` attribute alone is a hardening note until paired with a reachable primitive.
- **Fix:** regenerate on every authentication change, revoke server-side on logout and reset, rotate refresh tokens with reuse detection, and set cookie defaults in one place.

## JWT and token cryptography

- **Where it lives:** verification code: `decode` versus `verify` calls, algorithm options, key selection by `kid`, `jku` or `x5u`, claim checks and signing secrets in configuration.
- **Proof:** at `path:line`, a token accepted without signature verification; the algorithm read from the token header instead of pinned, admitting `none` or key confusion (a public key used as an HMAC secret); `kid` used in a file path or query; keys fetched from a token-supplied URL; `exp`, `aud` or `iss` unchecked; a weak or committed secret.
- **False-positive traps:** the library pins the algorithm from the configured key type in the version in use; `decode` only routes before a later `verify` on the same path; `kid` maps through a fixed key set; the secret lives only in a test fixture.
- **Fix:** verify with an explicit algorithm allowlist and fixed keys per issuer, and check `exp`, `nbf`, `aud` and `iss`.

## OAuth and OpenID Connect

- **Where it lives:** the client callback, state and nonce handling, redirect URI registration and matching, code exchange, PKCE and ID token validation.
- **Proof:** at `path:line`, a callback that never compares `state` with a value bound to the session; redirect URIs matched by prefix, substring or loose regex; public clients exchanging codes without PKCE; ID tokens accepted without signature, `aud`, `iss` or `nonce` checks; users identified by email rather than issuer and subject; tokens in URLs that reach logs or `Referer`.
- **False-positive traps:** the library checks `state` and `nonce` (cite the config); the registry uses exact matching; a URI variation that still lands on the registered origin; a confidential server-side client where missing PKCE is defence in depth.
- **Fix:** library-managed state, nonce and PKCE, exact redirect matching, ID tokens validated per OIDC, identity keyed by issuer and subject.

## SAML

- **Where it lives:** the assertion consumer service, the signature verification call, the element the application reads afterwards, canonicalisation, audience, recipient and time checks, and the replay cache.
- **Proof:** at `path:line`, the signature verified over one element while identity is read from another (signature wrapping), a verification result ignored or replaced by a presence check, unsigned assertions accepted, audience, recipient or expiry unchecked, no assertion-id replay cache, or an XML parser with external entities enabled.
- **False-positive traps:** the library returns the verified assertion and the application reads only from that object; canonicalisation treats comments identically for signer and reader in the pinned version; library defaults enforce audience and time (cite them).
- **Fix:** read identity only from the library's verified output, require signed assertions, and validate audience, recipient, time window and replay.

## Brute force, lockout and bot controls

- **Where it lives:** login, OTP, reset-token, sign-up and voucher endpoints, rate-limit middleware and its keys, client-IP resolution and CAPTCHA verification calls.
- **Proof:** a guessable-secret endpoint at `path:line` with no per-account attempt bound; a limiter keyed only on a client-settable value such as an untrusted forwarded-for header; a limit on one route but not its alias or older version; a CAPTCHA token never verified server-side, its result ignored, or not bound to the action.
- **False-positive traps:** a gateway enforces the limit (cite its config, or mark unknown); keyspace and lifetime make guessing infeasible, so show the arithmetic; identical responses mean no enumeration; CAPTCHA guards only a low-value action.
- **Fix:** per-account and per-source limits in the server, a trusted proxy list for client IPs, and server-side CAPTCHA verification bound to the action.

## Source

Adapted from Claude-BugHunter by elementalsouls (https://github.com/elementalsouls/Claude-BugHunter at 9ba66b1b3a7d84c501202a527cdc22b2051c5711; code MIT, content CC BY 4.0), rewritten for read-only source review. Licence: [claude-bughunter-LICENSE.txt](claude-bughunter-LICENSE.txt).
