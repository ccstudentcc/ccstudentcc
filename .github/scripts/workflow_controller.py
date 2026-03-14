from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from readme_utils import update_readme_section


ROOT = Path(__file__).resolve().parents[2]
README_PATH = ROOT / "README.md"
REGISTRY_PATH = ROOT / ".github/manager/registry.json"
STATE_PATH = ROOT / ".github/manager/state/state.json"
DEAD_LETTERS_PATH = ROOT / ".github/manager/state/dead-letters.json"
POLL_SECONDS = 10
ASIA_SHANGHAI = timezone(timedelta(hours=8))
AUTOMATION_MARKERS = {
    "automation_status": ("<!--START_SECTION:automation_status-->", "<!--END_SECTION:automation_status-->"),
    "worker_registry": ("<!--START_SECTION:worker_registry-->", "<!--END_SECTION:worker_registry-->"),
    "worker_health": ("<!--START_SECTION:worker_health-->", "<!--END_SECTION:worker_health-->"),
    "task_state": ("<!--START_SECTION:task_state-->", "<!--END_SECTION:task_state-->"),
    "dead_letters": ("<!--START_SECTION:dead_letters-->", "<!--END_SECTION:dead_letters-->")
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: object) -> object:
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


def initial_task(worker: dict) -> dict:
    return {
        "status": "Pending",
        "attempt": 0,
        "max_attempts": worker["max_retries"] + 1,
        "started_at": None,
        "completed_at": None,
        "updated_at": iso_now(),
        "message": "Queued by controller"
    }


def initial_worker_state(worker: dict) -> dict:
    return {
        "display_name": worker["display_name"],
        "enabled": worker["enabled"],
        "timeout_seconds": worker["timeout_seconds"],
        "max_retries": worker["max_retries"],
        "last_heartbeat_at": None,
        "last_started_at": None,
        "last_completed_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_exit_code": None,
        "last_error": None,
        "health": "Unknown"
    }


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


def initialize_state(registry: dict) -> tuple[dict, list[dict]]:
    existing_state = load_json(STATE_PATH, {})
    dead_letters = load_json(DEAD_LETTERS_PATH, [])
    run_id = os.getenv("GITHUB_RUN_ID")
    run_url = build_run_url()

    state = {
        "workflow": {
            "name": "workflow-manager",
            "status": "Running",
            "started_at": iso_now(),
            "completed_at": None,
            "run_id": run_id,
            "run_url": run_url
        },
        "failure_policy": "continue-on-error + retry + timeout cancel + dead-letter on exhaust",
        "managed_jobs": [worker["name"] for worker in registry["workers"] if worker.get("enabled", True)],
        "workers": existing_state.get("workers", {}),
        "tasks": {}
    }

    for worker in registry["workers"]:
        worker_state = state["workers"].get(worker["name"], initial_worker_state(worker))
        worker_state.update({
            "display_name": worker["display_name"],
            "enabled": worker["enabled"],
            "timeout_seconds": worker["timeout_seconds"],
            "max_retries": worker["max_retries"],
            "health": compute_health(worker_state, worker["heartbeat_grace_seconds"])
        })
        state["workers"][worker["name"]] = worker_state
        state["tasks"][worker["name"]] = initial_task(worker)

    return state, dead_letters


def persist(state: dict, dead_letters: list[dict], registry: dict) -> None:
    for worker in registry["workers"]:
        worker_state = state["workers"][worker["name"]]
        worker_state["health"] = compute_health(worker_state, worker["heartbeat_grace_seconds"])

    save_json(STATE_PATH, state)
    save_json(DEAD_LETTERS_PATH, dead_letters[-20:])
    render_readme(state, dead_letters[-10:])


def render_automation_status(state: dict) -> str:
    workflow = state["workflow"]
    lines = [
        f"- Last automation update: {format_time(workflow.get('completed_at') or workflow.get('started_at'))}",
        "- Timezone: Asia/Shanghai (UTC+8)",
        "- Orchestrator: workflow-manager/controller",
        f"- Managed jobs: {', '.join(state['managed_jobs']) or 'none'}",
        f"- Failure policy: {state['failure_policy']}"
    ]
    if workflow.get("run_url"):
        lines.append(f"- Run URL: {workflow['run_url']}")
    return "\n".join(lines)


def render_worker_registry(state: dict) -> str:
    lines = []
    for name, worker in state["workers"].items():
        enabled = "enabled" if worker.get("enabled") else "disabled"
        lines.append(
            f"- {name}: {worker['display_name']} | {enabled} | timeout {worker['timeout_seconds']}s | retries {worker['max_retries']}"
        )
    return "\n".join(lines) if lines else "- No workers registered."


def render_worker_health(state: dict) -> str:
    lines = []
    for name, worker in state["workers"].items():
        lines.append(
            f"- {name}: {worker['health']} | heartbeat {format_time(worker.get('last_heartbeat_at'))} | last success {format_time(worker.get('last_success_at'))}"
        )
    return "\n".join(lines) if lines else "- No worker health data."


def render_task_state(state: dict) -> str:
    lines = []
    for name, task in state["tasks"].items():
        lines.append(
            f"- {name}: {task['status']} | attempt {task['attempt']}/{task['max_attempts']} | updated {format_time(task.get('updated_at'))} | {task['message']}"
        )
    return "\n".join(lines) if lines else "- No tasks tracked."


def render_dead_letters(dead_letters: list[dict]) -> str:
    if not dead_letters:
        return "- No dead letters."

    lines = []
    for item in reversed(dead_letters[-5:]):
        lines.append(
            f"- {item['worker']}: failed after {item['attempts']} attempts at {format_time(item['failed_at'])} | {item['reason']}"
        )
    return "\n".join(lines)


def render_readme(state: dict, dead_letters: list[dict]) -> None:
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["automation_status"], render_automation_status(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["worker_registry"], render_worker_registry(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["worker_health"], render_worker_health(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["task_state"], render_task_state(state))
    update_readme_section(README_PATH, *AUTOMATION_MARKERS["dead_letters"], render_dead_letters(dead_letters))


def mark_worker_heartbeat(state: dict, worker_name: str, task_status: str | None = None, message: str | None = None) -> None:
    stamp = iso_now()
    worker_state = state["workers"][worker_name]
    task_state = state["tasks"][worker_name]
    worker_state["last_heartbeat_at"] = stamp
    task_state["updated_at"] = stamp
    if task_status:
        task_state["status"] = task_status
    if message:
        task_state["message"] = message


def execute_worker(worker: dict, state: dict, dead_letters: list[dict], registry: dict) -> None:
    worker_name = worker["name"]
    worker_state = state["workers"][worker_name]
    task_state = state["tasks"][worker_name]

    if not worker.get("enabled", True):
        task_state.update({
            "status": "Skipped",
            "attempt": 0,
            "updated_at": iso_now(),
            "message": "Worker disabled"
        })
        persist(state, dead_letters, registry)
        return

    max_attempts = worker["max_retries"] + 1

    for attempt in range(1, max_attempts + 1):
        task_state["attempt"] = attempt
        task_state["status"] = "Retry" if attempt > 1 else "Running"
        task_state["started_at"] = task_state["started_at"] or iso_now()
        task_state["updated_at"] = iso_now()
        task_state["message"] = f"Dispatching attempt {attempt}/{max_attempts}"
        worker_state["last_started_at"] = iso_now()
        worker_state["last_error"] = None
        worker_state["last_heartbeat_at"] = iso_now()
        persist(state, dead_letters, registry)

        process = subprocess.Popen(
            worker["command"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy()
        )

        deadline = time.time() + worker["timeout_seconds"]
        timed_out = False
        stdout = ""
        stderr = ""

        while True:
            return_code = process.poll()
            if return_code is not None:
                stdout, stderr = process.communicate()
                break

            if time.time() >= deadline:
                timed_out = True
                process.kill()
                stdout, stderr = process.communicate()
                break

            mark_worker_heartbeat(state, worker_name, "Running", f"Heartbeat OK on attempt {attempt}/{max_attempts}")
            persist(state, dead_letters, registry)
            time.sleep(POLL_SECONDS)

        worker_state["last_completed_at"] = iso_now()
        worker_state["last_heartbeat_at"] = iso_now()
        task_state["updated_at"] = iso_now()

        if timed_out:
            worker_state["last_exit_code"] = None
            worker_state["last_failure_at"] = iso_now()
            worker_state["last_error"] = f"Timeout after {worker['timeout_seconds']}s"
            task_state["status"] = "Retry" if attempt < max_attempts else "Failed"
            task_state["message"] = worker_state["last_error"]
        elif process.returncode == 0:
            worker_state["last_exit_code"] = 0
            worker_state["last_success_at"] = iso_now()
            task_state["status"] = "Success"
            task_state["completed_at"] = iso_now()
            task_state["message"] = (stdout.strip() or "Worker completed successfully")[:240]
            persist(state, dead_letters, registry)
            return
        else:
            worker_state["last_exit_code"] = process.returncode
            worker_state["last_failure_at"] = iso_now()
            failure_message = stderr.strip() or stdout.strip() or f"Exit code {process.returncode}"
            worker_state["last_error"] = failure_message[:500]
            task_state["status"] = "Retry" if attempt < max_attempts else "Failed"
            task_state["message"] = failure_message[:240]

        persist(state, dead_letters, registry)

    task_state["completed_at"] = iso_now()
    dead_letters.append({
        "worker": worker_name,
        "attempts": max_attempts,
        "failed_at": iso_now(),
        "reason": worker_state.get("last_error") or "Unknown failure"
    })
    persist(state, dead_letters, registry)


def main() -> int:
    registry = load_json(REGISTRY_PATH, {"workers": []})
    state, dead_letters = initialize_state(registry)
    persist(state, dead_letters, registry)

    for worker in registry["workers"]:
        execute_worker(worker, state, dead_letters, registry)

    state["workflow"]["status"] = "Completed"
    state["workflow"]["completed_at"] = iso_now()
    persist(state, dead_letters, registry)

    failed = [task for task in state["tasks"].values() if task["status"] == "Failed"]
    if failed:
        print(f"Completed with {len(failed)} failed worker(s) recorded in dead-letter queue")
    else:
        print("Completed with all workers successful")
    return 0


if __name__ == "__main__":
    sys.exit(main())