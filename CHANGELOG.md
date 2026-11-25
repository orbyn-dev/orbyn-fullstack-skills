# Changelog

Each release is a Git tag `vMAJOR.MINOR.PATCH` on `main` with a matching GitHub
release. [Releases in CONTRIBUTING](CONTRIBUTING.md#releases) explains what bumps
which number. Pin a consumer to a tag, not to a moving `main`.

## 1.1.0 (2026-11-25)

- **Diff review (`interrogate`):** a file-by-file review pipeline adapted from
  Alibaba's Open Code Review (Apache-2.0): a coverage ledger that accounts for every
  changed file, grouping of related files, rules matched per group, a plan then a
  review with fixed finding fields, a location check, and a fact check that removes
  only findings the diff proves wrong. Includes exact, hardened Git commands for
  commit, range and workspace reviews. No skill removed or renamed.

## 1.0.0 (2026-11-25)

First versioned release. It covers everything merged to `main` up to this tag: 47
skills for Claude Code and Codex, with provenance, eval cases and library checks.

Highlights:

- **Workflow:** an owner stance for every maintained codebase, restating the goal in
  your own words, PR summaries for the reviewer, stacked PRs, child recovery and
  bounded delegation through the harness.
- **Design and testing:** simplify in order before optimizing or automating;
  black-box tests at the program's edge first, unit tests only where they matter;
  suite audits and golden cases.
- **Security:** vulnerability classes and a seven-question validation gate for
  source audits, privacy threat modelling, and `orbyn-opengrep` built from pinned
  source.
- **Product UI:** a read-only UX audit across flows, `orbyn-scripted-motion`,
  `orbyn-astryx`, `orbyn-affectui` and `orbyn-seo`.
- **Code quality:** a comment pass on a diff with a suppression audit, and rewrite
  parity for ports into Rust.
