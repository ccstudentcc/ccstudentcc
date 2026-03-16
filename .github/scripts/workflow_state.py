from __future__ import annotations

"""State initialization and persistence routines for workflow controller."""

import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from workflow_common import (
    CANONICAL_FLOW_ORDER,
    DAG_PATH,
    DEAD_LETTERS_PATH,
    EVENT_LOG_PATH,
    METADATA_STORE_PATH,
    QUEUE_PATH,
    REGISTRY_PATH,
    ROOT,
    SCHEDULER_PATH,
    STATE_PATH,
    WORKFLOW_PATH,
    build_run_url,
    iso_at,
    iso_now,
    load_json,
    relative_repo_path,
    save_json,
    TERMINAL_STATUSES,
    enqueue_json,
    flush_json_writes,
)
from workflow_contract import extract_contract_metadata, worker_contracts_by_name
from workflow_renderer import render_readme as render_dashboard_readme
from workflow_runtime import compute_health


def initial_flow_order_state(existing_flow: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create runtime flow-order tracking state for the latest orchestration cycles."""
    existing_flow = existing_flow or {}
    recent_cycles = cast(list[dict[str, Any]], existing_flow.get("recent_cycles", []))[-9:]
    return {
        "expected_sequence": list(CANONICAL_FLOW_ORDER),
        "cycles_started": int(existing_flow.get("cycles_started", 0)),
        "cycles_completed": int(existing_flow.get("cycles_completed", 0)),
        "active_cycle": None,
        "latest_completed_cycle": existing_flow.get("latest_completed_cycle"),
        "recent_cycles": recent_cycles,
        "last_completed_at": existing_flow.get("last_completed_at"),
    }


def begin_flow_cycle(state: dict[str, Any], reason: str) -> None:
    """Start one tracked flow-order cycle for the current persistence pass."""
    flow = state["flow_order"]
    flow["cycles_started"] = int(flow.get("cycles_started", 0)) + 1
    flow["active_cycle"] = {
        "id": flow["cycles_started"],
        "reason": reason,
        "started_at": iso_now(),
        "expected_sequence": list(CANONICAL_FLOW_ORDER),
        "completed_sequence": [],
        "stages": [],
        "is_in_order": True,
        "is_complete": False,
    }


def record_flow_stage(state: dict[str, Any], stage_name: str, details: str) -> None:
    """Append one realized flow stage into the active cycle and check ordering."""
    flow = state["flow_order"]
    active_cycle = cast(dict[str, Any] | None, flow.get("active_cycle"))
    if not active_cycle:
        begin_flow_cycle(state, "implicit-cycle")
        active_cycle = cast(dict[str, Any], flow.get("active_cycle"))

    current_index = len(cast(list[str], active_cycle["completed_sequence"]))
    expected_stage = CANONICAL_FLOW_ORDER[current_index] if current_index < len(CANONICAL_FLOW_ORDER) else None
    matches_expected = stage_name == expected_stage
    active_cycle["is_in_order"] = bool(active_cycle.get("is_in_order", True)) and matches_expected
    active_cycle["completed_sequence"].append(stage_name)
    active_cycle["stages"].append(
        {
            "stage": stage_name,
            "at": iso_now(),
            "details": details[:240],
            "expected_stage": expected_stage,
            "matches_expected": matches_expected,
        }
    )


def complete_flow_cycle(state: dict[str, Any]) -> None:
    """Close the active flow cycle and retain it for later validation."""
    flow = state["flow_order"]
    active_cycle = cast(dict[str, Any] | None, flow.get("active_cycle"))
    if not active_cycle:
        return

    active_cycle["completed_at"] = iso_now()
    active_cycle["is_complete"] = cast(list[str], active_cycle["completed_sequence"]) == CANONICAL_FLOW_ORDER
    flow["cycles_completed"] = int(flow.get("cycles_completed", 0)) + 1
    flow["last_completed_at"] = active_cycle["completed_at"]
    flow["latest_completed_cycle"] = active_cycle
    recent_cycles = cast(list[dict[str, Any]], flow.get("recent_cycles", []))
    recent_cycles.append(active_cycle)
    flow["recent_cycles"] = recent_cycles[-10:]
    flow["active_cycle"] = None


def build_queue_entry(task_name: str, task_state: dict[str, Any], position: int | None = None) -> dict[str, Any]:
    """Build one queue entry payload from task runtime state."""
    entry = {
        "task": task_name,
        "status": task_state["status"],
        "priority": task_state["priority"],
        "pool": task_state["pool"],
        "worker": task_state["worker"],
        "scheduled_at": task_state.get("scheduled_at"),
        "updated_at": task_state.get("updated_at"),
        "attempt": task_state.get("attempt", 0),
        "max_attempts": task_state.get("max_attempts", 0),
    }
    if position is not None:
        entry["position"] = position
    return entry


def build_queue_snapshot(state: dict[str, Any], dead_letters: list[dict[str, Any]]) -> dict[str, Any]:
    """Build queue.json snapshot from scheduler and task state."""
    ready_queue = list(state["scheduler"].get("ready_queue", []))
    tasks = state["tasks"]

    deferred_queue = sorted(
        [name for name, task in tasks.items() if task["status"] == "Deferred"],
        key=lambda name: (tasks[name].get("scheduled_at") or "", name),
    )
    retry_queue = sorted(
        [name for name, task in tasks.items() if task["status"] == "Retry"],
        key=lambda name: (tasks[name].get("scheduled_at") or "", name),
    )
    running_leases = sorted(
        [name for name, task in tasks.items() if task["status"] == "Running"],
        key=lambda name: (tasks[name].get("updated_at") or "", name),
    )
    terminal_tasks = sorted(
        [name for name, task in tasks.items() if task["status"] in TERMINAL_STATUSES],
        key=lambda name: (tasks[name].get("updated_at") or "", name),
        reverse=True,
    )

    return {
        "generated_at": iso_now(),
        "backend": state["message_queue"]["backend"],
        "ordering": state["message_queue"]["ordering"],
        "ready_queue": [build_queue_entry(name, tasks[name], idx) for idx, name in enumerate(ready_queue, start=1)],
        "deferred_queue": [build_queue_entry(name, tasks[name]) for name in deferred_queue],
        "retry_queue": [build_queue_entry(name, tasks[name]) for name in retry_queue],
        "running_leases": [build_queue_entry(name, tasks[name]) for name in running_leases],
        "terminal_tasks": [build_queue_entry(name, tasks[name]) for name in terminal_tasks[:10]],
        "stats": {
            "ready": len(ready_queue),
            "deferred": len(deferred_queue),
            "retry": len(retry_queue),
            "running": len(running_leases),
            "terminal": len(terminal_tasks),
            "dead_letters": len(dead_letters),
        },
    }


def build_dag_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Build dag.json snapshot with resolved roots/leaves and node metadata."""
    tasks = state["tasks"]
    roots = sorted(name for name, task in tasks.items() if not task.get("depends_on"))
    leaves = sorted(name for name in tasks if not any(name in other_task.get("depends_on", []) for other_task in tasks.values()))
    nodes: list[dict[str, Any]] = []
    for name, task in tasks.items():
        nodes.append(
            {
                "task": name,
                "depends_on": task.get("depends_on", []),
                "condition": task.get("condition"),
                "priority": task.get("priority"),
                "pool": task.get("pool"),
                "status": task.get("status"),
            }
        )
    return {
        "generated_at": iso_now(),
        "node_count": len(nodes),
        "edge_count": sum(len(node["depends_on"]) for node in nodes),
        "roots": roots,
        "leaves": leaves,
        "nodes": nodes,
    }


def build_scheduler_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Build scheduler.json snapshot from scheduler and queue runtime counters."""
    scheduler = state["scheduler"]
    queue = state["message_queue"]
    return {
        "generated_at": iso_now(),
        "trigger": scheduler.get("trigger"),
        "cron": scheduler.get("cron"),
        "priority_policy": scheduler.get("priority_policy"),
        "delay_strategy": scheduler.get("delay_strategy"),
        "parallel_execution": scheduler.get("parallel_execution"),
        "ready_queue": scheduler.get("ready_queue", []),
        "deferred_tasks": scheduler.get("deferred_tasks", 0),
        "running_tasks": scheduler.get("running_tasks", 0),
        "completed_tasks": scheduler.get("completed_tasks", 0),
        "queue_depth": queue.get("current_depth", 0),
        "retry_entries": queue.get("retry_entries", 0),
        "running_leases": queue.get("running_leases", 0),
    }


def build_event_log_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Build event-log.json payload from in-memory event bus state."""
    bus = state["event_bus"]
    return {
        "generated_at": iso_now(),
        "backend": bus["backend"],
        "published_events": bus["published_events"],
        "last_event_at": bus.get("last_event_at"),
        "entries": bus.get("recent_events", []),
    }


def collect_store_documents(paths: dict[str, str]) -> list[dict[str, Any]]:
    """Collect document existence/size/mtime inventory for metadata manifest."""
    documents: list[dict[str, Any]] = []
    for name, relative_path in paths.items():
        document_path = ROOT / relative_path
        exists = document_path.exists()
        stat_result = document_path.stat() if exists else None
        size_bytes = stat_result.st_size if stat_result is not None else 0
        updated_at = None
        if stat_result is not None:
            updated_at = datetime.fromtimestamp(stat_result.st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        documents.append(
            {
                "name": name,
                "path": relative_path,
                "exists": exists,
                "size_bytes": size_bytes,
                "updated_at": updated_at,
            }
        )
    return documents


def refresh_state_store_inventory(state: dict[str, Any]) -> None:
    """Refresh state-store inventory and consistency fields in runtime state."""
    store = state["state_store"]
    documents = collect_store_documents(store["paths"])
    missing = [document["name"] for document in documents if not document["exists"]]
    store["documents"] = documents
    store["document_count"] = len(documents)
    store["available_documents"] = sum(1 for document in documents if document["exists"])
    store["total_size_bytes"] = sum(int(document["size_bytes"]) for document in documents if document["exists"])
    store["missing_documents"] = missing
    store["consistency_status"] = "healthy" if not missing else "degraded"


def build_metadata_store_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Build metadata-store.json payload from state-store runtime section."""
    store = state["state_store"]
    return {
        "generated_at": iso_now(),
        "backend": store["backend"],
        "transaction_model": store["transaction_model"],
        "manifest_version": store["manifest_version"],
        "write_count": store["write_count"],
        "last_persisted_at": store.get("last_persisted_at"),
        "last_write_batch": store.get("last_write_batch", []),
        "consistency_status": store.get("consistency_status", "unknown"),
        "total_size_bytes": store.get("total_size_bytes", 0),
        "documents": store.get("documents", []),
        "missing_documents": store.get("missing_documents", []),
    }


def stringify_condition(condition: object) -> str:
    """Stringify condition object for dashboard-friendly task state rendering."""
    if isinstance(condition, str):
        return condition
    if isinstance(condition, dict):
        condition_type = condition.get("type", "custom")
        if condition_type == "env_exists":
            return f"env_exists({condition.get('name', '')})"
        if condition_type == "task_status":
            return f"task_status({condition.get('task', '')}={condition.get('status', '')})"
        return condition_type
    return "always"


def replace_template(value: Any, mapping: dict[str, str]) -> Any:
    """Replace foreach template tokens inside nested data structures."""
    if isinstance(value, str):
        result = value
        for key, item in mapping.items():
            result = result.replace(f"{{{{{key}}}}}", item)
        return result
    if isinstance(value, list):
        return [replace_template(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: replace_template(item, mapping) for key, item in value.items()}
    return value


def expand_task_specs(task_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand foreach task templates into concrete task specifications."""
    expanded: list[dict[str, Any]] = []
    for task in task_specs:
        foreach = task.get("foreach")
        if not foreach:
            expanded.append(task)
            continue

        var_name = foreach.get("var", "item")
        for item in foreach.get("items", []):
            base = {key: value for key, value in task.items() if key != "foreach"}
            mapping = {var_name: str(item)}
            if isinstance(item, dict):
                for key, value in item.items():
                    mapping[f"{var_name}.{key}"] = str(value)
            expanded.append(cast(dict[str, Any], replace_template(base, mapping)))
    return expanded


def validate_dag(task_specs: list[dict[str, Any]]) -> None:
    """Validate DAG acyclic constraints and dependency references.

    Raises:
        ValueError: If duplicate task names, unknown dependencies, or cycle detected.
    """
    task_names = {task["name"] for task in task_specs}
    indegree = {task["name"]: 0 for task in task_specs}
    graph = {task["name"]: [] for task in task_specs}

    if len(task_names) != len(task_specs):
        raise ValueError("Task names must be unique")

    for task in task_specs:
        for dependency in task.get("depends_on", []):
            if dependency not in task_names:
                raise ValueError(f"Task {task['name']} depends on unknown task {dependency}")
            graph[dependency].append(task["name"])
            indegree[task["name"]] += 1

    queue = deque(name for name, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in graph[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if visited != len(task_specs):
        raise ValueError("Workflow DAG contains a cycle")


def initial_worker_state(worker: dict[str, Any]) -> dict[str, Any]:
    """Create initial worker runtime state from worker registry entry."""
    return {
        "display_name": worker["display_name"],
        "enabled": worker["enabled"],
        "managed_by_default": worker.get("managed_by_default", True),
        "worker_type": worker["worker_type"],
        "pool": worker["pool"],
        "capabilities": worker.get("capabilities", []),
        "timeout_seconds": worker["timeout_seconds"],
        "max_retries": worker["max_retries"],
        "retry_backoff_seconds": worker.get("retry_backoff_seconds", 5),
        "contract": extract_contract_metadata(worker),
        "last_heartbeat_at": None,
        "last_started_at": None,
        "last_completed_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_exit_code": None,
        "last_error": None,
        "health": "Unknown",
    }


def initial_pool_state(pool: dict[str, Any]) -> dict[str, Any]:
    """Create initial worker pool runtime state from pool definition."""
    return {
        "worker_type": pool["worker_type"],
        "min_workers": pool["min_workers"],
        "max_workers": pool["max_workers"],
        "queue_target_per_worker": pool["queue_target_per_worker"],
        "scale_metric": pool["scale_metric"],
        "capabilities": pool.get("capabilities", []),
        "desired_workers": pool["min_workers"],
        "active_workers": 0,
        "queued_tasks": 0,
        "completed_tasks": 0,
        "last_scale_reason": "Pool initialized",
    }


def initial_task_state(task: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
    """Create initial task runtime state from DAG spec and assigned worker."""
    scheduled_at = iso_at(task.get("delay_seconds", 0))
    status = "Deferred" if task.get("delay_seconds", 0) > 0 else "Pending"
    return {
        "display_name": worker["display_name"],
        "worker": task["worker"],
        "pool": task["pool"],
        "worker_type": worker["worker_type"],
        "priority": task.get("priority", 0),
        "depends_on": task.get("depends_on", []),
        "condition": stringify_condition(task.get("condition", "always")),
        "contract": extract_contract_metadata(worker),
        "status": status,
        "attempt": 0,
        "max_attempts": worker["max_retries"] + 1,
        "scheduled_at": scheduled_at,
        "started_at": None,
        "completed_at": None,
        "updated_at": iso_now(),
        "message": "Deferred by scheduler" if status == "Deferred" else "Queued by scheduler",
    }


def initialize_state(registry: dict[str, Any], workflow_spec: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Initialize runtime state from registry/spec and previous snapshots.

    Args:
        registry: Worker registry payload from registry.json.
        workflow_spec: Workflow DAG/config payload from workflow.json.

    Returns:
        A tuple of (state, dead_letters, expanded_task_specs).
    """
    existing_state = cast(dict[str, Any], load_json(STATE_PATH, {}))
    dead_letters: list[dict[str, Any]] = []
    workers_by_name = worker_contracts_by_name(registry)
    pool_defs = {pool["name"]: pool for pool in workflow_spec["worker_pools"]}
    task_specs = expand_task_specs(workflow_spec["tasks"])
    validate_dag(task_specs)

    state = {
        "workflow": {
            "name": workflow_spec["workflow"]["name"],
            "description": workflow_spec["workflow"]["description"],
            "status": "Running",
            "started_at": iso_now(),
            "completed_at": None,
            "run_id": os.getenv("GITHUB_RUN_ID"),
            "run_url": build_run_url(),
            "dag_nodes": len(task_specs),
            "dag_edges": sum(len(task.get("depends_on", [])) for task in task_specs),
        },
        "failure_policy": "continue-on-error + retry + timeout cancel + dead-letter on exhaust",
        "scheduler": {
            "trigger": os.getenv("GITHUB_EVENT_NAME", "manual"),
            "cron": workflow_spec["scheduler"]["cron"],
            "priority_policy": workflow_spec["scheduler"]["priority_policy"],
            "delay_strategy": workflow_spec["scheduler"]["delay_strategy"],
            "parallel_execution": workflow_spec["scheduler"]["parallel_execution"],
            "ready_queue": [],
            "deferred_tasks": 0,
            "running_tasks": 0,
            "completed_tasks": 0,
        },
        "message_queue": {
            "backend": workflow_spec.get("message_queue", {}).get("backend", "github-actions-internal-queue"),
            "delivery_guarantee": workflow_spec.get("message_queue", {}).get("delivery_guarantee", "at-least-once"),
            "ordering": workflow_spec.get("message_queue", {}).get("ordering", "priority-then-scheduled-at"),
            "persistence": workflow_spec.get("message_queue", {}).get("persistence", "queue-json + state-json + dead-letter-json"),
            "dead_letter_enabled": workflow_spec.get("message_queue", {}).get("dead_letter_enabled", True),
            "operations": workflow_spec.get("message_queue", {}).get("operations", workflow_spec.get("message_queue", {}).get("middleware_options", [])),
            "snapshot_path": relative_repo_path(QUEUE_PATH),
            "snapshot_updated_at": None,
            "current_depth": 0,
            "max_depth_seen": 0,
            "total_dispatched": 0,
            "total_completed": 0,
            "ready_entries": 0,
            "deferred_entries": 0,
            "retry_entries": 0,
            "running_leases": 0,
        },
        "state_store": {
            "backend": workflow_spec.get("state_store", {}).get("backend", "json-files-in-repo"),
            "ha_model": workflow_spec.get("state_store", {}).get("ha_model", "git-versioned-single-writer"),
            "latency_target": workflow_spec.get("state_store", {}).get("latency_target", "low"),
            "transaction_model": workflow_spec.get("state_store", {}).get("transaction_model", "atomic-file-write"),
            "metadata_scope": workflow_spec.get("state_store", {}).get("metadata_scope", []),
            "store_features": workflow_spec.get("state_store", {}).get("store_features", workflow_spec.get("state_store", {}).get("database_options", [])),
            "paths": {
                "workflow_spec": relative_repo_path(WORKFLOW_PATH),
                "runtime_state": relative_repo_path(STATE_PATH),
                "dag_snapshot": relative_repo_path(DAG_PATH),
                "scheduler_snapshot": relative_repo_path(SCHEDULER_PATH),
                "queue_snapshot": relative_repo_path(QUEUE_PATH),
                "event_log": relative_repo_path(EVENT_LOG_PATH),
                "dead_letters": relative_repo_path(DEAD_LETTERS_PATH),
                "metadata_manifest": relative_repo_path(METADATA_STORE_PATH),
            },
            "manifest_version": 1,
            "documents": existing_state.get("state_store", {}).get("documents", []),
            "document_count": 0,
            "available_documents": 0,
            "total_size_bytes": 0,
            "missing_documents": [],
            "write_count": int(existing_state.get("state_store", {}).get("write_count", 0)),
            "last_write_batch": [],
            "consistency_status": "unknown",
            "last_persisted_at": None,
        },
        "event_bus": {
            "backend": workflow_spec.get("event_bus", {}).get("backend", "internal-event-log"),
            "delivery_semantics": workflow_spec.get("event_bus", {}).get("delivery_semantics", "at-least-once"),
            "trigger_mode": workflow_spec.get("event_bus", {}).get("trigger_mode", "event-driven"),
            "subscribers": workflow_spec.get("event_bus", {}).get("subscribers", []),
            "integration_options": workflow_spec.get("event_bus", {}).get("integration_options", []),
            "event_log_path": relative_repo_path(EVENT_LOG_PATH),
            "published_events": 0,
            "last_event_at": None,
            "recent_events": [],
        },
        "flow_order": initial_flow_order_state(cast(dict[str, Any], existing_state.get("flow_order", {}))),
        "worker_pools": {},
        "managed_jobs": [task["name"] for task in task_specs],
        "standalone_jobs": [
            worker_name
            for worker_name, worker in sorted(workers_by_name.items())
            if not worker.get("managed_by_default", True)
        ],
        "workers": existing_state.get("workers", {}),
        "tasks": {},
    }

    for pool_name, pool_def in pool_defs.items():
        pool_state = existing_state.get("worker_pools", {}).get(pool_name, initial_pool_state(pool_def))
        pool_state.update(initial_pool_state(pool_def))
        state["worker_pools"][pool_name] = pool_state

    for worker_name, worker in workers_by_name.items():
        worker_state = state["workers"].get(worker_name, initial_worker_state(worker))
        worker_state.update(initial_worker_state(worker))
        worker_state["health"] = compute_health(worker_state, worker["heartbeat_grace_seconds"])
        state["workers"][worker_name] = worker_state

    for task in task_specs:
        worker = workers_by_name[task["worker"]]
        if task["pool"] not in pool_defs:
            raise ValueError(f"Task {task['name']} references unknown pool {task['pool']}")
        state["tasks"][task["name"]] = initial_task_state(task, worker)

    return state, dead_letters, task_specs


def build_persist_signature(state: dict[str, Any], dead_letters: list[dict[str, Any]]) -> str:
    """Build a stable signature for meaningful persistence-visible state."""

    def normalize_task(task: dict[str, Any]) -> dict[str, Any]:
        message = str(task.get("message", ""))
        status = task.get("status")
        if status == "Running" and message.startswith("Heartbeat OK on pool "):
            message = "heartbeat"

        return {
            "status": status,
            "attempt": task.get("attempt"),
            "max_attempts": task.get("max_attempts"),
            "priority": task.get("priority"),
            "pool": task.get("pool"),
            "worker": task.get("worker"),
            "depends_on": task.get("depends_on", []),
            "condition": task.get("condition"),
            "scheduled_at": task.get("scheduled_at"),
            "completed_at": task.get("completed_at"),
            "message": message,
        }

    def normalize_worker(worker: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": worker.get("enabled"),
            "managed_by_default": worker.get("managed_by_default", True),
            "health": worker.get("health"),
            "worker_type": worker.get("worker_type"),
            "pool": worker.get("pool"),
            "display_name": worker.get("display_name"),
            "capabilities": worker.get("capabilities", []),
            "last_exit_code": worker.get("last_exit_code"),
            "last_error": worker.get("last_error"),
            "last_success_at": worker.get("last_success_at"),
            "last_failure_at": worker.get("last_failure_at"),
        }

    def normalize_pool(pool: dict[str, Any]) -> dict[str, Any]:
        return {
            "desired_workers": pool.get("desired_workers"),
            "active_workers": pool.get("active_workers"),
            "queued_tasks": pool.get("queued_tasks"),
            "completed_tasks": pool.get("completed_tasks"),
            "last_scale_reason": pool.get("last_scale_reason"),
        }

    recent_events = cast(list[dict[str, Any]], state.get("event_bus", {}).get("recent_events", []))
    normalized = {
        "workflow": {
            "status": state.get("workflow", {}).get("status"),
            "dag_nodes": state.get("workflow", {}).get("dag_nodes"),
            "dag_edges": state.get("workflow", {}).get("dag_edges"),
        },
        "managed_jobs": state.get("managed_jobs", []),
        "standalone_jobs": state.get("standalone_jobs", []),
        "scheduler": {
            "ready_queue": state.get("scheduler", {}).get("ready_queue", []),
            "deferred_tasks": state.get("scheduler", {}).get("deferred_tasks"),
            "running_tasks": state.get("scheduler", {}).get("running_tasks"),
            "completed_tasks": state.get("scheduler", {}).get("completed_tasks"),
        },
        "message_queue": {
            "current_depth": state.get("message_queue", {}).get("current_depth"),
            "total_dispatched": state.get("message_queue", {}).get("total_dispatched"),
            "total_completed": state.get("message_queue", {}).get("total_completed"),
            "retry_entries": state.get("message_queue", {}).get("retry_entries"),
            "running_leases": state.get("message_queue", {}).get("running_leases"),
        },
        "event_bus": {
            "published_events": state.get("event_bus", {}).get("published_events"),
            "recent_events": [
                {
                    "type": event.get("type"),
                    "source": event.get("source"),
                    "details": event.get("details"),
                }
                for event in recent_events
            ],
        },
        "worker_pools": {
            name: normalize_pool(pool)
            for name, pool in sorted(cast(dict[str, dict[str, Any]], state.get("worker_pools", {})).items())
        },
        "workers": {
            name: normalize_worker(worker)
            for name, worker in sorted(cast(dict[str, dict[str, Any]], state.get("workers", {})).items())
        },
        "tasks": {
            name: normalize_task(task)
            for name, task in sorted(cast(dict[str, dict[str, Any]], state.get("tasks", {})).items())
        },
        "dead_letters": [
            {
                "task": item.get("task"),
                "worker": item.get("worker"),
                "attempts": item.get("attempts"),
                "reason": item.get("reason"),
            }
            for item in dead_letters
        ],
    }
    return json.dumps(normalized, sort_keys=True, ensure_ascii=True)


def prepare_state_store_batch(state: dict[str, Any]) -> None:
    """Increment write counter and record next write batch paths."""
    state["state_store"]["write_count"] = int(state["state_store"].get("write_count", 0)) + 1
    state["state_store"]["last_persisted_at"] = iso_now()
    state["state_store"]["last_write_batch"] = [
        relative_repo_path(STATE_PATH),
        relative_repo_path(DAG_PATH),
        relative_repo_path(SCHEDULER_PATH),
        relative_repo_path(QUEUE_PATH),
        relative_repo_path(METADATA_STORE_PATH),
        relative_repo_path(EVENT_LOG_PATH),
        relative_repo_path(DEAD_LETTERS_PATH),
    ]


def persist_runtime_artifacts(state: dict[str, Any], dead_letters: list[dict[str, Any]]) -> None:
    """Persist queue/dag/scheduler/event/dead-letter runtime artifacts."""
    queue_snapshot = build_queue_snapshot(state, dead_letters)
    state["message_queue"]["snapshot_updated_at"] = queue_snapshot["generated_at"]
    state["message_queue"]["ready_entries"] = queue_snapshot["stats"]["ready"]
    state["message_queue"]["deferred_entries"] = queue_snapshot["stats"]["deferred"]
    state["message_queue"]["retry_entries"] = queue_snapshot["stats"]["retry"]
    state["message_queue"]["running_leases"] = queue_snapshot["stats"]["running"]

    enqueue_json(DAG_PATH, build_dag_snapshot(state))
    enqueue_json(SCHEDULER_PATH, build_scheduler_snapshot(state))
    enqueue_json(QUEUE_PATH, queue_snapshot)


def persist_state_and_manifest(state: dict[str, Any]) -> None:
    """Persist state.json and metadata-store.json with refreshed inventory."""
    refresh_state_store_inventory(state)
    enqueue_json(STATE_PATH, state)

    refresh_state_store_inventory(state)
    enqueue_json(METADATA_STORE_PATH, build_metadata_store_payload(state))


def persist(
    state: dict[str, Any],
    dead_letters: list[dict[str, Any]],
    registry: dict[str, Any],
    previous_signature: str | None = None,
    force: bool = False,
) -> str:
    """Persist all workflow artifacts and re-render README dashboard when needed."""
    refresh_worker_health(state, registry)
    signature = build_persist_signature(state, dead_letters)
    # Avoid unnecessary writes when there is no meaningful change.
    # If signatures match, do not enqueue a new write batch. If `force` is
    # requested, only attempt to flush any pre-existing queued writes but
    # do not persist identical state to disk (prevents redundant commits).
    if previous_signature == signature:
        if force:
            try:
                flush_json_writes(force=True)
            except Exception:
                pass
        return signature

    begin_flow_cycle(state, "persist")
    record_flow_stage(state, "Orchestrator", f"workflow={state['workflow']['name']} status={state['workflow']['status']}")
    record_flow_stage(state, "DAG", f"nodes={state['workflow']['dag_nodes']} edges={state['workflow']['dag_edges']}")
    record_flow_stage(state, "Scheduler", f"ready={len(state['scheduler'].get('ready_queue', []))} running={state['scheduler'].get('running_tasks', 0)}")
    record_flow_stage(state, "Queue", f"depth={state['message_queue'].get('current_depth', 0)} dispatched={state['message_queue'].get('total_dispatched', 0)}")
    prepare_state_store_batch(state)
    record_flow_stage(state, "State Store", f"write={state['state_store'].get('write_count', 0)} batch={len(state['state_store'].get('last_write_batch', []))}")
    persist_runtime_artifacts(state, dead_letters)
    record_flow_stage(state, "Event Bus", f"published={state['event_bus'].get('published_events', 0)}")
    record_flow_stage(state, "Worker Pools", f"pools={len(state['worker_pools'])}")
    record_flow_stage(state, "Registry", f"workers={len(state['workers'])}")
    healthy_workers = sum(1 for worker in state['workers'].values() if worker.get('health') == 'Healthy')
    record_flow_stage(state, "Health", f"healthy={healthy_workers}/{len(state['workers'])}")
    terminal_tasks = sum(1 for task in state['tasks'].values() if task['status'] in TERMINAL_STATUSES)
    record_flow_stage(state, "Tasks", f"terminal={terminal_tasks}/{len(state['tasks'])}")
    record_flow_stage(state, "DLQ", f"dead_letters={len(dead_letters)}")
    complete_flow_cycle(state)
    persist_state_and_manifest(state)
    enqueue_json(EVENT_LOG_PATH, build_event_log_payload(state))
    enqueue_json(DEAD_LETTERS_PATH, dead_letters[-20:])
    render_dashboard_readme(state, dead_letters[-10:])
    # Attempt a best-effort flush respecting debounce; when batching disabled this is immediate.
    try:
        flush_json_writes(force=force)
    except Exception:
        # never raise from persist
        pass
    return build_persist_signature(state, dead_letters)


def write_step_summary(state: dict[str, Any], dead_letters: list[dict[str, Any]]) -> None:
    """Write compact run summary into GitHub Actions step summary file."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    succeeded = [name for name, task in state["tasks"].items() if task["status"] == "Success"]
    failed = [name for name, task in state["tasks"].items() if task["status"] == "Failed"]
    skipped = [name for name, task in state["tasks"].items() if task["status"] == "Skipped"]
    blocked = [name for name, task in state["tasks"].items() if task["status"] == "Blocked"]

    lines = [
        "## Workflow Manager Summary",
        "",
        f"- Workflow: {state['workflow']['name']}",
        f"- Status: {state['workflow']['status']}",
        f"- Trigger: {state['scheduler']['trigger']}",
        f"- Started: {state['workflow']['started_at']}",
        f"- Completed: {state['workflow'].get('completed_at', 'n/a')}",
        "",
        f"| Task | Status | Pool | Attempt |",
        f"|------|--------|------|---------|"]
    for name, task in state["tasks"].items():
        lines.append(f"| {name} | {task['status']} | {task['pool']} | {task['attempt']}/{task['max_attempts']} |")

    lines.extend([
        "",
        f"Succeeded: {len(succeeded)} | Failed: {len(failed)} | Skipped: {len(skipped)} | Blocked: {len(blocked)}",
    ])

    if dead_letters:
        lines.extend(["", "### Dead Letters", ""])
        for item in dead_letters:
            lines.append(f"- `{item['task']}`: {item['reason'][:120]}")

    Path(summary_path).write_text("\n".join(lines), encoding="utf-8")


def log_run_summary(state: dict[str, Any], dead_letters: list[dict[str, Any]]) -> None:
    """Print compact console run summary."""
    succeeded = sum(1 for task in state["tasks"].values() if task["status"] == "Success")
    failed = sum(1 for task in state["tasks"].values() if task["status"] == "Failed")
    skipped = sum(1 for task in state["tasks"].values() if task["status"] == "Skipped")
    blocked = sum(1 for task in state["tasks"].values() if task["status"] == "Blocked")
    total = len(state["tasks"])
    print(f"Run summary: {succeeded}/{total} succeeded, {failed} failed, {skipped} skipped, {blocked} blocked")
    for name, task in state["tasks"].items():
        print(f"  {name}: {task['status']} [{task['pool']}] attempt={task['attempt']}/{task['max_attempts']} | {task.get('message', '')[:120]}")
    if dead_letters:
        print(f"Dead letters ({len(dead_letters)}):")
        for item in dead_letters:
            print(f"  {item['task']}: {str(item.get('reason', ''))[:120]}")


def refresh_worker_health(state: dict[str, Any], registry: dict[str, Any]) -> None:
    """Refresh all worker health fields before persistence."""
    registry_by_name = worker_contracts_by_name(registry)
    for worker_name, worker_state in state["workers"].items():
        worker = registry_by_name.get(worker_name)
        if worker:
            worker_state["health"] = compute_health(worker_state, worker["heartbeat_grace_seconds"])
