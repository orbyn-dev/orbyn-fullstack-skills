# Rust: contracts to preserve

## Resolve the target

Inspect Cargo.toml edition/rust-version, rust-toolchain, Cargo.lock, enabled features and target. Preserve the minimum supported Rust version and public API contracts. The edition changes drop order: from 2024, `if let` temporaries drop before `else`, so a lock held in the condition no longer deadlocks the `else`. A `cargo fix --edition` rewrite to `match` keeps the old timing; review each one.

## Apply at the affected boundary

- Express owned versus borrowed data deliberately. Trace lifetime and concurrency ownership before adding clone, Arc or a mutex to silence the borrow checker. `Arc` shares ownership, not synchronization of mutable contents.
- Use Result and meaningful error propagation for expected failures. Keep unwrap/expect at documented invariants, not on untrusted input or routine I/O. Use `.get()` where an index comes from input.
- An async future can be dropped at any suspension point. Cancellation does not roll back a committed effect; design retry identity and cleanup at the effect owner, and check cancel safety for each `select!` branch.
- Do not hold a blocking mutex guard across await, and move blocking work to the runtime's blocking pool.
- Keep unsafe/FFI small with explicit allocation, aliasing, alignment and lifetime invariants. Safe wrappers must uphold them for every caller.

## Module structure

- **One module per capability**, with sub-modules per topic. A file of hundreds of
  types or thousands of lines becomes a folder: `mod.rs` (or `name.rs` beside
  `name/`; pick one layout per crate) declares the sub-modules and re-exports the
  public items by name with `pub use` at the old path's visibility, so existing `use`
  paths keep working.
- **Enforce, don't just group.** A folder of modules is not a boundary: make items
  `pub(crate)` or `pub(super)` unless another module must use them, and give each
  module a small public surface. When boundaries must hold across teams or builds,
  a workspace crate enforces them; split only when that need is real.
- **Thin transport.** Tauri commands, HTTP handlers and IPC adapters parse, call the
  owning module and map errors. Business rules live in the owner, where they can be
  tested without the transport.
- **One wrapper per effect.** Spawning git or another process, reading the home
  directory or formatting durations goes through one helper, not several hand-rolled
  copies. A whole-app move follows the
  [folder refactor playbook](../../architect/references/folder-refactor.md).

## Worked failure and correction

Return a borrow tied to its real input instead of allocating only to satisfy a signature:

```rust
fn first_tag(tags: &[String]) -> Option<&str> {
    tags.first().map(String::as_str)
}
```

An owned String is appropriate when the value must outlive tags, including when the
function builds it locally. The decision is lifetime ownership, not a blanket rule
against allocation.

## Traps to check

- Arithmetic overflow panics in debug and wraps in the default release profile. Use `checked_add` and friends on sizes and values from input.
- `as` truncates integers silently: `1234u16 as u8` is 210. Use `u8::try_from(value)`.
- `let _ = mutex.lock()` releases the guard immediately. Bind `let _guard` to hold it.
- `Vec` capacity is not initialized length. Prefer push/extend/resize; unsafe `set_len(n)` requires n within capacity and every newly exposed element initialized.
- Keep public types and dependency exposure intentional, and avoid generic layers without real callers. Debug output must not reveal secrets.

## Porting into Rust

When code moves into Rust from another language, prove each unit against the
original's recorded behaviour ([rewrite parity](../../figure-it-out/references/rewrite-parity.md)),
and check these first:

- **Profile before porting for speed.** One call across FFI costs more than a small
  function's work, and V8, the JVM and Go already run small functions fast. Move the
  loop with its callers, or pass the items once and run the whole batch in Rust.
  Code already compiled to machine code (numba `@njit`, Cython) gains little.
- **String offsets.** JavaScript, Java and C# index UTF-16 code units; Rust indexes
  UTF-8 bytes. Convert at the boundary wherever an offset or length crosses it.
- **Regex classes.** `\w`, `\b`, `\d` and case-insensitive matching are Unicode-aware
  in the `regex` crate but ASCII-only in JavaScript without `u` and in Java by default.
  Spell out `[A-Za-z0-9_]` or turn Unicode off where the original was ASCII. Record
  non-ASCII inputs such as the Turkish dotless i and the Kelvin sign. Lookaround and
  backreferences need `fancy-regex`.
- **Numbers.** A Python or Ruby int passed through must come back an int, not a float.
  Port with checked arithmetic where the original had big integers. Ported float code is
  rarely bit-identical (operation order, fused multiply-add, the math library), so agree
  a tolerance with the user instead of chasing bits.
- **Map order.** Use `indexmap` wherever the original's insertion order reaches output.
- **Errors.** Where the original throws or returns an error, return `Err` that maps
  back to the same error type; `unwrap` or `panic!` there replaces a catchable error
  with a crash.

## Evidence to report

Use configured cargo check/test, formatting and Clippy within authorized scope. Compilation proves neither unsafe-code soundness nor transactional cancellation safety. Test in release mode when overflow behavior matters.

## Primary references

Reviewed 24 October 2026: [Rust ownership](https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html), [Cargo rust-version](https://doc.rust-lang.org/cargo/reference/rust-version.html), [Rust unsafe](https://doc.rust-lang.org/book/ch20-01-unsafe-rust.html).

Focused semantics reviewed 30 October 2026: [Vec set_len safety](https://doc.rust-lang.org/std/vec/struct.Vec.html#method.set_len), [Arc thread safety](https://doc.rust-lang.org/std/sync/struct.Arc.html#thread-safety).

Reviewed 1 November 2026: [overflow and `as` casts](https://doc.rust-lang.org/reference/expressions/operator-expr.html), [Rust 2024 `if let` scope](https://doc.rust-lang.org/edition-guide/rust-2024/temporary-if-let-scope.html).
