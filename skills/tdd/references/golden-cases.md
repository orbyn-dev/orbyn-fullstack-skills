# Golden cases: black-box tests at the program's edge

A golden case is one input and the output it must produce, both stored as files. A
small runner feeds every input to the real program and fails when anything differs.
The test never looks inside: it checks what a user or caller would see.

## Lay out the cases

```text
tests/cases/
  import-valid-csv/
    cmd            # the arguments, one per line
    stdin          # optional
    expected.out   # stdout
    expected.err   # stderr, often empty
    expected.code  # exit code
  import-missing-header/
    ...
```

One folder per behaviour, named after it. Include the failures callers depend on:
bad input, a missing file, a refused permission. For a service, the case is a request
(method, path, headers, body) and the expected status, headers that matter and body.
For a job, the input state and the state it must leave behind.

## A runner fits in one screen

```sh
#!/usr/bin/env bash
# Runs every case against the built program; prints a diff and fails on any change.
set -u
fail=0; ran=0
for dir in tests/cases/*/; do
  [ -d "$dir" ] || continue
  name=$(basename "$dir"); ran=$((ran + 1))
  args=()
  if [ -f "${dir}cmd" ]; then
    while IFS= read -r line || [ -n "$line" ]; do args+=("$line"); done < "${dir}cmd"
  fi
  stdin="${dir}stdin"; [ -f "$stdin" ] || stdin=/dev/null
  out=$(mktemp); err=$(mktemp)
  ./bin/tool ${args[@]+"${args[@]}"} < "$stdin" > "$out" 2> "$err"
  code=$?
  if [ -f tests/mask.sed ]; then  # e.g. s/[0-9a-f]{8}-[0-9a-f-]{27}/<id>/g
    for f in "$out" "$err"; do sed -E -f tests/mask.sed "$f" > "$f.m" && mv "$f.m" "$f"; done
  fi
  if ! diff -u "${dir}expected.out" "$out" ||
     ! diff -u "${dir}expected.err" "$err" ||
     [ "$code" != "$(cat "${dir}expected.code" 2>/dev/null)" ]; then
    echo "FAIL $name (exit $code)"; fail=1
  fi
  rm -f "$out" "$err"
done
[ "$ran" -gt 0 ] || { echo "no cases ran"; exit 1; }
exit $fail
```

This one compares stdout, stderr and the exit code. Extend it for the other effects a
case pins, such as files written or rows in a disposable database.

Use the project's own language and test runner if it already has one that can do
this. The shape matters, not the tool.

## Keep the expected files honest

- **Values that change by themselves** (timestamps, generated ids, temporary paths)
  are masked: write `<id>` in the expected file and add the matching replacement to
  `tests/mask.sed`, which the runner applies to the actual output. Never change the program to make output stable.
  Before trusting a case, run it twice: anything that differs between runs needs a
  mask ([rewrite parity](../../figure-it-out/references/rewrite-parity.md#record-from-the-outside)
  covers runs that vary).
- **Update expected files only on purpose.** A runner flag may rewrite them, but the
  diff goes into the PR and someone reads it. Regenerating to turn red into green
  hides exactly the change the case exists to catch.
- **Keep each case small.** An expected file a reviewer cannot read in a minute will
  be regenerated, not read. Split it into cases that each pin one behaviour.

## When it is slow

Start the program once and feed it many inputs (one server, many requests; one
process reading a batch). Run case folders in parallel. Do not swap a slow golden case
for unit tests of its internals.

## When not to use it

- The output has no stable shape even after masking, such as rendered pixels or
  free-form model text. Use a visual check or an eval.
- The behaviour is a pure calculation with many edge cases that the edge cannot
  reach cheaply. That is the narrow case for a unit test in [SKILL.md](../SKILL.md#choose-the-test-black-box-first).

## Worked failure (synthetic)

An agent adds 140 unit tests for a CSV importer's helpers, all green. A real file with
a byte-order mark fails in production, because no test ran the importer on a file.
One golden case with that file catches it, and most of the helper tests can go once
the cases cover their behaviour ([suite audit](suite-audit.md)).
