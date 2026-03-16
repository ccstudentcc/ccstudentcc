from __future__ import annotations

"""README automation panel renderer.

This module owns markdown/html rendering for the automation dashboard sections
and updates marker blocks in README.md.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from readme_utils import MarkerConflictError, update_readme_section


ROOT = Path(__file__).resolve().parents[2]
README_PATH = ROOT / "README.md"
DAG_PATH = ROOT / ".github/manager/state/dag.json"
SCHEDULER_PATH = ROOT / ".github/manager/state/scheduler.json"
METADATA_STORE_PATH = ROOT / ".github/manager/state/metadata-store.json"
ASIA_SHANGHAI = timezone(timedelta(hours=8))

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


def load_json(path: Path, default: Any) -> Any:
    """Load JSON from path and return default on missing file."""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def relative_repo_path(path: Path) -> str:
    """Return POSIX-style repository-relative path string."""
    return path.relative_to(ROOT).as_posix()


def format_bytes(size_bytes: int) -> str:
    """Format byte size into human readable units."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def format_time(iso_value: str | None) -> str:
    """Convert UTC ISO string to Asia/Shanghai display text."""
    if not iso_value:
        return "n/a"
    normalized = iso_value.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(normalized).astimezone(ASIA_SHANGHAI)
    return stamp.strftime("%Y-%m-%d %H:%M CST")


def render_badge(label: str, message: str, color: str, logo: str | None = None) -> str:
    """Render shields static/v1 badge HTML."""
    label_q = quote(label, safe="")
    message_q = quote(message, safe="")
    logo_part = f"&logo={quote(logo, safe='')}" if logo else ""
    return (
        f'<img src="https://img.shields.io/static/v1?label={label_q}&message={message_q}&color={color}&style=for-the-badge{logo_part}" '
        f'alt="{label}: {message}" />'
    )


def render_pill(label: str, message: str, color: str) -> str:
    """Render flat-square badge HTML for card-level metrics."""
    label_q = quote(label, safe="")
    message_q = quote(message, safe="")
    return (
        f'<img src="https://img.shields.io/static/v1?label={label_q}&message={message_q}&color={color}&style=flat-square" '
        f'alt="{label}: {message}" />'
    )


def render_card(title: str, pills: list[str], body: str) -> str:
    """Render one details/summary dashboard card."""
    pills_block = " ".join(pills)
    return "\n".join([
        "<details>",
        f"<summary><b><code>{escape(title)}</code></b> {pills_block}</summary>",
        "",
        f"<sub>{body}</sub>",
        "</details>"
    ])


TRACEBACK_HEADER = "Traceback (most recent call last):"
OPTIONAL_MARKER_TEXT = "markers were not found:"


def summarize_runtime_message(message: str) -> str:
    """Collapse noisy runtime details into concise README-safe summaries."""
    text = message.strip()
    if not text:
        return "no details"
    if OPTIONAL_MARKER_TEXT in text:
        return "Optional README marker missing; standalone worker completed without updating this section"
    if "Retry scheduled:" in text and TRACEBACK_HEADER in text:
        return "Retry scheduled after worker failure; inspect event log or workflow run for the full traceback"
    if TRACEBACK_HEADER in text:
        return "Worker failed; inspect event log or workflow run for the full traceback"
    return text


def normalize_named_items(items: list[Any]) -> list[dict[str, str]]:
    """Normalize free-form items into name/summary objects."""
    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            summary = str(item.get("summary", "")).strip()
            if name:
                normalized.append({"name": name, "summary": summary})
            continue

        text = str(item).strip()
        if text:
            normalized.append({"name": text, "summary": ""})
    return normalized


def render_named_items(items: list[Any], empty_text: str = "none") -> str:
    """Render normalized name/summary list into HTML line list."""
    normalized = normalize_named_items(items)
    if not normalized:
        return escape(empty_text)

    lines: list[str] = []
    for item in normalized:
        name = escape(item["name"])
        summary = escape(item["summary"])
        if summary:
            lines.append(f"- <code>{name}</code>: {summary}")
        else:
            lines.append(f"- <code>{name}</code>")
    return "<br/>".join(lines)


def render_document_items(documents: list[dict[str, Any]], empty_text: str = "none") -> str:
    """Render managed document inventory entries."""
    if not documents:
        return escape(empty_text)

    lines: list[str] = []
    for document in documents:
        path_text = escape(str(document.get("path", "unknown")))
        alias_text = escape(str(document.get("name", "document")))
        status_text = "present" if document.get("exists") else "missing"
        raw_size = int(document.get("size_bytes", 0))
        size_text = format_bytes(raw_size) if raw_size > 0 else "size pending refresh"
        updated_text = format_time(cast(str | None, document.get("updated_at")))
        lines.append(
            f"- <code>{alias_text}</code>: <code>{path_text}</code> | {status_text} | {size_text} | {updated_text}"
        )
    return "<br/>".join(lines)


def try_update_readme_section(start_marker: str, end_marker: str, new_block: str) -> bool:
    """Try marker update and tolerate missing marker pairs."""
    try:
        update_readme_section(README_PATH, start_marker, end_marker, new_block)
        return True
    except MarkerConflictError:
        raise
    except ValueError:
        return False


def render_automation_status(state: dict[str, Any]) -> str:
    """Render orchestrator overview section."""
    workflow = state["workflow"]
    scheduler = state["scheduler"]
    queue = state["message_queue"]
    store = state["state_store"]
    flow = cast(dict[str, Any], state.get("flow_order", {}))
    latest_cycle = cast(dict[str, Any], flow.get("latest_completed_cycle") or {})
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
        f"- **Standalone workers:** {', '.join(state.get('standalone_jobs', [])) or 'none'}",
        "- **Render policy:** meaningful state changes only | optional markers skip safely | duplicate markers fail fast",
        f"- **Failure policy:** {state['failure_policy']}",
        f"- **Runtime artifacts:** `{store['paths']['dag_snapshot']}`, `{store['paths']['scheduler_snapshot']}`, `{queue['snapshot_path']}`, `{state['event_bus']['event_log_path']}`, `{store['paths']['metadata_manifest']}`",
        f"- **State persistence:** write #{store.get('write_count', 0)} | docs {store.get('available_documents', 0)}/{store.get('document_count', 0)} | {store.get('consistency_status', 'unknown')}",
        f"- **Queue snapshot:** ready {queue.get('ready_entries', 0)} | deferred {queue.get('deferred_entries', 0)} | retry {queue.get('retry_entries', 0)} | running {queue.get('running_leases', 0)}"
    ]
    if latest_cycle:
        lines.append(
            f"- **Flow realization:** cycle #{latest_cycle.get('id', 'n/a')} | in-order `{latest_cycle.get('is_in_order', False)}` | complete `{latest_cycle.get('is_complete', False)}`"
        )
        lines.append(
            f"- **Latest realized sequence:** {' -> '.join(cast(list[str], latest_cycle.get('completed_sequence', [])))}"
        )
    if workflow.get("run_url"):
        lines.append(f"- **Run URL:** [Open latest run]({workflow['run_url']})")
    return "\n".join(lines)


def render_workflow_dag(state: dict[str, Any]) -> str:
    """Render DAG card list and graph snapshot metadata."""
    dag_snapshot = cast(dict[str, Any], load_json(DAG_PATH, {}))
    roots = ", ".join(cast(list[str], dag_snapshot.get("roots", []))) or "none"
    leaves = ", ".join(cast(list[str], dag_snapshot.get("leaves", []))) or "none"
    lines = ['<div align="left">']
    lines.append(
        render_card(
            "dag-snapshot",
            [
                render_pill("nodes", str(dag_snapshot.get("node_count", len(state["tasks"]))), "334155"),
                render_pill("edges", str(dag_snapshot.get("edge_count", state["workflow"].get("dag_edges", 0))), "2563eb")
            ],
            (
                f"file: <code>{escape(relative_repo_path(DAG_PATH))}</code> | "
                f"roots: <code>{escape(roots)}</code> | "
                f"leaves: <code>{escape(leaves)}</code>"
            )
        )
    )
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
        lines.append(render_card(name, pills, body))
    lines.append("</div>")
    return "\n".join(lines) if state["tasks"] else "- No DAG tasks defined."


def render_scheduler_state(state: dict[str, Any]) -> str:
    """Render scheduler counters and scheduler snapshot section."""
    scheduler = state["scheduler"]
    scheduler_snapshot = cast(dict[str, Any], load_json(SCHEDULER_PATH, {}))
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
        render_card(
            "scheduler-snapshot",
            [
                render_pill("queue-depth", str(scheduler_snapshot.get("queue_depth", state["message_queue"].get("current_depth", 0))), "64748b"),
                render_pill("retry", str(scheduler_snapshot.get("retry_entries", state["message_queue"].get("retry_entries", 0))), "b45309")
            ],
            (
                f"file: <code>{escape(relative_repo_path(SCHEDULER_PATH))}</code> | "
                f"running leases: <code>{escape(str(scheduler_snapshot.get('running_leases', state['message_queue'].get('running_leases', 0))))}</code>"
            )
        ),
        "</div>"
    ]
    return "\n".join(lines)


def render_message_queue(state: dict[str, Any]) -> str:
    """Render queue runtime, snapshot, and operation capabilities."""
    queue = state["message_queue"]
    operation_items = queue.get("operations", [])
    depth_color = "d97706" if queue["current_depth"] > 0 else "64748b"
    lines = [
        '<div align="left">',
        "<p>",
        render_badge("Queue", str(queue["backend"]), "2563eb"),
        render_badge("Delivery", str(queue["delivery_guarantee"]), "0f766e"),
        render_badge("Ordering", str(queue["ordering"]), "334155"),
        "</p>",
        render_card(
            "queue-runtime",
            [
                render_pill("depth", str(queue["current_depth"]), depth_color),
                render_pill("deferred", str(queue.get("deferred_entries", 0)), "64748b"),
                render_pill("retry", str(queue.get("retry_entries", 0)), "b45309"),
                render_pill("running", str(queue.get("running_leases", 0)), "0284c7"),
                render_pill("max-depth", str(queue["max_depth_seen"]), "b45309"),
                render_pill("completed", str(queue["total_completed"]), "16a34a")
            ],
            (
                f"persistence: <code>{escape(str(queue['persistence']))}</code> | "
                f"dead-letter enabled: <code>{escape(str(queue['dead_letter_enabled']))}</code>"
            )
        ),
        render_card(
            "queue-snapshot",
            [
                render_pill("ready", str(queue.get("ready_entries", 0)), depth_color),
                render_pill("dispatched", str(queue["total_dispatched"]), "0284c7")
            ],
            (
                f"file: <code>{escape(str(queue['snapshot_path']))}</code> | "
                f"updated: <code>{escape(format_time(queue.get('snapshot_updated_at')))}</code>"
            )
        ),
        render_card(
            "queue-operations",
            [render_pill("count", str(len(normalize_named_items(operation_items))), "334155")],
            render_named_items(operation_items)
        ),
        "</div>"
    ]
    return "\n".join(lines)


def render_state_store(state: dict[str, Any]) -> str:
    """Render state-store capabilities and tracked documents."""
    store = state["state_store"]
    metadata_store = cast(dict[str, Any], load_json(METADATA_STORE_PATH, {}))
    documents = cast(list[dict[str, Any]], metadata_store.get("documents", store.get("documents", [])))
    consistency_status = str(metadata_store.get("consistency_status", store.get("consistency_status", "unknown")))
    consistency_color = {"healthy": "16a34a", "degraded": "d97706"}.get(consistency_status, "64748b")
    features = store.get("store_features", [])
    total_size_bytes = int(metadata_store.get("total_size_bytes", store.get("total_size_bytes", 0)))
    lines = [
        '<div align="left">',
        "<p>",
        render_badge("Store", str(store["backend"]), "2563eb"),
        render_badge("HA", str(store["ha_model"]), "0f766e"),
        render_badge("Tx", str(store["transaction_model"]), "334155"),
        render_badge("Consistency", consistency_status, consistency_color),
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
                f"dag: <code>{escape(str(store['paths']['dag_snapshot']))}</code> | "
                f"scheduler: <code>{escape(str(store['paths']['scheduler_snapshot']))}</code> | "
                f"queue: <code>{escape(str(store['paths']['queue_snapshot']))}</code> | "
                f"event log: <code>{escape(str(store['paths']['event_log']))}</code> | "
                f"manifest: <code>{escape(str(store['paths']['metadata_manifest']))}</code>"
            )
        ),
        render_card(
            "store-features",
            [render_pill("count", str(len(normalize_named_items(features))), "334155")],
            render_named_items(features)
        ),
        render_card(
            "managed-documents",
            [
                render_pill("tracked", str(store.get("document_count", len(documents))), "334155"),
                render_pill("available", str(store.get("available_documents", len([doc for doc in documents if doc.get('exists')]))), "16a34a"),
                render_pill("size", format_bytes(total_size_bytes), "0284c7")
            ],
            render_document_items(documents)
        ),
        f"<sub>last persisted: {escape(format_time(cast(str | None, metadata_store.get('last_persisted_at', store.get('last_persisted_at')))))}</sub>",
        "</div>"
    ]
    return "\n".join(lines)


def render_event_bus(state: dict[str, Any]) -> str:
    """Render event bus status, integrations, and recent events."""
    bus = state["event_bus"]
    integration_items = bus.get("integration_options", [])
    lines = [
        '<div align="left">',
        "<p>",
        render_badge("Event Bus", str(bus["backend"]), "2563eb"),
        render_badge("Semantics", str(bus["delivery_semantics"]), "0f766e"),
        render_badge("Published", str(bus["published_events"]), "0284c7"),
        "</p>",
        render_card(
            "trigger-and-subscribers",
            [render_pill("mode", str(bus.get("trigger_mode", "n/a")), "334155")],
            (
                f"subscribers: <code>{escape(', '.join(bus.get('subscribers', [])) or 'none')}</code> | "
                f"last event: <code>{escape(format_time(bus.get('last_event_at')))}</code> | "
                f"log: <code>{escape(str(bus.get('event_log_path', 'n/a')))}</code>"
            )
        ),
        render_card(
            "implemented-integrations",
            [render_pill("count", str(len(normalize_named_items(integration_items))), "334155")],
            render_named_items(integration_items)
        )
    ]

    recent = bus.get("recent_events", [])
    if recent:
        event_rows: list[str] = []
        for item in reversed(recent[-5:]):
            event_rows.append(
                " | ".join([
                    f"<code>{escape(format_time(item.get('at')))}</code>",
                    f"<b>{escape(str(item.get('type', 'unknown')))}</b>",
                    f"<code>{escape(str(item.get('source', 'unknown')))}</code>",
                    escape(summarize_runtime_message(str(item.get('details', ''))))
                ])
            )
        lines.append(
            render_card(
                "recent-events",
                [render_pill("window", str(min(len(recent), 5)), "334155")],
                "<br/>".join(f"- {row}" for row in event_rows)
            )
        )
    else:
        lines.append("<sub>No events published.</sub>")

    lines.append("</div>")
    return "\n".join(lines)


def render_worker_pools(state: dict[str, Any]) -> str:
    """Render worker pool scaling/status cards."""
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
        body = f"max workers: {pool['max_workers']} | scale: {escape(str(pool['last_scale_reason']))}"
        lines.append(render_card(name, pills, body))
    lines.append("</div>")
    return "\n".join(lines) if state["worker_pools"] else "- No worker pools configured."


def render_worker_registry(state: dict[str, Any]) -> str:
    """Render worker registry details."""
    lines = ['<div align="left">']
    for name, worker in state["workers"].items():
        enabled = "enabled" if worker.get("enabled") else "disabled"
        enabled_color = "16a34a" if worker.get("enabled") else "dc2626"
        capabilities = ", ".join(worker.get("capabilities", [])) or "none"
        mode = "managed" if worker.get("managed_by_default", True) else "manual-only"
        mode_color = "0284c7" if worker.get("managed_by_default", True) else "b45309"
        pills = [
            render_pill("state", enabled, enabled_color),
            render_pill("mode", mode, mode_color),
            render_pill("type", str(worker["worker_type"]), "334155"),
            render_pill("pool", str(worker["pool"]), "0f766e")
        ]
        body = f"display: {escape(str(worker['display_name']))} | capabilities: {escape(capabilities)}"
        lines.append(render_card(name, pills, body))
    lines.append("</div>")
    return "\n".join(lines) if state["workers"] else "- No workers registered."


def render_worker_health(state: dict[str, Any]) -> str:
    """Render worker heartbeat/health cards."""
    lines = ['<div align="left">']
    for name, worker in state["workers"].items():
        health = str(worker["health"])
        health_color = {"Healthy": "16a34a", "Stale": "d97706", "Offline": "dc2626"}.get(health, "64748b")
        pills = [render_pill("health", health, health_color)]
        body = (
            f"heartbeat: {escape(format_time(worker.get('last_heartbeat_at')))} | "
            f"last success: {escape(format_time(worker.get('last_success_at')))}"
        )
        lines.append(render_card(name, pills, body))
    lines.append("</div>")
    return "\n".join(lines) if state["workers"] else "- No worker health data."


def render_task_state(state: dict[str, Any]) -> str:
    """Render task state cards for each managed job."""
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
        message = summarize_runtime_message(str(task.get("message", "")))
        pills = [
            render_pill("status", status, status_color),
            render_pill("priority", str(task["priority"]), "2563eb"),
            render_pill("attempt", attempt_ratio, "334155"),
            render_pill("pool", str(task["pool"]), "0f766e")
        ]
        body = f"updated: {escape(format_time(task.get('updated_at')))} | {escape(message)}"
        lines.append(render_card(name, pills, body))
    lines.append("</div>")
    return "\n".join(lines) if state["tasks"] else "- No tasks tracked."


def render_dead_letters(dead_letters: list[dict[str, Any]]) -> str:
    """Render dead-letter queue summary cards."""
    if not dead_letters:
        return '<div align="left"><sub>No dead letters.</sub></div>'

    lines = ['<div align="left">']
    for item in reversed(dead_letters[-5:]):
        pills = [
            render_pill("attempts", str(item["attempts"]), "b45309"),
            render_pill("failed", "yes", "dc2626")
        ]
        reason = summarize_runtime_message(str(item.get("reason", "")))
        body = f"at: {escape(format_time(item['failed_at']))} | reason: {escape(reason)}"
        lines.append(render_card(str(item["task"]), pills, body))
    lines.append("</div>")
    return "\n".join(lines)


def render_readme(state: dict[str, Any], dead_letters: list[dict[str, Any]]) -> None:
    """Render and write all automation dashboard sections into README.

    Args:
        state: Runtime workflow state.
        dead_letters: Dead-letter queue tail entries for rendering.
    """
    sections: dict[str, str] = {
        "automation_status": render_automation_status(state),
        "workflow_dag": render_workflow_dag(state),
        "scheduler_state": render_scheduler_state(state),
        "message_queue": render_message_queue(state),
        "state_store": render_state_store(state),
        "event_bus": render_event_bus(state),
        "worker_pools": render_worker_pools(state),
        "worker_registry": render_worker_registry(state),
        "worker_health": render_worker_health(state),
        "task_state": render_task_state(state),
        "dead_letters": render_dead_letters(dead_letters)
    }

    missing_sections: list[str] = []
    for section, content in sections.items():
        start_marker, end_marker = AUTOMATION_MARKERS[section]
        if not try_update_readme_section(start_marker, end_marker, content):
            missing_sections.append(section)

    if missing_sections:
        print(
            "::warning::Skipped README automation sections with missing markers: "
            + ", ".join(missing_sections)
        )
