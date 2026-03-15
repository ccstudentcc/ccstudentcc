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
- `.github/scripts/workflow_runtime.py` (scheduler loop and worker process execution)
- `.github/scripts/workflow_state.py` (state bootstrap, snapshots, persistence, step summary)

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
- `snapshot.yml`: Refreshes showcase snapshot and carousel SVG.
- `featured-projects.yml`: Refreshes featured projects section.
- `wakatime.yml`: Refreshes WakaTime section.
- `daily-quote.yml`: Refreshes daily quote section.

All worker workflows support:
- `workflow_call`
- `workflow_dispatch`

## 3) Required Inputs / Secrets

Used by orchestrator and worker scripts:
- `GITHUB_TOKEN` (provided by GitHub Actions runtime)
- `WAKATIME_API_KEY` (required for WakaTime update path)

Behavior notes:
- Missing `WAKATIME_API_KEY` does not break whole orchestration; the related task can be skipped by condition.
- Missing optional README markers should not hard-fail the controller; unavailable sections are skipped with warnings.
- `workflow-manager` launches independent workers in parallel, but all README marker updates are serialized through `.github/scripts/readme_utils.py` so one worker cannot overwrite another worker's section.
- `featured-projects` worker calls `readme_utils.py --allow-missing-markers` so a missing `<!--START_SECTION:featured-->` block does not fail the task; the README update is silently skipped while the repo-discovery step still runs.

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
- `state.json.flow_order`: latest realized canonical stage cycle for runtime verification.
- `dag.json`: resolved graph snapshot.
- `scheduler.json`: scheduler policy and queue counters.
- `queue.json`: ready/deferred/retry/running/terminal queue snapshot.
- `event-log.json`: emitted orchestration events.
- `dead-letters.json`: exhausted failures from the current run only (reset at start of each run; historical entries are preserved in git history).
- `metadata-store.json`: persisted document inventory and consistency metadata.

## 5) How To Run

### A. Run in GitHub (recommended)

1. Open Actions -> `Workflow Manager`.
2. Click `Run workflow` (workflow_dispatch).
3. Wait for job `orchestrate` to finish.
4. Verify commit with updated files:
   - `README.md`
   - `assets/showcase-carousel.svg`
   - `.github/manager/state/*.json`

### B. Run locally (script-level)

From repository root:

```bash
set PYTHONPATH=.github/scripts
python .github/scripts/validate_workflow_chain.py
python .github/scripts/workflow_controller.py
```

Optional env for local parity:

```bash
set WAKATIME_API_KEY=your_key_here
```

Expected outcomes:
- Chain validation passes before orchestration starts.
- README automation panel sections update.
- state snapshots are regenerated in `.github/manager/state/`.

CI order in `.github/workflows/workflow-manager.yml`:
1. Validate workflow chain coverage
2. Run workflow controller
3. Commit README and state artifacts

Worker execution note:
- The manager does not call the standalone worker workflows via `workflow_call`; it runs worker scripts directly inside one orchestrated job.
- Standalone workflows such as `wakatime.yml` remain useful for isolated manual retries.
- Because the manager runs multiple README writers concurrently, all section replacements must go through the shared locked updater in `.github/scripts/readme_utils.py`.
- Workers whose README blocks are optional should use the locked updater's tolerant mode instead of failing the entire manager run on missing markers.

## 6) Badge and Rendering Rules

- Use Shields `static/v1` format for custom badges.
- Avoid legacy `/badge/` URLs for dynamic labels/messages with hyphens.
- Keep README marker pairs intact for all managed sections:
  - `<!--START_SECTION:...-->`
  - `<!--END_SECTION:...-->`
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

### Symptom: running Workflow Manager does not refresh WakaTime, but running `Waka Readme` directly does

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
2. Add task node to `.github/manager/workflow.json` with:
   - `name`, `worker`, `pool`, `priority`, `depends_on`, `condition`, `delay_seconds`
3. Validate DAG remains acyclic.
4. Ensure script writes deterministic output.
5. Run orchestrator once and verify:
   - task appears in README automation panel
   - state snapshots include expected transitions

When adding a new README managed block:
1. Add START/END markers in `README.md`.
2. Add renderer entry in controller section map.
3. Keep graceful degradation: missing blocks should warn, not crash.

## 9) AI Agent Checklist

Before making automation changes:
1. Read `.github/workflows/workflow-manager.yml`.
2. Read `.github/manager/workflow.json` and `.github/manager/registry.json`.
3. Read `.github/scripts/workflow_controller.py`, `.github/scripts/workflow_runtime.py`, `.github/scripts/workflow_state.py`.
4. Confirm target README markers exist.

After making changes:
1. Run controller (or CI workflow) once.
2. Confirm no stale `/badge/` custom badges remain.
3. Confirm state artifacts and README are consistent.
4. Confirm no new diagnostics in touched files.

## 10) Source of Truth

Automation behavior is defined by these files together:
- `.github/workflows/workflow-manager.yml`
- `.github/manager/workflow.json`
- `.github/manager/registry.json`
- `.github/scripts/workflow_controller.py`
- `.github/scripts/workflow_common.py`
- `.github/scripts/workflow_runtime.py`
- `.github/scripts/workflow_state.py`
- `.github/scripts/workflow_renderer.py`

If any conflict appears, prioritize controller runtime behavior and then sync docs/config accordingly.

## 11) Change Synchronization Policy

Whenever automation code is modified, update docs in the same change set to avoid stale guidance.

Minimum sync checklist per change:
1. If execution flow/state persistence changes, update sections 1, 4, 5, and 10.
2. If rendering logic or marker ownership changes, update sections 1, 6, and 10.
3. If secrets, triggers, or workflow names change, update sections 2, 3, and 5.
4. If extension steps change, update sections 8 and 9.
5. If dead-letter scope or tolerant-mode behavior changes, update sections 3, 4, and 7.
