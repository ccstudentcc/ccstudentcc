# Workflows Automation Guide

This document explains how humans and AI agents should operate the README automation workflows in this repository.

## 1) What This Automation Does

The automation stack updates dynamic sections in README.md and keeps runtime snapshots under `.github/manager/state/`.

Primary orchestrator:
- `.github/workflows/workflow-manager.yml`

Execution entrypoint:
- `.github/scripts/workflow_controller.py`

Orchestration modules:
- `.github/scripts/workflow_common.py` (shared constants/time/json/path helpers)
- `.github/scripts/workflow_contract.py` (worker contract normalization and validation)
- `.github/scripts/workflow_runtime.py` (scheduler loop and worker process execution)
- `.github/scripts/workflow_state.py` (state bootstrap, snapshots, persistence, step summary)

Managed worker shell:
- `.github/workflows/_managed-readme-worker.yml` (shared reusable workflow used by standalone worker wrappers)

Rendering entrypoint:
- `.github/scripts/workflow_renderer.py`

Core flow order:
1. Orchestrator
2. DAG
3. Scheduler
4. Queue
5. State Store
6. Event Bus
7. Worker Pools
8. Worker Registry
9. Worker Health
10. Task State
11. Dead Letter Queue

Canonical one-line flow order:
- Orchestrator • DAG • Scheduler • Queue • State Store • Event Bus • Worker Pools • Registry • Health • Tasks • DLQ

Runtime enforcement note:
- The canonical flow order is recorded at runtime, not only described in docs.
- Each persistence pass writes the realized stage sequence into `state.json -> flow_order.latest_completed_cycle`.
- Validation fails when the latest cycle is out of order, incomplete, or diverges from the canonical sequence.

## 2) Workflow Inventory

- `workflow-manager.yml`: Main DAG orchestrator. Triggered by schedule and manual dispatch.
- `_managed-readme-worker.yml`: Shared reusable worker shell for standalone worker wrappers.
- `snapshot.yml`: Thin wrapper for snapshot refresh.
- `featured-projects.yml`: Thin wrapper for featured projects refresh.
- `wakatime.yml`: Thin wrapper for WakaTime refresh.
- `daily-quote.yml`: Thin wrapper for daily quote refresh.

Current display names in GitHub Actions:
- `README Workflow Manager`
- `README Worker Core`
- `README Worker - Snapshot`
- `README Worker - Featured Projects`
- `README Worker - WakaTime`
- `README Worker - Daily Quote`

All standalone worker workflows support:
- `workflow_call`
- `workflow_dispatch`
- concurrency isolation per worker (`readme-worker-<name>`)

Wrapper alignment rules:
- Each worker wrapper must call `./.github/workflows/_managed-readme-worker.yml`.
- Wrapper `execution_mode`, `command`, `summary_label`, `commit_scope`, and `required_secrets` must stay aligned with `.github/manager/registry.json`.
- For required secret wiring, keep registry and wrapper mappings aligned, but do not declare reserved `GITHUB_TOKEN` under `workflow_call.secrets`.
- In the shared worker shell, use non-reserved names for reusable-workflow secrets (for example `repo_token`), then map them to runtime environment variables.

## 3) Worker Contract / Required Inputs / Secrets

Contract source of truth:
- `.github/manager/registry.json`
- `.github/scripts/workflow_contract.py`

Every managed worker contract now declares:
- `execution_mode`
- `workflow`
- `required_secrets`
- `commit_scope`
- `optional_readme_markers`
- `summary_label`

Used by orchestrator and worker scripts:
- `GITHUB_TOKEN` (provided by GitHub Actions runtime)
- `WAKATIME_API_KEY` (required for WakaTime update path)

Reusable workflow secret mapping rule:
- In `_managed-readme-worker.yml`, reusable secret names must avoid reserved-system collisions.
- Use `repo_token` as the reusable input secret and map it to runtime `GITHUB_TOKEN` in the worker command environment.
- Keep `wakatime_api_key` for WakaTime and map it to runtime `WAKATIME_API_KEY`.

Behavior notes:
- Missing `WAKATIME_API_KEY` does not break whole orchestration; the related task can be skipped by condition.
- Missing optional README markers should not hard-fail the controller; unavailable sections are skipped with warnings.
- Duplicate README markers are treated as a configuration error and must fail fast so managed updates never target an ambiguous block.
- `workflow-manager` launches independent workers in parallel, but all README marker updates are serialized through `.github/scripts/readme_utils.py` so one worker cannot overwrite another worker's section.
- `featured-projects` remains available as a standalone/manual worker, but it is not part of the default manager DAG; `snapshot` is the default repository showcase path.
- `featured-projects` worker calls `readme_utils.py --allow-missing-markers` so a missing `<!--START_SECTION:featured-->` block does not fail the task; the README update is silently skipped while the repo-discovery step still runs.
- Standalone worker wrappers expose the same secret requirements declared in the registry contract, so drift must be fixed in both places together.

## 4) Runtime State Files

The orchestrator persists concrete runtime artifacts here:
- `.github/manager/state/state.json`
- `.github/manager/state/dag.json`
- `.github/manager/state/scheduler.json`
- `.github/manager/state/queue.json`
- `.github/manager/state/event-log.json`
- `.github/manager/state/dead-letters.json`
- `.github/manager/state/metadata-store.json`

Quick meaning:
- `state.json`: top-level workflow, worker, and task states.
- `state.json.managed_jobs`: task names included in the default manager DAG.
- `state.json.standalone_jobs`: registered workers kept available for standalone/manual runs but excluded from the default manager DAG.
- `state.json.workers.<name>.contract`: persisted contract metadata for each worker (`execution_mode`, `workflow`, `required_secrets`, `commit_scope`, `optional_readme_markers`, `summary_label`).
- `state.json.tasks.<name>.contract`: task-level copy of the assigned worker contract metadata for easier debugging and rendering.
- `state.json.flow_order`: latest realized canonical stage cycle for runtime verification.
- `dag.json`: resolved graph snapshot.
- `scheduler.json`: scheduler policy and queue counters.
- `queue.json`: ready/deferred/retry/running/terminal queue snapshot.
- `event-log.json`: emitted orchestration events.
- `dead-letters.json`: exhausted failures from the current run only (reset at start of each run; historical entries are preserved in git history).
- `metadata-store.json`: persisted document inventory and consistency metadata.

## 5) How To Run

### A. Run in GitHub (recommended)

1. Open Actions -> `README Workflow Manager`.
2. Click `Run workflow` (workflow_dispatch).
3. Wait for job `orchestrate` to finish.
4. Verify commit with updated files:
   - `README.md`
   - `assets/showcase-carousel.svg`
   - `.github/manager/state/*.json`

### B. Run locally (script-level)

From repository root:

```powershell
$env:PYTHONPATH = ".github/scripts"
python .github/scripts/validate_workflow_chain.py
python .github/scripts/workflow_controller.py
```

Optional env for local parity:

```powershell
$env:WAKATIME_API_KEY = "your_key_here"
```

Expected outcomes:
- Chain validation passes before orchestration starts.
- README automation panel sections update.
- state snapshots are regenerated in `.github/manager/state/`.

CI order in `.github/workflows/workflow-manager.yml`:
1. Validate workflow chain coverage
2. Run workflow controller
3. Validate workflow chain post-run
4. Commit README and state artifacts

Manager / wrapper boundary:
- The manager does not call the standalone worker workflows via `workflow_call`; it runs worker scripts directly inside one orchestrated job.
- Standalone workflows such as `wakatime.yml` remain useful for isolated manual retries and for keeping each worker's Actions contract explicit.
- The controller/runtime path now loads registry workers through `.github/scripts/workflow_contract.py`, validates contracts up front, and still preserves the raw `command` list for local process spawning.
- The default manager DAG persists only meaningful state transitions; heartbeat-only noise does not trigger a full state-store write or README dashboard re-render.
- The README automation dashboard summarizes retry traces, dead-letter reasons, and optional-marker skips into concise status text; inspect the event log or workflow run for full traceback details.
- Because the manager runs multiple README writers concurrently, all section replacements must go through the shared locked updater in `.github/scripts/readme_utils.py`.
- Workers whose README blocks are optional should use the locked updater's tolerant mode instead of failing the entire manager run on missing markers.

## 6) Badge and Rendering Rules

- Use Shields `static/v1` format for custom badges.
- Avoid legacy `/badge/` URLs for dynamic labels/messages with hyphens.
- Keep README marker pairs intact for all managed sections:
  - `<!--START_SECTION:...-->`
  - `<!--END_SECTION:...-->`
- Every managed marker pair must appear exactly once; duplicates are treated as invalid configuration and must be fixed before rerunning automation.
- WakaTime section scope:
   - Primary `Code Time` badge prefers WakaTime `status_bar/today` and falls back to `stats/last_7_days` when today payload is empty or stale.
   - Optional `All Time` badge may be rendered separately when `all_time_since_today` is available.
   - Focus summary and Weekly Breakdown use explicit `summaries?start=<monday>&end=<today>&timezone=Asia/Shanghai` for current-week coverage; when summaries are empty, category rows fall back to `stats/last_7_days`.

## 7) Troubleshooting

### Symptom: badge not found

Checks:
1. Ensure badge URL uses `https://img.shields.io/static/v1?...`.
2. Ensure `label` and `message` are URL-encoded.
3. Ensure color values are valid hex-like strings expected by shields.

### Symptom: section not updated in README

Checks:
1. Confirm marker pair exists exactly once.
2. Confirm marker keys match controller section names.
3. Check workflow logs for skipped section warnings.
4. If the block is optional, ensure the worker uses tolerant marker handling instead of failing the run.

### Symptom: task keeps retrying or fails

Checks:
1. Inspect `.github/manager/state/state.json` task status/message.
2. Inspect `.github/manager/state/dead-letters.json`.
3. Inspect `.github/manager/state/event-log.json` for failure timeline.
4. Inspect `.github/manager/state/state.json -> flow_order.latest_completed_cycle` to confirm the latest manager pass realized the full canonical stage order.

### Symptom: DLQ section shows a failure for a task that succeeded in the latest run

Checks:
1. Dead letters are reset at the start of each orchestration run; stale DLQ entries from previous runs do not carry over.
2. If the dead letter still appears after a run where the task shows `status: Success`, ensure local state files are in sync with the latest commit (`git pull`).
3. Trigger the workflow once more; the DLQ section should be empty when all current-run tasks succeed.

### Symptom: running Workflow Manager does not refresh WakaTime, but running `README Worker - WakaTime` directly does

Checks:
1. Confirm the WakaTime task is not merely skipped by inspecting `.github/manager/state/state.json` for `tasks.wakatime.status`.
2. Confirm `WAKATIME_API_KEY` is available to the manager run, because the manager executes `.github/scripts/update_wakatime.py` directly rather than dispatching `wakatime.yml`.
3. Confirm every README-writing worker still uses `.github/scripts/readme_utils.py`; bypassing the shared lock can reintroduce lost updates during parallel runs.
4. Confirm `flow_order.latest_completed_cycle.completed_sequence` still matches the canonical chain before debugging worker-specific logic.

### Symptom: `status_bar/today` has non-zero time, but Weekly Breakdown shows only older days or all zeros

Checks:
1. Query `summaries?range=This Week&timezone=Asia/Shanghai` and compare with `summaries?start=<monday>&end=<today>&timezone=Asia/Shanghai`; some accounts may receive stale windows for the relative range.
2. Treat explicit `start/end` as source of truth for weekly rendering and keep `status_bar/today` for the Code Time badge.
3. Confirm returned `data[].range.date` includes today's local date before investigating README rendering.

## 8) How To Extend (Human or AI)

When adding a new automation task:
1. Add worker metadata in `.github/manager/registry.json`.
2. Include the full worker contract:
   - `execution_mode`
   - `workflow`
   - `required_secrets`
   - `commit_scope`
   - `optional_readme_markers`
   - `summary_label`
3. Add task node to `.github/manager/workflow.json` with:
   - `name`, `worker`, `pool`, `priority`, `depends_on`, `condition`, `delay_seconds`
4. Create or update the standalone worker wrapper under `.github/workflows/` so it delegates to `_managed-readme-worker.yml` and stays contract-aligned.
5. Validate DAG remains acyclic.
6. Ensure script writes deterministic output.
7. Run validator and orchestrator once, then verify:
   - task appears in README automation panel
   - state snapshots include expected transitions
   - wrapper workflow still matches registry contract

When adding a new README managed block:
1. Add START/END markers in `README.md`.
2. Add renderer entry in controller section map.
3. Keep graceful degradation: missing blocks should warn, not crash.
4. If the block is optional for a worker, record that marker in the worker contract and preserve tolerant update behavior in the script.

## 9) AI Agent Checklist

Before making automation changes:
1. Read `.github/workflows/workflow-manager.yml`.
2. Read `.github/manager/workflow.json` and `.github/manager/registry.json`.
3. Read `.github/scripts/workflow_contract.py`, `.github/scripts/workflow_controller.py`, `.github/scripts/workflow_runtime.py`, `.github/scripts/workflow_state.py`.
4. Confirm target README markers exist.
5. Confirm each standalone worker wrapper still matches its registry contract.

After making changes:
1. Run `validate_workflow_chain.py` first.
2. Run controller (or CI workflow) when runtime/state behavior changed.
3. Confirm no stale `/badge/` custom badges remain.
4. Confirm state artifacts and README are consistent.
5. Confirm no new diagnostics in touched files.
6. Check git status in the active workflow worktree before handing off.

## 10) Source of Truth

Automation behavior is defined by these files together:
- `.github/workflows/workflow-manager.yml`
- `.github/workflows/_managed-readme-worker.yml`
- `.github/workflows/*.yml` standalone worker wrappers
- `.github/manager/workflow.json`
- `.github/manager/registry.json`
- `.github/scripts/workflow_contract.py`
- `.github/scripts/workflow_controller.py`
- `.github/scripts/workflow_common.py`
- `.github/scripts/workflow_runtime.py`
- `.github/scripts/workflow_state.py`
- `.github/scripts/workflow_renderer.py`
- `.github/scripts/validate_workflow_chain.py`

If any conflict appears, prioritize validated controller/runtime behavior, then registry contract, then sync wrappers/docs accordingly.

## 11) Change Synchronization Policy

Whenever automation code is modified, update docs in the same change set to avoid stale guidance.

Minimum sync checklist per change:
1. If worker contract fields or wrapper boundaries change, update sections 1, 2, 3, 8, 9, and 10.
2. If execution flow/state persistence changes, update sections 1, 4, 5, and 10.
3. If rendering logic or marker ownership changes, update sections 1, 6, and 10.
4. If validator coverage changes, update sections 5, 8, 9, and 10.
5. If secrets, triggers, or workflow names change, update sections 2, 3, and 5.
6. If dead-letter scope or tolerant-mode behavior changes, update sections 3, 4, and 7.
