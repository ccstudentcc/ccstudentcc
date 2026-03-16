from __future__ import annotations

"""Shared constants and helpers for workflow orchestration modules."""

import json
import os
import tempfile
import traceback
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
PERSISTENCE_METRICS_PATH = ROOT / ".github/manager/state/persistence-metrics.json"
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
    """Persist JSON payload with UTF-8 and stable indentation using atomic replace.

    Writes to a temporary file in the same directory and then uses ``os.replace``
    to atomically move the file into place. Ensures data is flushed to disk
    before replacement.
    """
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to `path` atomically using a temp file in same dir.

    This helper is intentionally independent from `save_json` so callers
    (e.g. metrics writer) can persist without relying on the public
    `save_json` symbol which may be monkeypatched in tests.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
        os.replace(tmp_path, str(path))
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


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

    # best-effort write with retries, enhanced durable error logging and metrics
    PERSISTENCE_ERROR_LOG = ROOT / ".github" / "manager" / "state" / "persistence-errors.log"
    for path, payload in items:
        saved = False
        attempts = 0
        max_retries = 3
        last_exc: Exception | None = None
        while not saved and attempts < max_retries:
            try:
                save_json(path, payload)
                saved = True
            except Exception as exc:
                last_exc = exc
                attempts += 1
                # exponential backoff
                time.sleep(0.05 * (2 ** (attempts - 1)))

        # prepare metrics update
        try:
            metrics = load_json(PERSISTENCE_METRICS_PATH, {"total_writes": 0, "failed_writes": 0, "total_attempts": 0, "failed_attempts": 0, "last_error": None, "last_error_at": None, "last_failed_path": None, "last_error_traceback": None})
        except Exception:
            metrics = {"total_writes": 0, "failed_writes": 0, "total_attempts": 0, "failed_attempts": 0, "last_error": None, "last_error_at": None, "last_failed_path": None, "last_error_traceback": None}

        attempts_per_item = attempts if not saved else attempts + 1
        if saved:
            metrics["total_writes"] = int(metrics.get("total_writes", 0)) + 1
            metrics["total_attempts"] = int(metrics.get("total_attempts", 0)) + int(attempts_per_item)
        else:
            # record failure to a log for later inspection and re-enqueue for next flush
            try:
                PERSISTENCE_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
                with PERSISTENCE_ERROR_LOG.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{iso_now()}] Failed to persist {relative_repo_path(path)} after {max_retries} attempts\n")
                    if last_exc is not None:
                        fh.write(f"    error: {type(last_exc).__name__}: {last_exc}\n")
                        fh.write(traceback.format_exc())
            except Exception:
                # best-effort only; avoid raising
                pass

            with _WRITE_QUEUE_LOCK:
                _WRITE_QUEUE[path] = payload

            metrics["failed_writes"] = int(metrics.get("failed_writes", 0)) + 1
            metrics["failed_attempts"] = int(metrics.get("failed_attempts", 0)) + int(attempts_per_item)
            metrics["last_error"] = str(last_exc) if last_exc is not None else None
            metrics["last_error_at"] = iso_now()
            metrics["last_failed_path"] = relative_repo_path(path)
            try:
                metrics["last_error_traceback"] = traceback.format_exc()
            except Exception:
                metrics["last_error_traceback"] = None

        # persist metrics best-effort using internal atomic writer (avoids calling save_json)
        try:
            _atomic_write_text(PERSISTENCE_METRICS_PATH, json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
        except Exception:
            # do not raise from metrics write
            pass


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
