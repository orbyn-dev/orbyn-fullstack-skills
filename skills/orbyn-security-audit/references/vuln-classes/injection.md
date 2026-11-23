# Injection

Use during an explicitly requested local source audit ([audit protocol](../protocol.md)). Each class lists where it lives in code, what proves it at `path:line`, the traps that make a candidate false, and the fix at the owning boundary ([remediation patterns](../../../orbyn-security-remediation/references/finding-patterns.md)).

## SQL injection

- **Where it lives:** string-built queries (`"... WHERE id = " + id`, f-strings, template literals), raw-query escape hatches in ORMs (`raw`, `extra`, `sequelize.literal`, `whereRaw`), dynamic `ORDER BY`, column or table names, stored procedures that build SQL, and reporting or search builders.
- **Proof:** an attacker-controlled value travels from the request to a query string at `path:line` without a bound parameter, and without an allowlist for identifiers, which cannot be parameterised.
- **False-positive traps:**
  - The value is bound, even though the SQL text looks concatenated (placeholders built in a loop).
  - The input is cast to an integer or matched to an allowlist first.
  - The source is a constant or server configuration, not the request.
  - The ORM method used escapes in the version pinned.
- **Fix:** parameterised queries in the data-access layer, and identifiers mapped from a fixed allowlist.

## NoSQL injection

- **Where it lives:** document queries built from parsed request bodies (`find({ user: req.body.user })`), operator-bearing keys accepted from JSON or bracketed query strings, `$where` and server-side JavaScript, and aggregation stages built from input.
- **Proof:** at `path:line`, a request value that can be an object rather than a scalar reaches a query position where operators are interpreted, or input reaches a server-side code evaluation stage.
- **False-positive traps:**
  - A schema or validator coerces the field to a string before the query.
  - The driver or ODM strips operator keys (sanitise option, strict query mode, cite it).
  - The query uses only fixed keys with typed values.
- **Fix:** validate types at the boundary, reject operator keys from input, and never evaluate code built from requests.

## LDAP injection

- **Where it lives:** directory lookups for sign-in, user search and group membership that build filter strings or distinguished names from input.
- **Proof:** a request value reaches an LDAP filter or DN at `path:line` without filter escaping (parentheses, asterisk, backslash, NUL) or DN escaping, in a lookup whose result grants identity or reveals directory data.
- **False-positive traps:**
  - The library's filter builder escapes values.
  - Input is restricted to a strict character set first.
  - Authentication relies on a bind with the user's password, so a widened search filter does not grant a session.
- **Fix:** escape with the library's filter and DN encoders, or build filters with its typed API.

## Server-side template injection

- **Where it lives:** calls that compile a template from a string (`render_template_string`, `Template(src)`, `Handlebars.compile(userText)`, `Velocity.evaluate`), user-editable email or notification templates, and preview endpoints.
- **Proof:** attacker-controlled text becomes template source, not template data, at `path:line`, in an engine that permits expressions or object access, and the render runs on the server.
- **False-positive traps:**
  - Input is passed only as a context variable to a fixed template file.
  - The double-brace syntax is evaluated by a client framework in the browser, which makes it a client-side issue.
  - The engine is logic-less or sandboxed with no object access in the version pinned.
  - A preview renders client-side and the send path uses a fixed template.
- **Fix:** keep templates as trusted files, pass user text only as data, and use a sandboxed or logic-less engine for user-authored templates.

## XML external entities

- **Where it lives:** XML parsers handling uploads, SOAP, SAML, SVG, office documents, RSS and configuration imports, plus their factory settings (`DocumentBuilderFactory`, `XMLInputFactory`, `lxml` `resolve_entities`, `libxml` options).
- **Proof:** at `path:line`, a parser processing attacker-supplied XML with DTDs or external entities enabled, either by explicit setting or by the platform default in the version pinned.
- **False-positive traps:**
  - The parser disables DTDs or external entities (cite the setting).
  - The language default is safe in that version.
  - The XML comes only from a trusted internal source.
  - Parsing goes through a hardened wrapper such as `defusedxml`.
- **Fix:** disable DTDs and external entities in one shared parser factory, and use it everywhere.

## Unsafe deserialisation

- **Where it lives:** native object deserialisers fed from requests, cookies, queues or caches: `pickle.loads`, `yaml.load` without a safe loader, Java `ObjectInputStream`, .NET `BinaryFormatter`, PHP `unserialize`, and JSON libraries with polymorphic type handling enabled.
- **Proof:** attacker-controlled bytes reach a deserialiser at `path:line` that can instantiate arbitrary types. For `pickle`, unsafe `yaml.load`, `BinaryFormatter`, PHP `unserialize` and similar, untrusted input reaching the call is the finding; no gadget chain needs to be shown. For polymorphic JSON or allowlist-based formats, show the type information read from the input.
- **False-positive traps:**
  - The data is signed or encrypted with a server key and verified before deserialising.
  - A safe loader or type allowlist is in place.
  - The format is plain data (JSON into fixed types).
  - The source is a trusted internal store that attackers cannot write to.
- **Fix:** use data-only formats bound to explicit types; where native formats remain, verify integrity first and restrict types to an allowlist.

## Command execution and code evaluation

- **Where it lives:** shell calls (`exec`, `system`, `subprocess` with `shell=True`, backticks, `Runtime.exec` with a single string), `eval`, `Function`, dynamic `require` or import, script engines, and file writes into executable or interpreted locations.
- **Proof:** attacker input reaches a shell string, an evaluated expression or a dynamically loaded module at `path:line`; or a write lands in a location the server later interprets.
- **False-positive traps:**
  - Arguments are passed as an array with no shell, and the binary is fixed.
  - Input is matched to an allowlist.
  - The evaluated code is a constant.
  - An upload directory is served as static content with no interpreter.
  - A file read or a reflected error is a different, smaller class.
- **Fix:** call programs with argument arrays and fixed binaries, remove `eval` on input, and keep writable paths out of anything executed.

## Source

Adapted from Claude-BugHunter by elementalsouls (https://github.com/elementalsouls/Claude-BugHunter at 9ba66b1b3a7d84c501202a527cdc22b2051c5711; code MIT, content CC BY 4.0), rewritten for read-only source review. Licence: [claude-bughunter-LICENSE.txt](claude-bughunter-LICENSE.txt).
