# Contributing Guide

Thanks for your interest in contributing to this repository.

## Development Setup

1. Fork the repository and create your branch from main.
2. Clone your fork and enter the project directory.
3. Ensure Python 3.11+ is available.
4. Run tests before and after your change:

```bash
python -m pytest -q
python .github/scripts/validate_workflow_chain.py
```

## Recommended Workflow

1. Understand the scope and check related docs:
- README.md
- docs/workflows-automation-guide.md

2. Make focused changes with clear intent.
3. Keep changes deterministic for automation outputs.
4. Validate locally using tests and workflow-chain validation.
5. Commit using conventional messages.

## Commit Message Style

Use short, action-focused messages:

- feat(scope): add new behavior
- fix(scope): correct broken behavior
- refactor(scope): internal improvement without behavior change
- docs(scope): documentation-only updates
- chore(scope): maintenance work

Examples:

- fix(workflows): add explicit write permissions for worker wrappers
- docs(repo): add contribution and security policies

## Pull Request Checklist

Before opening a PR, confirm:

- Tests pass: python -m pytest -q
- Workflow chain validation passes
- No unrelated file changes are included
- Documentation updated if behavior changed
- README marker blocks remain valid and unique

## Coding Standards

- Prefer simple, readable code over clever shortcuts.
- Keep functions small and explicit.
- Fail fast with clear errors; avoid silent exception handling.
- Do not hardcode secrets.
- Do not introduce breaking dependency upgrades unless requested.

## Automation-Specific Notes

- The primary orchestration path is workflow-manager.yml.
- Standalone worker wrappers are manual/targeted entry points.
- Keep worker contract data in .github/manager/registry.json aligned with wrapper workflow inputs.
- If a worker updates README sections, keep marker pairs intact.

- Note: the controller now enables write-batching by default. To override or opt out, set `WORKFLOW_WRITE_BATCHING` in your environment.
- Default debounce: `WORKFLOW_WRITE_DEBOUNCE_SECONDS` (2 seconds). For graceful shutdowns or long pauses, code that may trigger writes should call `flush_json_writes(force=True)` to flush pending batched writes.
- `persist(..., force=True)` behavior: the controller computes a persist signature of the runtime-visible state. When the signature matches the previous persist (i.e., no meaningful state change), the controller will not enqueue redundant writes. When `force=True` and there is no change, the runtime will only attempt to flush already-enqueued writes but will not create duplicate writes of identical content. This prevents noisy repeated commits when nothing changed.
- Persistence fallback: failed writes are retried up to 3 times with exponential backoff; exhausted failures are logged to `.github/manager/state/persistence-errors.log` for later inspection.

Troubleshooting (common automation issues)

- Missing secrets: tasks guarded by `env_exists(...)` will be skipped if required env vars (e.g. `WAKATIME_API_KEY`) are not present. Add secrets to repository settings for CI or set them in your local environment (use a `.env` file ignored by git for local testing).
- Persistence mismatches: if dashboard `last persisted` does not match file mtimes, try disabling batching (`WORKFLOW_WRITE_BATCHING=false`) or use `flush_json_writes(force=True)` to force disk writes. Recent controller updates refresh the metadata manifest after flush to align `last_persisted_at` with actual file mtimes.
- Inspecting failures: collect `.github/manager/state/persistence-metrics.json`, `.github/manager/state/persistence-errors.log`, `.github/manager/state/event-log.json`, and `.github/manager/state/metadata-store.json` when diagnosing persistence issues.


Developer tips:

- If you add code that performs frequent state updates (e.g., heartbeat updates), keep `flush_json_writes(force=True)` available during shutdown sequences to avoid losing in-memory batched updates.
- To temporarily disable batching during debugging, set `WORKFLOW_WRITE_BATCHING=false` in your environment.
- The `WORKFLOW_WRITE_DEBOUNCE_SECONDS` value controls how long writes are coalesced — larger values reduce IO but increase amortized write latency.

## Reporting Issues

Please use GitHub Issues with:

- Expected behavior
- Actual behavior
- Reproduction steps
- Relevant logs or screenshots
- Environment details (OS, Python version)
