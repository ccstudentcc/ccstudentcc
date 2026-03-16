from __future__ import annotations

"""Shared constants and helpers for workflow orchestration modules."""

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import time
from threading import Lock

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / ".github/manager/registry.json"
WORKFLOW_PATH = ROOT / ".github/manager/workflow.json"
STATE_PATH = ROOT / ".github/manager/state/state.json"
DEAD_LETTERS_PATH = ROOT / ".github/manager/state/dead-letters.json"
QUEUE_PATH = ROOT / ".github/manager/state/queue.json"
EVENT_LOG_PATH = ROOT / ".github/manager/state/event-log.json"
METADATA_STORE_PATH = ROOT / ".github/manager/state/metadata-store.json"
DAG_PATH = ROOT / ".github/manager/state/dag.json"
SCHEDULER_PATH = ROOT / ".github/manager/state/scheduler.json"
POLL_SECONDS = 2
ASIA_SHANGHAI = timezone(timedelta(hours=8))
TERMINAL_STATUSES = {"Success", "Failed", "Skipped", "Blocked"}
WAITING_STATUSES = {"Pending", "Deferred", "Retry"}
CANONICAL_FLOW_ORDER = [
    "Orchestrator",
    "DAG",
    "Scheduler",
    "Queue",
    "State Store",
    "Event Bus",
    "Worker Pools",
    "Registry",
    "Health",
    "Tasks",
    "DLQ",
]


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Return current UTC timestamp in compact ISO format."""
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_at(offset_seconds: int) -> str:
    """Return UTC timestamp offset by given seconds."""
    return (utc_now() + timedelta(seconds=offset_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    """Load JSON from disk and return a deepcopy default when file is absent."""
    if not path.exists():
        return deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: object) -> None:
    """Persist JSON payload with UTF-8 and stable indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# Optional write-batching / debounce support
WRITE_BATCHING_ENABLED = os.getenv("WORKFLOW_WRITE_BATCHING", "true").lower() == "true"
WRITE_DEBOUNCE_SECONDS = int(os.getenv("WORKFLOW_WRITE_DEBOUNCE_SECONDS", "2"))

# Internal in-memory queue for pending writes: Path -> payload
_WRITE_QUEUE: dict[Path, object] = {}
_WRITE_QUEUE_LOCK = Lock()
_LAST_ENQUEUE_AT = 0.0


def enqueue_json(path: Path, payload: object) -> None:
    """Enqueue a JSON payload for batched write or write immediately when batching disabled.

    This function is safe to call from multiple places; callers should call
    `flush_json_writes()` to ensure queued writes are persisted.
    """
    global _LAST_ENQUEUE_AT
    if not WRITE_BATCHING_ENABLED:
        save_json(path, payload)
        return

    with _WRITE_QUEUE_LOCK:
        _WRITE_QUEUE[path] = payload
        _LAST_ENQUEUE_AT = time.time()


def flush_json_writes(force: bool = False) -> None:
    """Flush any enqueued writes to disk.

    If `force` is False, flush will be a no-op while recent enqueues exist within
    the debounce period. When `force` is True, all queued writes are immediately
    persisted.
    """
    global _LAST_ENQUEUE_AT
    if not WRITE_BATCHING_ENABLED:
        return

    now = time.time()
    with _WRITE_QUEUE_LOCK:
        if not _WRITE_QUEUE:
            return
        if not force and (now - _LAST_ENQUEUE_AT) < WRITE_DEBOUNCE_SECONDS:
            return

        items = list(_WRITE_QUEUE.items())
        _WRITE_QUEUE.clear()

    # best-effort write with retries and durable error logging
    PERSISTENCE_ERROR_LOG = ROOT / ".github" / "manager" / "state" / "persistence-errors.log"
    for path, payload in items:
        saved = False
        attempts = 0
        max_retries = 3
        while not saved and attempts < max_retries:
            try:
                save_json(path, payload)
                saved = True
            except Exception as exc:
                attempts += 1
                # exponential backoff
                time.sleep(0.05 * (2 ** (attempts - 1)))

        if not saved:
            # record failure to a log for later inspection and re-enqueue for next flush
            try:
                PERSISTENCE_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
                with PERSISTENCE_ERROR_LOG.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{iso_now()}] Failed to persist {relative_repo_path(path)} after {max_retries} attempts\n")
            except Exception:
                # best-effort only; avoid raising
                pass

            with _WRITE_QUEUE_LOCK:
                _WRITE_QUEUE[path] = payload


def relative_repo_path(path: Path) -> str:
    """Convert absolute path to repository relative POSIX path."""
    return path.relative_to(ROOT).as_posix()


def format_time(iso_value: str | None) -> str:
    """Render ISO timestamp in CST for dashboard display."""
    if not iso_value:
        return "n/a"
    normalized = iso_value.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(normalized).astimezone(ASIA_SHANGHAI)
    return stamp.strftime("%Y-%m-%d %H:%M CST")


def build_run_url() -> str | None:
    """Build GitHub Actions run URL from runtime environment variables."""
    server = os.getenv("GITHUB_SERVER_URL")
    repository = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if server and repository and run_id:
        return f"{server}/{repository}/actions/runs/{run_id}"
    return None
