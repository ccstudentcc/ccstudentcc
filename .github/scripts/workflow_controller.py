from __future__ import annotations

"""Thin workflow orchestrator entrypoint.

This module coordinates the runtime loop by delegating state and execution
responsibilities to dedicated modules.
"""

import sys
from pathlib import Path
from typing import Any, cast

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_common import POLL_SECONDS, REGISTRY_PATH, WORKFLOW_PATH, iso_now, load_json
from workflow_contract import worker_contracts_by_name
from workflow_runtime import (
    collect_ready_tasks,
    launch_task,
    mark_unreachable_tasks,
    poll_running_tasks,
    publish_event,
    refresh_pool_state,
    refresh_scheduler_state,
    workflow_finished,
)
from workflow_state import build_persist_signature, initialize_state, log_run_summary, persist, write_step_summary


def main() -> int:
    """Run orchestration loop until all tasks reach terminal states.

    Returns:
        Process exit code. This controller currently returns 0 and records
        failed tasks through state/dead-letter artifacts.
    """
    registry = cast(dict[str, Any], load_json(REGISTRY_PATH, {"workers": []}))
    workflow_spec = cast(dict[str, Any], load_json(WORKFLOW_PATH, {"workflow": {}, "scheduler": {}, "worker_pools": [], "tasks": []}))
    state, dead_letters, task_specs_list = initialize_state(registry, workflow_spec)
    persist_signature = build_persist_signature(state, dead_letters)
    registry_by_name = worker_contracts_by_name(registry)
    task_specs = {task["name"]: task for task in task_specs_list}
    running: dict[str, dict[str, Any]] = {}
    publish_event(state, "workflow.started", state["workflow"]["name"], f"Trigger={state['scheduler']['trigger']}")

    persist_signature = persist(state, dead_letters, registry, previous_signature=persist_signature, force=True)

    while True:
        ready_queue = collect_ready_tasks(task_specs, state)
        refresh_pool_state(state, workflow_spec, ready_queue, running)
        refresh_scheduler_state(state, ready_queue, running)

        launched_any = False
        available_slots = {
            pool_name: max(0, pool_state["desired_workers"] - pool_state["active_workers"])
            for pool_name, pool_state in state["worker_pools"].items()
        }

        for task_name in ready_queue:
            if task_name in running:
                continue
            pool_name = state["tasks"][task_name]["pool"]
            if available_slots.get(pool_name, 0) <= 0:
                continue
            launch_task(task_name, task_specs, state, registry_by_name, running)
            available_slots[pool_name] -= 1
            launched_any = True

        if launched_any:
            refresh_pool_state(state, workflow_spec, ready_queue, running)
            refresh_scheduler_state(state, ready_queue, running)

        persist_signature = persist(state, dead_letters, registry, previous_signature=persist_signature)

        if workflow_finished(state) and not running:
            break

        if running:
            import time

            time.sleep(POLL_SECONDS)
            poll_running_tasks(state, registry_by_name, running, dead_letters)
            mark_unreachable_tasks(task_specs, state)
            persist_signature = persist(state, dead_letters, registry, previous_signature=persist_signature)
            continue

        if not ready_queue:
            future_deferred = any(task["status"] == "Deferred" for task in state["tasks"].values())
            if future_deferred:
                import time

                time.sleep(1)
                continue
            mark_unreachable_tasks(task_specs, state)
            if workflow_finished(state):
                break

    state["workflow"]["status"] = "Completed"
    state["workflow"]["completed_at"] = iso_now()
    publish_event(state, "workflow.completed", state["workflow"]["name"], "All terminal task states reached")
    refresh_scheduler_state(state, [], running)
    persist_signature = persist(state, dead_letters, registry, previous_signature=persist_signature, force=True)
    write_step_summary(state, dead_letters)
    log_run_summary(state, dead_letters)

    failed = [task for task in state["tasks"].values() if task["status"] == "Failed"]
    if failed:
        print(f"Completed with {len(failed)} failed task(s) recorded in dead-letter queue")
    else:
        print("Completed with all tasks successful")
    return 0


if __name__ == "__main__":
    sys.exit(main())
