from __future__ import annotations

"""Runtime scheduling and worker execution logic for workflow controller."""

import math
import os
import subprocess
import tempfile
import time
from datetime import datetime
from typing import Any, cast

from workflow_common import (
    POLL_SECONDS,
    ROOT,
    TERMINAL_STATUSES,
    WAITING_STATUSES,
    format_time,
    iso_at,
    iso_now,
    utc_now,
)


def compute_health(worker_state: dict[str, Any], grace_seconds: int) -> str:
    """Compute worker health from heartbeat timestamp and grace period.

    Args:
        worker_state: Worker state snapshot.
        grace_seconds: Grace window in seconds.

    Returns:
        Health label in {Unknown, Healthy, Stale, Offline}.
    """
    heartbeat = worker_state.get("last_heartbeat_at")
    if not heartbeat:
        return "Unknown"

    try:
        last_seen = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
    except ValueError:
        return "Unknown"
    delta = utc_now() - last_seen
    if delta.total_seconds() <= grace_seconds:
        return "Healthy"
    if delta.total_seconds() <= grace_seconds * 3:
        return "Stale"
    return "Offline"


def evaluate_condition(task_spec: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    """Evaluate task condition against dependency and runtime state."""
    condition = task_spec.get("condition", "always")
    dependencies = task_spec.get("depends_on", [])
    dependency_statuses = [tasks[name]["status"] for name in dependencies]

    if condition == "always":
        return True, "always"
    if condition == "all_success":
        ok = all(status == "Success" for status in dependency_statuses)
        return ok, "dependencies must all succeed"
    if condition == "any_success":
        ok = any(status == "Success" for status in dependency_statuses)
        return ok, "at least one dependency must succeed"
    if condition == "all_failed":
        ok = bool(dependency_statuses) and all(status == "Failed" for status in dependency_statuses)
        return ok, "dependencies must all fail"
    if isinstance(condition, dict):
        condition_type = condition.get("type")
        if condition_type == "env_exists":
            name = condition.get("name", "")
            return bool(os.getenv(name)), f"environment variable {name} must exist"
        if condition_type == "task_status":
            task_name = condition.get("task", "")
            expected_status = condition.get("status", "")
            return tasks.get(task_name, {}).get("status") == expected_status, f"task {task_name} must be {expected_status}"

    return True, "default-allow"


def dependencies_finished(task_spec: dict[str, Any], state: dict[str, Any]) -> bool:
    """Return whether all task dependencies are in terminal statuses."""
    return all(state["tasks"][dependency]["status"] in TERMINAL_STATUSES for dependency in task_spec.get("depends_on", []))


def collect_ready_tasks(task_specs: dict[str, dict[str, Any]], state: dict[str, Any]) -> list[str]:
    """Collect schedulable task names sorted by scheduler priority."""
    ready: list[str] = []
    now = utc_now()

    for name, task_spec in task_specs.items():
        task_state = state["tasks"][name]
        if task_state["status"] not in WAITING_STATUSES:
            continue

        worker_name = task_state["worker"]
        worker_state = state["workers"].get(worker_name, {})
        if not worker_state.get("enabled", True):
            task_state["status"] = "Skipped"
            task_state["completed_at"] = iso_now()
            task_state["updated_at"] = iso_now()
            task_state["message"] = f"Worker {worker_name} is disabled"
            continue

        try:
            scheduled_at = datetime.fromisoformat(str(task_state["scheduled_at"]).replace("Z", "+00:00"))
        except ValueError:
            task_state["scheduled_at"] = iso_now()
            task_state["status"] = "Pending"
            task_state["message"] = "Recovered from invalid schedule timestamp"
            scheduled_at = now
        if scheduled_at > now:
            task_state["status"] = "Deferred"
            task_state["message"] = f"Waiting until {format_time(task_state['scheduled_at'])}"
            continue

        if not dependencies_finished(task_spec, state):
            if task_state["status"] != "Retry":
                task_state["status"] = "Pending"
            task_state["message"] = "Waiting for dependencies"
            continue

        allowed, reason = evaluate_condition(task_spec, state["tasks"])
        if not allowed:
            task_state["status"] = "Skipped"
            task_state["completed_at"] = iso_now()
            task_state["updated_at"] = iso_now()
            task_state["message"] = f"Condition not met: {reason}"
            continue

        task_state["status"] = "Pending"
        task_state["message"] = "Ready for scheduler dispatch"
        ready.append(name)

    ready.sort(key=lambda item: (-state["tasks"][item]["priority"], state["tasks"][item]["scheduled_at"], item))
    return ready


def spawn_worker_process(command: list[str]) -> tuple[subprocess.Popen[str], tempfile._TemporaryFileWrapper[str], tempfile._TemporaryFileWrapper[str]]:
    """Spawn a worker process with temporary stdout/stderr buffers."""
    stdout_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    stderr_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
        env=os.environ.copy(),
    )
    return process, stdout_file, stderr_file


def publish_event(state: dict[str, Any], event_type: str, source: str, details: str) -> None:
    """Append one runtime event to bounded in-memory event window."""
    bus = state["event_bus"]
    event_index = bus["published_events"] + 1
    event = {
        "id": f"evt-{event_index:04d}",
        "at": iso_now(),
        "type": event_type,
        "source": source,
        "details": details[:240],
    }
    bus["published_events"] = event_index
    bus["last_event_at"] = event["at"]
    bus["recent_events"].append(event)
    bus["recent_events"] = bus["recent_events"][-30:]


def refresh_scheduler_state(state: dict[str, Any], ready_queue: list[str], running: dict[str, dict[str, Any]]) -> None:
    """Synchronize scheduler and queue counters from current runtime state."""
    scheduler = state["scheduler"]
    scheduler["ready_queue"] = ready_queue
    scheduler["running_tasks"] = len(running)
    scheduler["deferred_tasks"] = sum(1 for task in state["tasks"].values() if task["status"] == "Deferred")
    scheduler["completed_tasks"] = sum(1 for task in state["tasks"].values() if task["status"] in TERMINAL_STATUSES)

    queue = state["message_queue"]
    queue["current_depth"] = len(ready_queue)
    queue["max_depth_seen"] = max(queue["max_depth_seen"], queue["current_depth"])
    queue["ready_entries"] = len(ready_queue)
    queue["deferred_entries"] = sum(1 for task in state["tasks"].values() if task["status"] == "Deferred")
    queue["retry_entries"] = sum(1 for task in state["tasks"].values() if task["status"] == "Retry")
    queue["running_leases"] = len(running)


def refresh_pool_state(state: dict[str, Any], workflow_spec: dict[str, Any], ready_queue: list[str], running: dict[str, dict[str, Any]]) -> None:
    """Recompute desired workers and queue metrics per worker pool."""
    queued_by_pool: dict[str, int] = {name: 0 for name in state["worker_pools"]}
    active_by_pool: dict[str, int] = {name: 0 for name in state["worker_pools"]}
    completed_by_pool: dict[str, int] = {name: 0 for name in state["worker_pools"]}

    for task_name in ready_queue:
        queued_by_pool[state["tasks"][task_name]["pool"]] += 1
    for info in running.values():
        active_by_pool[info["pool"]] += 1
    for task in state["tasks"].values():
        if task["status"] in TERMINAL_STATUSES:
            completed_by_pool[task["pool"]] += 1

    for pool_def in workflow_spec["worker_pools"]:
        pool_name = pool_def["name"]
        pool_state = state["worker_pools"][pool_name]
        active = active_by_pool[pool_name]
        queued = queued_by_pool[pool_name]
        denominator = max(1, pool_def["queue_target_per_worker"])
        desired = math.ceil((queued + active) / denominator) if (queued + active) else pool_def["min_workers"]
        desired = max(pool_def["min_workers"], min(pool_def["max_workers"], desired))
        pool_state["desired_workers"] = desired
        pool_state["active_workers"] = active
        pool_state["queued_tasks"] = queued
        pool_state["completed_tasks"] = completed_by_pool[pool_name]
        pool_state["last_scale_reason"] = f"queue {queued}, active {active}, target {pool_def['queue_target_per_worker']} per worker"


def mark_worker_heartbeat(
    state: dict[str, Any],
    worker_name: str,
    task_name: str,
    task_status: str | None = None,
    message: str | None = None,
) -> None:
    """Refresh worker heartbeat and optional task status/message."""
    stamp = iso_now()
    worker_state = state["workers"][worker_name]
    task_state = state["tasks"][task_name]
    worker_state["last_heartbeat_at"] = stamp
    task_state["updated_at"] = stamp
    if task_status:
        task_state["status"] = task_status
    if message:
        task_state["message"] = message


def launch_task(
    task_name: str,
    task_specs: dict[str, dict[str, Any]],
    state: dict[str, Any],
    registry_by_name: dict[str, dict[str, Any]],
    running: dict[str, dict[str, Any]],
) -> None:
    """Dispatch one ready task into worker process table."""
    task_spec = task_specs[task_name]
    worker = registry_by_name[task_spec["worker"]]
    task_state = state["tasks"][task_name]
    worker_state = state["workers"][task_spec["worker"]]
    if not worker_state.get("enabled", True):
        task_state["status"] = "Skipped"
        task_state["completed_at"] = iso_now()
        task_state["updated_at"] = iso_now()
        task_state["message"] = f"Worker {task_spec['worker']} is disabled"
        return
    attempt = task_state["attempt"] + 1

    task_state["attempt"] = attempt
    task_state["status"] = "Running"
    task_state["started_at"] = task_state["started_at"] or iso_now()
    task_state["updated_at"] = iso_now()
    task_state["message"] = f"Dispatched by scheduler on pool {task_state['pool']} (attempt {attempt}/{task_state['max_attempts']})"

    worker_state["last_started_at"] = iso_now()
    worker_state["last_error"] = None
    worker_state["last_heartbeat_at"] = iso_now()
    state["message_queue"]["total_dispatched"] += 1
    publish_event(state, "task.dispatched", task_name, f"Dispatched on pool {task_state['pool']} attempt {attempt}/{task_state['max_attempts']}")

    process, stdout_file, stderr_file = spawn_worker_process(cast(list[str], worker["command"]))

    running[task_name] = {
        "process": process,
        "stdout_file": stdout_file,
        "stderr_file": stderr_file,
        "worker": task_spec["worker"],
        "pool": task_state["pool"],
        "deadline": time.time() + worker["timeout_seconds"],
        "timed_out": False,
    }


def finalize_task_result(
    task_name: str,
    state: dict[str, Any],
    registry_by_name: dict[str, dict[str, Any]],
    running: dict[str, dict[str, Any]],
    dead_letters: list[dict[str, Any]],
) -> None:
    """Finalize one completed process and update task/worker state."""
    info = running.pop(task_name)
    process = info["process"]
    stdout_file = info["stdout_file"]
    stderr_file = info["stderr_file"]
    process.wait()
    stdout_file.seek(0)
    stderr_file.seek(0)
    stdout = stdout_file.read()
    stderr = stderr_file.read()
    stdout_file.close()
    stderr_file.close()
    worker_name = info["worker"]
    worker = registry_by_name[worker_name]
    task_state = state["tasks"][task_name]
    worker_state = state["workers"][worker_name]

    task_state["updated_at"] = iso_now()
    worker_state["last_completed_at"] = iso_now()
    worker_state["last_heartbeat_at"] = iso_now()

    if info["timed_out"]:
        worker_state["last_exit_code"] = None
        worker_state["last_failure_at"] = iso_now()
        worker_state["last_error"] = f"Timeout after {worker['timeout_seconds']}s"
        if task_state["attempt"] < task_state["max_attempts"]:
            task_state["status"] = "Retry"
            task_state["scheduled_at"] = iso_at(worker.get("retry_backoff_seconds", 5))
            task_state["message"] = worker_state["last_error"]
            publish_event(state, "task.retry", task_name, f"Timeout retry scheduled after {worker.get('retry_backoff_seconds', 5)}s")
        else:
            task_state["status"] = "Failed"
            task_state["completed_at"] = iso_now()
            task_state["message"] = worker_state["last_error"]
            publish_event(state, "task.failed", task_name, task_state["message"])
    elif process.returncode == 0:
        worker_state["last_exit_code"] = 0
        worker_state["last_success_at"] = iso_now()
        task_state["status"] = "Success"
        task_state["completed_at"] = iso_now()
        task_state["message"] = (stdout.strip() or "Worker completed successfully")[:240]
        state["message_queue"]["total_completed"] += 1
        publish_event(state, "task.succeeded", task_name, task_state["message"])
    else:
        worker_state["last_exit_code"] = process.returncode
        worker_state["last_failure_at"] = iso_now()
        failure_message = stderr.strip() or stdout.strip() or f"Exit code {process.returncode}"
        worker_state["last_error"] = failure_message[:500]
        if task_state["attempt"] < task_state["max_attempts"]:
            task_state["status"] = "Retry"
            task_state["scheduled_at"] = iso_at(worker.get("retry_backoff_seconds", 5))
            task_state["message"] = f"Retry scheduled: {failure_message[:200]}"
            publish_event(state, "task.retry", task_name, task_state["message"])
        else:
            task_state["status"] = "Failed"
            task_state["completed_at"] = iso_now()
            task_state["message"] = failure_message[:240]
            publish_event(state, "task.failed", task_name, task_state["message"])

    if task_state["status"] == "Failed":
        dead_letters.append(
            {
                "task": task_name,
                "worker": worker_name,
                "attempts": task_state["attempt"],
                "failed_at": iso_now(),
                "reason": worker_state.get("last_error") or "Unknown failure",
            }
        )


def poll_running_tasks(
    state: dict[str, Any],
    registry_by_name: dict[str, dict[str, Any]],
    running: dict[str, dict[str, Any]],
    dead_letters: list[dict[str, Any]],
) -> None:
    """Poll running tasks, handling completion and timeout paths."""
    finished: list[str] = []
    for task_name, info in running.items():
        task_state = state["tasks"][task_name]
        worker_name = info["worker"]
        worker = registry_by_name[worker_name]
        process = info["process"]

        if process.poll() is not None:
            finished.append(task_name)
            continue

        if time.time() >= info["deadline"]:
            info["timed_out"] = True
            process.kill()
            finished.append(task_name)
            continue

        mark_worker_heartbeat(
            state,
            worker_name,
            task_name,
            "Running",
            f"Heartbeat OK on pool {task_state['pool']} (attempt {task_state['attempt']}/{task_state['max_attempts']})",
        )
        state["workers"][worker_name]["health"] = compute_health(state["workers"][worker_name], worker["heartbeat_grace_seconds"])

    for task_name in finished:
        finalize_task_result(task_name, state, registry_by_name, running, dead_letters)


def mark_unreachable_tasks(task_specs: dict[str, dict[str, Any]], state: dict[str, Any]) -> None:
    """Mark tasks blocked when upstream blocked dependencies exist."""
    for name, task_spec in task_specs.items():
        task_state = state["tasks"][name]
        if task_state["status"] in TERMINAL_STATUSES or task_state["status"] == "Running":
            continue

        dependencies = [state["tasks"][dependency]["status"] for dependency in task_spec.get("depends_on", [])]
        if any(status == "Blocked" for status in dependencies):
            task_state["status"] = "Blocked"
            task_state["completed_at"] = iso_now()
            task_state["updated_at"] = iso_now()
            task_state["message"] = "Blocked by upstream task"


def workflow_finished(state: dict[str, Any]) -> bool:
    """Return whether all tasks are in terminal statuses."""
    return all(task["status"] in TERMINAL_STATUSES for task in state["tasks"].values())
