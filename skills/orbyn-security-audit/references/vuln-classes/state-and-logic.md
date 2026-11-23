# State and logic

Use during an explicitly requested local source audit ([audit protocol](../protocol.md)). Each class lists where it lives in code, what proves it at `path:line`, the traps that make a candidate false, and the fix at the owning boundary ([remediation patterns](../../../orbyn-security-remediation/references/finding-patterns.md)).

## Race conditions

- **Where it lives:** one-time or limited actions: coupon and gift-card redemption, withdrawals and transfers, votes and likes, invitations, quota and seat limits, OTP and reset-token use, sign-up bonuses. Look for a read, a decision in application code, then a separate write; counters updated as `x = x + 1; save()`; eligibility checked in one service and fulfilled in another or in a background job.
- **Proof:** cite the check and the act at `path:line` and show that nothing makes them one unit: no transaction at a strong enough isolation level, no row lock (`SELECT ... FOR UPDATE`), no conditional update (`UPDATE ... WHERE used = false`), no unique constraint, no idempotency key. Name the window: two concurrent requests can both pass the check before either write commits.
- **False-positive traps:**
  - Two requests in a row is not a race; if a sequential repeat also succeeds, it is a logic bug.
  - A database unique constraint or conditional update rejects the second write even when the check is racy.
  - All writes for the key go through one queue or actor that processes them one at a time, across every instance. A single-threaded runtime is not enough: async code that awaits between check and write still interleaves, and so do several instances.
  - The duplicate effect is only in a response, and the ledger records one change.
- **Fix:** make the check and the write atomic in the store: a conditional update, a lock on the row, or a unique constraint, plus an idempotency key on the request.

## Business logic flaws

- **Where it lives:** pricing, cart and checkout code, discounts and coupon stacking, refunds, plan upgrades and downgrades, quantity and currency fields, multi-step flows (verify then act, approve then ship), and any request body that carries a total, price, status or role.
- **Proof:** show the server using a value it should derive itself (a client total, unit price, discount percent, `status: completed`), or a state transition allowed from a state that should forbid it. Follow it to the durable effect: the captured payment amount, the stored order, the granted entitlement.
- **False-positive traps:**
  - The field is accepted but ignored; the server recomputes before charging.
  - Payment capture or the provider rejects the amount, so a strange cart total never becomes money.
  - A cap applies later, such as a discount limited to the order value.
  - A rate-limit gap with no business outcome it was protecting is a control gap, not a logic flaw.
- **Fix:** recompute prices and totals on the server from trusted records, and keep allowed transitions in one state machine that every handler calls.

## Exceptional conditions

- **Where it lives:** `try` and `catch` blocks around authorization, signature or token checks; default branches in permission switches; middleware that calls `next()` on error; feature-flag or policy lookups with a permissive fallback; global error handlers and debug modes that print stack traces, SQL or internal paths.
- **Proof:** show an attacker-triggerable failure (malformed input, a missing field, an unexpected type, a dependency timeout) and the code path it takes: the check is skipped and the action proceeds (fail-open), or the response includes internals that matter.
- **False-positive traps:**
  - A generic error with no internals is correct handling.
  - A stack trace that appears only in a development profile the production config disables; cite that config.
  - The caught error is rethrown or turns into a denial further up.
  - A timeout that fails closed, even if it is unfriendly.
- **Fix:** make the security decision fail closed in the function that owns it, and send internal detail to logs, not responses.

## Payment and fintech flows

- **Where it lives:** payment-provider webhooks, ledger and balance code, transfer and payout APIs, currency conversion, refund and chargeback handling, and financial GraphQL mutations that take an account or amount argument.
- **Proof:** show one of these at `path:line`: a webhook handled without verifying its signature, or trusting payload fields over a provider lookup; a balance changed without a double-entry or a ledger row in the same transaction; an account id from the request not tied to the caller; rounding or currency mixing that lets small amounts accumulate; replay of the same event or request applying twice.
- **False-positive traps:**
  - The handler verifies the signature in middleware you have not read yet; follow the route registration.
  - The event id is stored with a unique constraint, so a replay is a no-op.
  - The amount comes from the server's own order record, not the payload.
  - Sandbox-only code paths excluded from production builds.
- **Fix:** verify signatures and fetch authoritative state from the provider, apply each event idempotently, and write balance changes and ledger entries in one transaction.

## Source

Adapted from Claude-BugHunter by elementalsouls (https://github.com/elementalsouls/Claude-BugHunter at 9ba66b1b3a7d84c501202a527cdc22b2051c5711; code MIT, content CC BY 4.0), rewritten for read-only source review. Licence: [claude-bughunter-LICENSE.txt](claude-bughunter-LICENSE.txt).
