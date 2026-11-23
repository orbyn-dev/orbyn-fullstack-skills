# Platforms

Use during an explicitly requested local source audit ([audit protocol](../protocol.md)). Each class lists where it lives in code, what proves it at `path:line`, the traps that make a candidate false, and the fix at the owning boundary ([remediation patterns](../../../orbyn-security-remediation/references/finding-patterns.md)).

## GraphQL

- **Where it lives:** resolvers, field and type directives (`@auth`), schema-level middleware, DataLoader batching, and server setup for introspection, depth, complexity and batching limits.
- **Proof:** show a resolver for a sensitive field or mutation that loads by an argument id without checking the caller owns it, or that relies on a parent resolver's check that does not run on another path to the same type (a nested edge, a node lookup, a different query). For cost, show no depth or complexity limit and a resolver whose work grows with the query. For rate limits, show that a limit counted per HTTP request lets aliases or batched operations make many login, OTP or reset attempts in one request.
- **False-positive traps:**
  - Introspection on its own exposes structure, not data.
  - Batched aliases may run one after another; a unique constraint still stops a double effect.
  - Authorization sits in a shared data-access layer below the resolver; read it.
  - A mutation reachable by GET that the server rejects at execution.
- **Fix:** authorize in the data-access layer every resolver uses, and set depth, complexity and batch limits in server config.

## gRPC

- **Where it lives:** service implementations, interceptors, `.proto` files, reflection and health registration, grpc-gateway or JSON transcoding config, and proxies that set identity metadata.
- **Proof:** show a method with side effects or sensitive reads that no auth interceptor covers (interceptors often apply per server or by method-name match), or a backend that trusts identity metadata a client or gateway can set.
- **False-positive traps:**
  - Health and reflection are often public by design.
  - The service binds only to a private interface behind an authenticating proxy; cite both.
  - The method is anonymous by intent, such as a public catalog read.
- **Fix:** enforce authentication and per-method authorization in one interceptor with a deny-by-default method list, and strip identity metadata at the edge.

## WebSocket

- **Where it lives:** upgrade handlers, socket.io, SignalR or Phoenix channel join and room logic, and per-message handlers.
- **Proof:** show the handshake authenticating by cookie with no `Origin` check (so another site can open the socket as the victim), or message handlers that act without checking the caller's rights to the target room, record or action. Authentication at connect does not authorize each message.
- **False-positive traps:**
  - Auth by a token in the first message or a header a browser cannot send cross-site is not exposed to cross-site hijacking.
  - The handler re-checks authorization through a shared service.
  - A message that is accepted but has no server-side effect.
- **Fix:** check `Origin` against an allowlist at upgrade, and authorize every message and room join on the server.

## LLM and RAG features

- **Where it lives:** prompt assembly, tool and function definitions, agent loops, retrieval queries against vector stores, ingestion pipelines, and code that renders or executes model output.
- **Proof:** trace attacker-controlled text (a document, page, email, tool result) into the model context, then to a tool call or sink with an effect: a write, a fetch to a model-chosen URL, a Markdown image loading an external URL, unsanitized HTML, a shell or SQL string. For retrieval, show the query not filtered by tenant or document permissions. Use [tool credential boundaries](../threat-model/tool-credential-boundaries.md) for tool authority and the [RAG](../threat-model/architectures/rag.md) and [agentic AI](../threat-model/architectures/agentic-ai.md) checks.
- **False-positive traps:**
  - The model saying something it should not, with no tool, data or sink behind it.
  - A "leaked system prompt" in a test transcript is usually made up; check the prompt source.
  - Retrieval filtered by an ACL at query time in the store client.
  - Output rendered as plain text.
- **Fix:** enforce authorization in tools and retrieval, not in the prompt; treat model output as untrusted input at each sink.

## CI/CD

- **Where it lives:** workflow files (`.github/workflows`, `.gitlab-ci.yml`, Jenkinsfiles), reusable actions, self-hosted runner config, OIDC trust policies, cache and artifact steps.
- **Proof:** show fork or issue input reaching a privileged context: `pull_request_target` or `workflow_run` running PR code with secrets or a write token, or `${{ github.event.* }}` expanded into a `run:` script. Also: actions pinned by tag, not SHA, in jobs with secrets; broad `permissions`; secrets in logs or artifacts; OIDC trust accepting any branch.
- **False-positive traps:**
  - `pull_request_target` that checks out only the base ref and never builds, installs or runs PR code is safe; the trigger alone is not a finding.
  - `pull_request` from forks runs without secrets.
  - The expression is passed through `env:` and quoted in the shell.
  - Untrusted fields used only in an `if:` condition.
  - Masked, protected variables available only to protected branches.
- **Fix:** split untrusted build from privileged steps, pass event data via environment variables, pin actions by SHA, and set least `permissions`. See [CI/CD supply chain](../threat-model/architectures/cicd-supply-chain.md).

## Kubernetes and cloud config

- **Where it lives:** manifests, Helm charts, Terraform and CloudFormation, IAM and bucket policies, RBAC roles and bindings, network policies.
- **Proof:** cite the granting resource: a role with wildcard verbs or `secrets` access bound to a workload service account, privileged pods or a `hostPath` mount of the container socket, a public bucket principal, IAM wildcards on sensitive actions, or metadata reachable without IMDSv2 from a workload that fetches URLs.
- **False-positive traps:**
  - Read-only public buckets meant for public assets.
  - A sample or dev overlay not used in deployment; cite which overlay ships.
  - The token is not mounted (`automountServiceAccountToken: false`).
  - An organization-level policy denies what the local file allows.
- **Fix:** narrow the grant in the IaC that owns it, block public access at account level, require IMDSv2 and default-deny network policy.

## Framework pitfalls

Exposed debug endpoints, actuators and client-bundled environment variables belong to [source and secret leaks](server-fetch-and-files.md#source-and-secret-leaks).

- **Where it lives:** Next.js server actions, route handlers and `images.remotePatterns`; Node object merges, `child_process` and Express `trust proxy`; Spring SpEL; Laravel `$fillable` or `$guarded`; ASP.NET ViewState and machine keys.
- **Proof:** attacker input reaching the feature: a server action with no auth of its own (middleware-only checks miss other paths); request data merged so `__proto__` reaches shared state; a shell string built from input; `trust proxy` wide enough to spoof an IP check; SpEL parsed from input; `$guarded = []` on a model saved from the request; ViewState without MAC or with a committed machine key.
- **False-positive traps:**
  - The pattern sits in code no route reaches.
  - The framework version pinned in the lockfile already closes it; check the version, not the docs you remember.
  - A schema or DTO picks fields before the merge or save.
- **Fix:** close it in the framework's config or one shared helper: auth inside each action, a null-prototype or schema-validated merge, argument arrays instead of shell strings, an exact proxy count, explicit `$fillable`, and a secret machine key with MAC on.

## TLS and network config

- **Where it lives:** HTTP clients, TLS server config, reverse proxy and ingress settings, cookie flags, HSTS headers, mTLS and internal service settings.
- **Proof:** show a client that turns off certificate or hostname checks (`verify=False`, `rejectUnauthorized: false`, an accept-all trust manager) on a path carrying credentials or data, or a service that terminates mTLS and trusts a client-cert header the edge does not strip.
- **False-positive traps:**
  - A missing header or older cipher with no reachable attacker position is hardening, not a finding.
  - Disabled verification only in tests or a local tool.
  - TLS terminated by a managed load balancer configured elsewhere; mark it unknown.
- **Fix:** keep verification on in shared client setup, set HSTS and secure cookies at the edge, and strip identity headers from clients.

## Source

Adapted from Claude-BugHunter by elementalsouls (https://github.com/elementalsouls/Claude-BugHunter at 9ba66b1b3a7d84c501202a527cdc22b2051c5711; code MIT, content CC BY 4.0), rewritten for read-only source review. Licence: [claude-bughunter-LICENSE.txt](claude-bughunter-LICENSE.txt).
