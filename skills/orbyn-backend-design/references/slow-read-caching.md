# Caching slow reads

Use when a read is slow (seconds) and its data may be a little old without harm,
and a measured screen depends on it. Measure first: see the
[slow screen method](../../orbyn-workflow/playbooks/perf-issue.md#slow-screen-over-a-local-or-http11-service).
Caching a read nobody measured adds a staleness bug for no gain.

## Stale while revalidate

Serve the last value at once, refresh in the background, then tell clients.

- **Single flight per key.** One refresh runs at a time; later callers reuse the
  last value.
- **Bounded keys and explicit TTL constants,** named and reviewed, not magic numbers.
  Keep TTLs short until the client reacts to change events.
- **Key by everything that changes the answer:** project, thread, engine, account.
  Never share a cache across them.
- **Invalidate on every write** that changes the data: drop exactly the affected
  keys. A background watcher must not drop a whole project's cache on each pass.
- **A force flag reads for real.** Refresh buttons and checks after an action use it.
- **Keep authorization and trust checks on cache hits.** A cached value is data,
  not permission.
- **Never cache progress or state that decides an action** (update progress, "is it
  running") behind a TTL.
- **Do not hold a lock across `await`** while refreshing.
- **Announce fresh data** on the existing event stream with a topic name per key
  family, so clients re-read quietly. Add no second channel.

## Check before merge

Staleness after each write path, key completeness, invalidation coverage, lock
scope, and whether the first cold read still blocks an interactive path. Cold reads
stay slow; say so in the report.
