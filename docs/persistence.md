# Persistence and write-batching

This project persists controller state and message-queue artifacts using an "atomic-file-write" transaction model combined with optional write-batching to coalesce frequent updates.

Summary

- Transaction model: `atomic-file-write` — write content to a temporary file in the same directory, fsync/flush, then atomically replace the target file.
- Write batching: enabled by default to reduce write frequency and amortize IO.
- Debounce window: `WORKFLOW_WRITE_DEBOUNCE_SECONDS` controls how long updates are coalesced (default: `2` seconds).
- Metrics and errors: persistence writes update a metrics file and record unrecoverable failures to a log for post-mortem analysis.

Files created by the persistence subsystem

- `.github/manager/state/persistence-metrics.json`
  - JSON object with counters and last error details. Keys include `total_writes`, `failed_writes`, `total_attempts`, `failed_attempts`, `last_error`, `last_error_at`, `last_failed_path`, and `last_error_traceback`.

- `.github/manager/state/persistence-errors.log`
  - Append-only log of unrecoverable persistence failures (human-readable timestamped entries).

Environment variables

- `WORKFLOW_WRITE_BATCHING` (string `true`/`false`)
  - When `true` (default), the runtime batches writes and coalesces frequent updates.
  - When `false`, writes are performed immediately (useful for determinism in development or debugging).

- `WORKFLOW_WRITE_DEBOUNCE_SECONDS` (integer, default: `2`)
  - Number of seconds used as the debounce window for coalescing writes. Larger windows reduce I/O but increase the time until an update is persisted.

Developer guidance

- If your code performs frequent state updates (e.g., heartbeat updates), call `flush_json_writes(force=True)` during shutdown or before a prolonged pause to ensure batched updates are written.
- For debugging or unit tests that require immediate persistence, set `WORKFLOW_WRITE_BATCHING=false` or call `flush_json_writes(force=True)`.
 - For debugging or unit tests that require immediate persistence, set `WORKFLOW_WRITE_BATCHING=false` or call `flush_json_writes(force=True)`.
 - Note: `persist(..., force=True)` no longer unconditionally enqueues a new write when the runtime state has not meaningfully changed. The controller computes a persist signature and will skip enqueueing/writing if the signature matches the previous one. When `force=True` and no change is detected, the runtime will only attempt to flush any already-enqueued writes but will not create redundant writes of identical content.
- The persistence layer retries failed writes. Exhausted retries are recorded in `.github/manager/state/persistence-errors.log` and the metrics file.

Operational notes

- The persistence metrics file is written best-effort by the persistence helper. Do not rely on it for strong correctness guarantees; it is meant as an operational aid for troubleshooting.
- The atomic-file-write implementation includes a bounded retry loop to avoid transient contention when writing repository files (useful when multiple processes may touch the same file during testing or CI).

Example: disabling batching for a benchmark run

```
WORKFLOW_WRITE_BATCHING=false python -m scripts.run_benchmark
```

See also: `docs/workflows-automation-guide.md` for higher-level flow and examples.
