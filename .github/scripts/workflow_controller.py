from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from readme_utils import update_readme_section


ROOT = Path(__file__).resolve().parents[2]
README_PATH = ROOT / "README.md"
REGISTRY_PATH = ROOT / ".github/manager/registry.json"
WORKFLOW_PATH = ROOT / ".github/manager/workflow.json"
STATE_PATH = ROOT / ".github/manager/state/state.json"
DEAD_LETTERS_PATH = ROOT / ".github/manager/state/dead-letters.json"
POLL_SECONDS = 2
ASIA_SHANGHAI = timezone(timedelta(hours=8))
TERMINAL_STATUSES = {"Success", "Failed", "Skipped", "Blocked"}
WAITING_STATUSES = {"Pending", "Deferred", "Retry"}
AUTOMATION_MARKERS = {
    "automation_status": ("<!--START_SECTION:automation_status-->", "<!--END_SECTION:automation_status-->"),
    "workflow_dag": ("<!--START_SECTION:workflow_dag-->", "<!--END_SECTION:workflow_dag-->"),
    "scheduler_state": ("<!--START_SECTION:scheduler_state-->", "<!--END_SECTION:scheduler_state-->"),
    "message_queue": ("<!--START_SECTION:message_queue-->", "<!--END_SECTION:message_queue-->"),
    "state_store": ("<!--START_SECTION:state_store-->", "<!--END_SECTION:state_store-->"),
    "event_bus": ("<!--START_SECTION:event_bus-->", "<!--END_SECTION:event_bus-->"),
    "worker_pools": ("<!--START_SECTION:worker_pools-->", "<!--END_SECTION:worker_pools-->"),
    "worker_registry": ("<!--START_SECTION:worker_registry-->", "<!--END_SECTION:worker_registry-->"),
    "worker_health": ("<!--START_SECTION:worker_health-->", "<!--END_SECTION:worker_health-->"),
    "task_state": ("<!--START_SECTION:task_state-->", "<!--END_SECTION:task_state-->"),
    "dead_letters": ("<!--START_SECTION:dead_letters-->", "<!--END_SECTION:dead_letters-->")
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_at(offset_seconds: int) -> str:
    return (utc_now() + timedelta(seconds=offset_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_time(iso_value: str | None) -> str:
    if not iso_value:
        return "n/a"
    normalized = iso_value.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(normalized).astimezone(ASIA_SHANGHAI)
    return stamp.strftime("%Y-%m-%d %H:%M CST")


def build_run_url() -> str | None:
    server = os.getenv("GITHUB_SERVER_URL")
    repository = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if server and repository and run_id:
        return f"{server}/{repository}/actions/runs/{run_id}"
    return None


def render_badge(label: str, message: str, color: str, logo: str | None = None) -> str:
    label_q = quote(label, safe="")
    message_q = quote(message, safe="")
    logo_part = f"&logo={quote(logo, safe='')}" if logo else ""
    return (
        f'<img src="https://img.shields.io/badge/{label_q}-{message_q}-{color}?style=for-the-badge{logo_part}" '
        f'alt="{label}: {message}" />'
    )


def render_pill(label: str, message: str, color: str) -> str:
    label_q = quote(label, safe="")
    message_q = quote(message, safe="")
    return (
        f'<img src="https://img.shields.io/badge/{label_q}-{message_q}-{color}?style=flat-square" '
        f'alt="{label}: {message}" />'
    )


def render_card(title: str, pills: list[str], body: str) -> str:
    pills_block = " ".join(pills)
    return "\n".join([
        "<details>",
        f"<summary><b><code>{escape(title)}</code></b> {pills_block}</summary>",
        "",
        f"<sub>{body}</sub>",
        "</details>"
    ])


def stringify_condition(condition: object) -> str:
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


def validate_dag(task_specs: list[dict]) -> None:
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


def compute_health(worker_state: dict, grace_seconds: int) -> str:
    heartbeat = worker_state.get("last_heartbeat_at")
    if not heartbeat:
        return "Unknown"

    last_seen = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
    delta = utc_now() - last_seen
    if delta.total_seconds() <= grace_seconds:
        return "Healthy"
    if delta.total_seconds() <= grace_seconds * 3:
        return "Stale"
    return "Offline"


def initial_worker_state(worker: dict) -> dict:
    return {
        "display_name": worker["display_name"],
        "enabled": worker["enabled"],
        "worker_type": worker["worker_type"],
        "pool": worker["pool"],
        "capabilities": worker.get("capabilities", []),
        "timeout_seconds": worker["timeout_seconds"],
        "max_retries": worker["max_retries"],
        "retry_backoff_seconds": worker.get("retry_backoff_seconds", 5),
        "last_heartbeat_at": None,
        "last_started_at": None,
        "last_completed_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_exit_code": None,
        "last_error": None,
        "health": "Unknown"
    }


def initial_pool_state(pool: dict) -> dict:
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
        "last_scale_reason": "Pool initialized"
    }


def initial_task_state(task: dict, worker: dict) -> dict:
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
        "status": status,
        "attempt": 0,
        "max_attempts": worker["max_retries"] + 1,
        "scheduled_at": scheduled_at,
        "started_at": None,
        "completed_at": None,
        "updated_at": iso_now(),
        "message": "Deferred by scheduler" if status == "Deferred" else "Queued by scheduler"
    }


def evaluate_condition(task_spec: dict, tasks: dict[str, dict]) -> tuple[bool, str]:
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


def initialize_state(registry: dict[str, Any], workflow_spec: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    existing_state = cast(dict[str, Any], load_json(STATE_PATH, {}))
    dead_letters = cast(list[dict[str, Any]], load_json(DEAD_LETTERS_PATH, []))
    workers_by_name = {worker["name"]: worker for worker in registry["workers"]}
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
            "dag_edges": sum(len(task.get("depends_on", [])) for task in task_specs)
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
            "completed_tasks": 0
        },
        "message_queue": {
            "backend": workflow_spec.get("message_queue", {}).get("backend", "github-actions-internal-queue"),
            "delivery_guarantee": workflow_spec.get("message_queue", {}).get("delivery_guarantee", "at-least-once"),
            "ordering": workflow_spec.get("message_queue", {}).get("ordering", "priority-then-scheduled-at"),
            "persistence": workflow_spec.get("message_queue", {}).get("persistence", "state-json-and-dead-letter-json"),
            "dead_letter_enabled": workflow_spec.get("message_queue", {}).get("dead_letter_enabled", True),
            "middleware_options": workflow_spec.get("message_queue", {}).get("middleware_options", []),
            "current_depth": 0,
            "max_depth_seen": 0,
            "total_dispatched": 0,
            "total_completed": 0
        },
        "state_store": {
            "backend": workflow_spec.get("state_store", {}).get("backend", "json-files-in-repo"),
            "ha_model": workflow_spec.get("state_store", {}).get("ha_model", "git-versioned-single-writer"),
            "latency_target": workflow_spec.get("state_store", {}).get("latency_target", "low"),
            "transaction_model": workflow_spec.get("state_store", {}).get("transaction_model", "atomic-file-write"),
            "metadata_scope": workflow_spec.get("state_store", {}).get("metadata_scope", []),
            "database_options": workflow_spec.get("state_store", {}).get("database_options", []),
            "paths": {
                "workflow_spec": str(WORKFLOW_PATH.relative_to(ROOT)),
                "runtime_state": str(STATE_PATH.relative_to(ROOT)),
                "dead_letters": str(DEAD_LETTERS_PATH.relative_to(ROOT))
            },
            "last_persisted_at": None
        },
        "event_bus": {
            "backend": workflow_spec.get("event_bus", {}).get("backend", "internal-event-log"),
            "delivery_semantics": workflow_spec.get("event_bus", {}).get("delivery_semantics", "at-least-once"),
            "trigger_mode": workflow_spec.get("event_bus", {}).get("trigger_mode", "event-driven"),
            "subscribers": workflow_spec.get("event_bus", {}).get("subscribers", []),
            "integration_options": workflow_spec.get("event_bus", {}).get("integration_options", []),
            "published_events": 0,
            "last_event_at": None,
            "recent_events": []
        },
        "worker_pools": {},
        "managed_jobs": [task["name"] for task in task_specs],
        "workers": existing_state.get("workers", {}),
        "tasks": {}
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


def persist(state: dict, dead_letters: list[dict], registry: dict) -> None:
    for worker in registry["workers"]:
        worker_state = state["workers"][worker["name"]]
        worker_state["health"] = compute_health(worker_state, worker["heartbeat_grace_seconds"])

    state["state_store"]["last_persisted_at"] = iso_now()

    save_json(STATE_PATH, state)
    save_json(DEAD_LETTERS_PATH, dead_letters[-20:])
    render_readme(state, dead_letters[-10:])


def render_automation_status(state: dict) -> str:
    workflow = state["workflow"]
    scheduler = state["scheduler"]
    status = workflow.get("status", "Unknown")
    status_color = {
        "Completed": "16a34a",
        "Running": "0284c7",
        "Failed": "dc2626",
        "Blocked": "d97706"
    }.get(status, "475569")

    lines = [
        '<div align="center">',
        render_badge("Workflow", str(status), status_color, "githubactions"),
        render_badge("Trigger", str(scheduler["trigger"]), "2563eb"),
        render_badge("Cron", str(scheduler["cron"]), "0f766e"),
        "</div>",
        "",
        f"- **Last automation update:** {format_time(workflow.get('completed_at') or workflow.get('started_at'))}",
        "- **Timezone:** Asia/Shanghai (UTC+8)",
        f"- **Orchestrator:** {workflow['name']} (DAG nodes {workflow['dag_nodes']}, edges {workflow['dag_edges']})",
        f"- **Scheduler:** trigger `{scheduler['trigger']}` | cron `{scheduler['cron']}` | policy `{scheduler['priority_policy']}`",
        "- **Worker pool model:** logical worker pools inside a single GitHub Actions run",
        f"- **Managed jobs:** {', '.join(state['managed_jobs']) or 'none'}",
        f"- **Failure policy:** {state['failure_policy']}"
    ]
    if workflow.get("run_url"):
        lines.append(f"- **Run URL:** [Open latest run]({workflow['run_url']})")
    return "\n".join(lines)


def render_workflow_dag(state: dict) -> str:
    lines = ['<div align="left">']
    for name, task in state["tasks"].items():
        dependencies = ", ".join(task["depends_on"]) if task["depends_on"] else "root"
        pills = [
            render_pill("pool", str(task["pool"]), "0f766e"),
            render_pill("priority", str(task["priority"]), "2563eb")
        ]
        body = (
            f"depends on: <code>{escape(dependencies)}</code> | "
            f"condition: <code>{escape(str(task['condition']))}</code>"
        )
        lines.append(
            render_card(name, pills, body)
        )
    lines.append("</div>")
    return "\n".join(lines) if state["tasks"] else "- No DAG tasks defined."


def render_scheduler_state(state: dict) -> str:
    scheduler = state["scheduler"]
    ready_queue = ", ".join(scheduler["ready_queue"]) if scheduler["ready_queue"] else "empty"
    running_color = "0284c7" if scheduler["running_tasks"] > 0 else "64748b"
    completed_color = "16a34a" if scheduler["completed_tasks"] > 0 else "64748b"
    lines = [
        '<div align="left">',
        "<p>",
        render_badge("Trigger", str(scheduler["trigger"]), "2563eb"),
        render_badge("Cron", str(scheduler["cron"]), "0f766e"),
        render_badge("Running", str(scheduler["running_tasks"]), running_color),
        render_badge("Completed", str(scheduler["completed_tasks"]), completed_color),
        "</p>",
        render_card(
            "queue-and-policy",
            [
                render_pill("deferred", str(scheduler["deferred_tasks"]), "64748b"),
                render_pill("parallel", str(scheduler["parallel_execution"]), "334155")
            ],
            (
                f"ready queue: <code>{escape(ready_queue)}</code> | "
                f"delay strategy: <code>{escape(str(scheduler['delay_strategy']))}</code>"
            )
        ),
        "</div>"
    ]
    return "\n".join(lines)


def render_message_queue(state: dict) -> str:
    queue = state["message_queue"]
    depth_color = "d97706" if queue["current_depth"] > 0 else "64748b"
    lines = [
        '<div align="left">',
        "<p>",
        render_badge("QueueBackend", str(queue["backend"]), "2563eb"),
        render_badge("Delivery", str(queue["delivery_guarantee"]), "0f766e"),
        render_badge("Ordering", str(queue["ordering"]), "334155"),
        "</p>",
        render_card(
            "queue-runtime",
            [
                render_pill("depth", str(queue["current_depth"]), depth_color),
                render_pill("max-depth", str(queue["max_depth_seen"]), "b45309"),
                render_pill("dispatched", str(queue["total_dispatched"]), "0284c7"),
                render_pill("completed", str(queue["total_completed"]), "16a34a")
            ],
            (
                f"persistence: <code>{escape(str(queue['persistence']))}</code> | "
                f"dead-letter enabled: <code>{escape(str(queue['dead_letter_enabled']))}</code>"
            )
        ),
        render_card(
            "middleware-options",
            [render_pill("count", str(len(queue.get("middleware_options", []))), "334155")],
            f"{escape(', '.join(queue.get('middleware_options', [])) or 'none')}"
        ),
        "</div>"
    ]
    return "\n".join(lines)


def render_state_store(state: dict) -> str:
    store = state["state_store"]
    lines = [
        '<div align="left">',
        "<p>",
        render_badge("Store", str(store["backend"]), "2563eb"),
        render_badge("HA", str(store["ha_model"]), "0f766e"),
        render_badge("Tx", str(store["transaction_model"]), "334155"),
        "</p>",
        render_card(
            "metadata-scope",
            [render_pill("items", str(len(store.get("metadata_scope", []))), "334155")],
            escape(", ".join(store.get("metadata_scope", [])) or "none")
        ),
        render_card(
            "storage-paths",
            [render_pill("latency", str(store.get("latency_target", "n/a")), "0284c7")],
            (
                f"workflow: <code>{escape(str(store['paths']['workflow_spec']))}</code> | "
                f"state: <code>{escape(str(store['paths']['runtime_state']))}</code> | "
                f"dead letters: <code>{escape(str(store['paths']['dead_letters']))}</code>"
            )
        ),
        render_card(
            "database-options",
            [render_pill("count", str(len(store.get("database_options", []))), "334155")],
            escape(", ".join(store.get("database_options", [])) or "none")
        ),
        f"<sub>last persisted: {escape(format_time(store.get('last_persisted_at')))}</sub>",
        "</div>"
    ]
    return "\n".join(lines)


def render_event_bus(state: dict) -> str:
    bus = state["event_bus"]
    lines = [
        '<div align="left">',
        "<p>",
        render_badge("EventBus", str(bus["backend"]), "2563eb"),
        render_badge("Semantics", str(bus["delivery_semantics"]), "0f766e"),
        render_badge("Published", str(bus["published_events"]), "0284c7"),
        "</p>",
        render_card(
            "trigger-and-subscribers",
            [render_pill("mode", str(bus.get("trigger_mode", "n/a")), "334155")],
            (
                f"subscribers: <code>{escape(', '.join(bus.get('subscribers', [])) or 'none')}</code> | "
                f"last event: <code>{escape(format_time(bus.get('last_event_at')))}</code>"
            )
        ),
        render_card(
            "integration-options",
            [render_pill("count", str(len(bus.get("integration_options", []))), "334155")],
            escape(", ".join(bus.get("integration_options", [])) or "none")
        )
    ]

    recent = bus.get("recent_events", [])
    if recent:
        lines.append("<sub>recent events:</sub>")
        for item in reversed(recent[-5:]):
            lines.append(
                f"<sub>- {escape(str(item['at']))} | {escape(str(item['type']))} | {escape(str(item['source']))}</sub>"
            )
    else:
        lines.append("<sub>No events published.</sub>")

    lines.append("</div>")
    return "\n".join(lines)


def render_worker_pools(state: dict) -> str:
    lines = ['<div align="left">']
    for name, pool in state["worker_pools"].items():
        queued_color = "d97706" if pool["queued_tasks"] > 0 else "64748b"
        desired_active = f"{pool['desired_workers']}/{pool['active_workers']}"
        pills = [
            render_pill("type", str(pool["worker_type"]), "334155"),
            render_pill("desired/active", desired_active, "2563eb"),
            render_pill("queued", str(pool["queued_tasks"]), queued_color),
            render_pill("completed", str(pool["completed_tasks"]), "16a34a")
        ]
        body = (
            f"max workers: {pool['max_workers']} | "
            f"scale: {escape(str(pool['last_scale_reason']))}"
        )
        lines.append(
            render_card(name, pills, body)
        )
    lines.append("</div>")
    return "\n".join(lines) if state["worker_pools"] else "- No worker pools configured."


def write_step_summary(state: dict, dead_letters: list[dict]) -> None:
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
        f"- Succeeded: {len(succeeded)}",
        f"- Failed: {len(failed)}",
        f"- Skipped: {len(skipped)}",
        f"- Blocked: {len(blocked)}",
        ""
    ]

    if failed:
        lines.append("### Failed Tasks")
        lines.append("")
        for name in failed:
            task = state["tasks"][name]
            lines.append(f"- {name}: {task['message']}")
        lines.append("")

    if dead_letters:
        lines.append("### Dead Letters")
        lines.append("")
        for item in dead_letters[-5:]:
            lines.append(f"- {item['task']}: {item['reason']}")
        lines.append("")

    Path(summary_path).write_text("\n".join(lines), encoding="utf-8")


def log_run_summary(state: dict, dead_letters: list[dict]) -> None:
    failed = [name for name, task in state["tasks"].items() if task["status"] == "Failed"]
    blocked = [name for name, task in state["tasks"].items() if task["status"] == "Blocked"]
    succeeded = [name for name, task in state["tasks"].items() if task["status"] == "Success"]

    print("Workflow summary:")
    print(f"  succeeded={len(succeeded)} failed={len(failed)} blocked={len(blocked)} dead_letters={len(dead_letters)}")

    for name in failed:
        print(f"  FAILED {name}: {state['tasks'][name]['message']}")
    for name in blocked:
        print(f"  BLOCKED {name}: {state['tasks'][name]['message']}")

    if failed:
        print(f"::warning::{len(failed)} task(s) failed. See Step Summary for details.")


def render_worker_registry(state: dict) -> str:
    lines = ['<div align="left">']
    for name, worker in state["workers"].items():
        enabled = "enabled" if worker.get("enabled") else "disabled"
        enabled_color = "16a34a" if worker.get("enabled") else "dc2626"
        capabilities = ", ".join(worker.get("capabilities", [])) or "none"
        pills = [
            render_pill("state", enabled, enabled_color),
            render_pill("type", str(worker["worker_type"]), "334155"),
            render_pill("pool", str(worker["pool"]), "0f766e")
        ]
        body = f"display: {escape(str(worker['display_name']))} | capabilities: {escape(capabilities)}"
        lines.append(
            render_card(name, pills, body)
        )
    lines.append("</div>")
    return "\n".join(lines) if state["workers"] else "- No workers registered."


def render_worker_health(state: dict) -> str:
    lines = ['<div align="left">']
    for name, worker in state["workers"].items():
        health = str(worker["health"])
        health_color = {"Healthy": "16a34a", "Stale": "d97706", "Offline": "dc2626"}.get(health, "64748b")
        pills = [render_pill("health", health, health_color)]
        body = (
            f"heartbeat: {escape(format_time(worker.get('last_heartbeat_at')))} | "
            f"last success: {escape(format_time(worker.get('last_success_at')))}"
        )
        lines.append(
            render_card(name, pills, body)
        )
    lines.append("</div>")
    return "\n".join(lines) if state["workers"] else "- No worker health data."


def render_task_state(state: dict) -> str:
    lines = ['<div align="left">']
    for name, task in state["tasks"].items():
        status = str(task["status"])
        status_color = {
            "Success": "16a34a",
            "Running": "0284c7",
            "Retry": "d97706",
            "Failed": "dc2626",
            "Skipped": "64748b",
            "Blocked": "b45309",
            "Pending": "2563eb",
            "Deferred": "475569"
        }.get(status, "64748b")
        attempt_ratio = f"{task['attempt']}/{task['max_attempts']}"
        pills = [
            render_pill("status", status, status_color),
            render_pill("priority", str(task["priority"]), "2563eb"),
            render_pill("attempt", attempt_ratio, "334155"),
            render_pill("pool", str(task["pool"]), "0f766e")
        ]
        body = (
            f"updated: {escape(format_time(task.get('updated_at')))} | "
            f"{escape(str(task['message']))}"
        )
        lines.append(
            render_card(name, pills, body)
        )
    lines.append("</div>")
    return "\n".join(lines) if state["tasks"] else "- No tasks tracked."


def render_dead_letters(dead_letters: list[dict]) -> str:
    if not dead_letters:
        return "<div align=\"left\"><sub>No dead letters.</sub></div>"

    lines = ['<div align="left">']
    for item in reversed(dead_letters[-5:]):
        pills = [
            render_pill("attempts", str(item["attempts"]), "b45309"),
            render_pill("failed", "yes", "dc2626")
        ]
        body = (
            f"at: {escape(format_time(item['failed_at']))} | "
            f"reason: {escape(str(item['reason']))}"
        )
        lines.append(
            render_card(str(item["task"]), pills, body)
        )
    lines.append("</div>")
    return "\n".join(lines)


def render_readme(state: dict, dead_letters: list[dict]) -> None:
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["automation_status"], render_automation_status(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["workflow_dag"], render_workflow_dag(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["scheduler_state"], render_scheduler_state(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["message_queue"], render_message_queue(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["state_store"], render_state_store(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["event_bus"], render_event_bus(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["worker_pools"], render_worker_pools(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["worker_registry"], render_worker_registry(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["worker_health"], render_worker_health(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["task_state"], render_task_state(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["dead_letters"], render_dead_letters(dead_letters))


def refresh_scheduler_state(state: dict, ready_queue: list[str], running: dict[str, dict]) -> None:
    scheduler = state["scheduler"]
    scheduler["ready_queue"] = ready_queue
    scheduler["running_tasks"] = len(running)
    scheduler["deferred_tasks"] = sum(1 for task in state["tasks"].values() if task["status"] == "Deferred")
    scheduler["completed_tasks"] = sum(1 for task in state["tasks"].values() if task["status"] in TERMINAL_STATUSES)

    queue = state["message_queue"]
    queue["current_depth"] = len(ready_queue)
    queue["max_depth_seen"] = max(queue["max_depth_seen"], queue["current_depth"])


def publish_event(state: dict, event_type: str, source: str, details: str) -> None:
    bus = state["event_bus"]
    event_index = bus["published_events"] + 1
    event = {
        "id": f"evt-{event_index:04d}",
        "at": iso_now(),
        "type": event_type,
        "source": source,
        "details": details[:240]
    }
    bus["published_events"] = event_index
    bus["last_event_at"] = event["at"]
    bus["recent_events"].append(event)
    bus["recent_events"] = bus["recent_events"][-30:]


def refresh_pool_state(state: dict, workflow_spec: dict, ready_queue: list[str], running: dict[str, dict]) -> None:
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


def mark_worker_heartbeat(state: dict, worker_name: str, task_name: str, task_status: str | None = None, message: str | None = None) -> None:
    stamp = iso_now()
    worker_state = state["workers"][worker_name]
    task_state = state["tasks"][task_name]
    worker_state["last_heartbeat_at"] = stamp
    task_state["updated_at"] = stamp
    if task_status:
        task_state["status"] = task_status
    if message:
        task_state["message"] = message


def dependencies_finished(task_spec: dict, state: dict) -> bool:
    return all(state["tasks"][dependency]["status"] in TERMINAL_STATUSES for dependency in task_spec.get("depends_on", []))


def collect_ready_tasks(task_specs: dict[str, dict], state: dict) -> list[str]:
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

        scheduled_at = datetime.fromisoformat(task_state["scheduled_at"].replace("Z", "+00:00"))
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


def launch_task(task_name: str, task_specs: dict[str, dict], state: dict, registry_by_name: dict[str, dict], running: dict[str, dict]) -> None:
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

    stdout_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    stderr_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")

    process = subprocess.Popen(
        worker["command"],
        cwd=ROOT,
        stdout=stdout_file,
        stderr=stderr_file,
        env=os.environ.copy()
    )

    running[task_name] = {
        "process": process,
        "stdout_file": stdout_file,
        "stderr_file": stderr_file,
        "worker": task_spec["worker"],
        "pool": task_state["pool"],
        "deadline": time.time() + worker["timeout_seconds"],
        "timed_out": False
    }


def finalize_task_result(task_name: str, state: dict, registry_by_name: dict[str, dict], running: dict[str, dict], dead_letters: list[dict]) -> None:
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
        dead_letters.append({
            "task": task_name,
            "worker": worker_name,
            "attempts": task_state["attempt"],
            "failed_at": iso_now(),
            "reason": worker_state.get("last_error") or "Unknown failure"
        })


def poll_running_tasks(state: dict, registry_by_name: dict[str, dict], running: dict[str, dict], dead_letters: list[dict]) -> None:
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

        mark_worker_heartbeat(state, worker_name, task_name, "Running", f"Heartbeat OK on pool {task_state['pool']} (attempt {task_state['attempt']}/{task_state['max_attempts']})")
        state["workers"][worker_name]["health"] = compute_health(state["workers"][worker_name], worker["heartbeat_grace_seconds"])

    for task_name in finished:
        finalize_task_result(task_name, state, registry_by_name, running, dead_letters)


def mark_unreachable_tasks(task_specs: dict[str, dict], state: dict) -> None:
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


def workflow_finished(state: dict) -> bool:
    return all(task["status"] in TERMINAL_STATUSES for task in state["tasks"].values())


def main() -> int:
    registry = cast(dict[str, Any], load_json(REGISTRY_PATH, {"workers": []}))
    workflow_spec = cast(dict[str, Any], load_json(WORKFLOW_PATH, {"workflow": {}, "scheduler": {}, "worker_pools": [], "tasks": []}))
    state, dead_letters, task_specs_list = initialize_state(registry, workflow_spec)
    registry_by_name = {worker["name"]: worker for worker in registry["workers"]}
    task_specs = {task["name"]: task for task in task_specs_list}
    running: dict[str, dict] = {}
    publish_event(state, "workflow.started", state["workflow"]["name"], f"Trigger={state['scheduler']['trigger']}")

    persist(state, dead_letters, registry)

    while True:
        ready_queue = collect_ready_tasks(task_specs, state)
        refresh_pool_state(state, workflow_spec, ready_queue, running)
        refresh_scheduler_state(state, ready_queue, running)

        for task_name in ready_queue:
            if task_name in running:
                continue
            pool_name = state["tasks"][task_name]["pool"]
            pool_state = state["worker_pools"][pool_name]
            if pool_state["active_workers"] >= pool_state["desired_workers"]:
                continue
            launch_task(task_name, task_specs, state, registry_by_name, running)
            refresh_pool_state(state, workflow_spec, ready_queue, running)
            refresh_scheduler_state(state, ready_queue, running)

        persist(state, dead_letters, registry)

        if workflow_finished(state) and not running:
            break

        if running:
            time.sleep(POLL_SECONDS)
            poll_running_tasks(state, registry_by_name, running, dead_letters)
            mark_unreachable_tasks(task_specs, state)
            persist(state, dead_letters, registry)
            continue

        if not ready_queue:
            future_deferred = any(task["status"] == "Deferred" for task in state["tasks"].values())
            if future_deferred:
                time.sleep(1)
                continue
            mark_unreachable_tasks(task_specs, state)
            if workflow_finished(state):
                break

    state["workflow"]["status"] = "Completed"
    state["workflow"]["completed_at"] = iso_now()
    publish_event(state, "workflow.completed", state["workflow"]["name"], "All terminal task states reached")
    refresh_scheduler_state(state, [], running)
    persist(state, dead_letters, registry)
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
