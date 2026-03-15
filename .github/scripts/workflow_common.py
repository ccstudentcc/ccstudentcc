from __future__ import annotations

"""Shared constants and helpers for workflow orchestration modules."""

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
