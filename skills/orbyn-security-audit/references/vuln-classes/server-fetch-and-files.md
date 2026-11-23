# Server fetch and files

Use during an explicitly requested local source audit ([audit protocol](../protocol.md)). Each class lists where it lives in code, what proves it at `path:line`, the traps that make a candidate false, and the fix at the owning boundary ([remediation patterns](../../../orbyn-security-remediation/references/finding-patterns.md)).

## Server-side request forgery (SSRF)

- **Where it lives:** any server code that builds an outbound request from caller input: URL previews, webhook registration and delivery, import-from-URL, avatar or feed fetchers, PDF and HTML renderers (headless browsers, wkhtmltopdf), image proxies such as `/_next/image`, XML parsers that load external entities, and RPC arguments that carry a host or URL.
- **Proof:** trace the caller-controlled URL, host or path fragment to the HTTP client, socket or renderer call, and show what validation sits between them. An allowlist only counts if it checks the address the client actually connects to. Name the internal target the server could reach: cloud metadata, loopback admin ports, cluster services.
- **False-positive traps:**
  - The URL is only parsed, logged or echoed in an error, and no network call follows on that path.
  - The fetch runs in the browser (`fetch` in client code), so it never leaves the server's network.
  - The client is pinned to a fixed host and the input only picks a path on a public API.
  - Egress is denied by configuration you can cite, such as a proxy with an allowlist or a network policy.
- **Fix:** in one outbound-fetch helper, resolve the name, reject private, loopback, link-local and metadata ranges, then connect to that checked IP. Turn off automatic redirects or re-check every hop; a check before a second DNS lookup loses to rebinding.

## File upload

- **Where it lives:** multipart handlers (multer, `IFormFile`, Django `FileField`, Active Storage), presigned-upload issuers, image and document processors (ImageMagick, libvips, office converters), archive extraction, and whatever serves stored files back.
- **Proof:** show the stored file reaching a consumer that treats it as more than bytes: an interpreter mapped to the upload directory, a parser with a known dangerous mode, an inline render on the app origin, or a path built from the original filename. Cite the type, size, name and storage decisions at `path:line`.
- **False-positive traps:**
  - Files land in object storage or a static host with no interpreter.
  - Downloads are served with `Content-Disposition: attachment` and `nosniff`, or from a separate sandbox domain.
  - The framework replaces the filename with a generated key before writing; the original name lives only in metadata.
  - A polyglot or oversized file is stored but no component ever parses it.
- **Fix:** at the upload service, use generated names, check content against an allowlist, set size limits before buffering, keep storage outside any executable root, and serve from an isolated origin with a fixed content type.

## Path traversal and local file inclusion

- **Where it lives:** download and export endpoints, template or language selectors (`include`, `render(name)`), static-file handlers, log viewers, archive extraction (zip slip), and `path.join`, `Path.Combine` or `os.path.join` fed by request data.
- **Proof:** show a request value reaching a filesystem read, write or include without being resolved and compared to a base directory. Absolute input is a separate case: `Path.Combine` and `os.path.join` drop the base when the second argument is absolute. For inclusion, show the included file is executed, not just read.
- **False-positive traps:**
  - A filter that blocks `../` text with a fixed error proves nothing in either direction; read what happens after it.
  - The value is checked against a fixed map of allowed names before use.
  - The path is canonicalized and prefix-checked against the base after resolution, including symlinks.
  - A sandboxed or chrooted reader confines the access you claim.
- **Fix:** take an identifier, not a path; otherwise canonicalize, resolve links and require the result to stay inside the base directory. For archives, check every entry name the same way before writing it.

## Source and secret leaks

- **Where it lives:** build and deploy output (`.map` files shipped to production, `.git` or `.env` inside the web root), web server configs that serve dotfiles or backups, framework debug consoles and actuator endpoints, OpenAPI documents describing internal routes, and client bundles that inline environment variables.
- **Proof:** show the configuration or build step that places the file or value on a public path, and what it contains: a live credential, a signing key, internal routes that lack authorization. A secret in a bundle needs the variable's prefix rule or inlining step cited, such as `NEXT_PUBLIC_` or a `define` block.
- **False-positive traps:**
  - Publishable keys (maps, analytics, payment publishable keys) restricted by origin or scope are meant to be public; the finding is a missing restriction, not the key itself.
  - Source maps for open-source code reveal nothing new.
  - The debug endpoint is bound to localhost or a management port with its own authentication.
  - Scanner hits on example, test or revoked values.
- **Fix:** exclude maps, dotfiles and backups at the build or server config, gate debug endpoints by profile and network, and move secrets to server-only configuration. Rotate any key that shipped.

## Source

Adapted from Claude-BugHunter by elementalsouls (https://github.com/elementalsouls/Claude-BugHunter at 9ba66b1b3a7d84c501202a527cdc22b2051c5711; code MIT, content CC BY 4.0), rewritten for read-only source review. Licence: [claude-bughunter-LICENSE.txt](claude-bughunter-LICENSE.txt).
