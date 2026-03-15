from __future__ import annotations

"""Validate end-to-end workflow chain coverage and consistency.

The validator enforces that manager config, workflow scripts, state artifacts,
README markers, and docs all align with the canonical chain order:
Orchestrator -> DAG -> Scheduler -> Queue -> State Store -> Event Bus ->
Worker Pools -> Registry -> Health -> Tasks -> DLQ.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_MANAGER_PATH = ROOT / ".github/workflows/workflow-manager.yml"
WORKFLOW_SPEC_PATH = ROOT / ".github/manager/workflow.json"
REGISTRY_PATH = ROOT / ".github/manager/registry.json"
STATE_PATH = ROOT / ".github/manager/state/state.json"
README_PATH = ROOT / "README.md"
DOC_PATH = ROOT / "docs/workflows-automation-guide.md"
RENDERER_PATH = ROOT / ".github/scripts/workflow_renderer.py"
CONTROLLER_PATH = ROOT / ".github/scripts/workflow_controller.py"

EXPECTED_FLOW = [
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
EXPECTED_FLOW_BULLET = " • ".join(EXPECTED_FLOW)
EXPECTED_MARKER_KEYS = [
    "automation_status",
    "workflow_dag",
    "scheduler_state",
    "message_queue",
    "state_store",
    "event_bus",
    "worker_pools",
    "worker_registry",
    "worker_health",
    "task_state",
    "dead_letters",
]
EXPECTED_STATE_FILES = [
    ".github/manager/state/state.json",
    ".github/manager/state/dag.json",
    ".github/manager/state/scheduler.json",
    ".github/manager/state/queue.json",
    ".github/manager/state/event-log.json",
    ".github/manager/state/dead-letters.json",
    ".github/manager/state/metadata-store.json",
]


class ValidationError(Exception):
    """Raised when one or more validation checks fail."""



def load_json(path: Path) -> dict[str, Any]:
    """Load JSON object from file path."""
    return json.loads(path.read_text(encoding="utf-8"))



def ensure(condition: bool, message: str) -> None:
    """Assert one validation condition."""
    if not condition:
        raise ValidationError(message)



def parse_cron_from_workflow_manager(content: str) -> str:
    """Extract cron expression from workflow-manager YAML text."""
    match = re.search(r"cron:\s*['\"]([^'\"]+)['\"]", content)
    ensure(match is not None, "workflow-manager.yml 缺少 schedule cron 配置")
    assert match is not None
    return match.group(1)



def extract_renderer_marker_keys(content: str) -> list[str]:
    """Extract automation marker keys from renderer source."""
    block = re.search(r"AUTOMATION_MARKERS\s*=\s*\{([\s\S]*?)\n\}", content)
    ensure(block is not None, "workflow_renderer.py 中未找到 AUTOMATION_MARKERS")
    assert block is not None
    keys = re.findall(r'"([a-z_]+)"\s*:\s*\(', block.group(1))
    return keys



def validate_manager_and_registry(workflow_spec: dict[str, Any], registry: dict[str, Any], workflow_manager_text: str) -> None:
    """Validate workflow-manager schedule and registry-task relations."""
    cron_workflow = parse_cron_from_workflow_manager(workflow_manager_text)
    cron_spec = workflow_spec.get("scheduler", {}).get("cron")
    ensure(cron_workflow == cron_spec, f"cron 不一致: workflow-manager={cron_workflow}, workflow.json={cron_spec}")

    required_top_keys = {
        "workflow",
        "scheduler",
        "message_queue",
        "state_store",
        "event_bus",
        "worker_pools",
        "tasks",
    }
    ensure(required_top_keys.issubset(set(workflow_spec.keys())), "workflow.json 缺少链路顶层配置字段")

    tasks = workflow_spec.get("tasks", [])
    pools = {pool["name"] for pool in workflow_spec.get("worker_pools", [])}
    workers = {worker["name"] for worker in registry.get("workers", [])}
    ensure(bool(tasks), "workflow.json 的 tasks 为空")

    for task in tasks:
        ensure(task.get("worker") in workers, f"任务 {task.get('name')} 引用了不存在的 worker: {task.get('worker')}")
        ensure(task.get("pool") in pools, f"任务 {task.get('name')} 引用了不存在的 pool: {task.get('pool')}")



def validate_state_artifacts(state: dict[str, Any]) -> None:
    """Validate runtime state structure and artifact file existence."""
    for rel in EXPECTED_STATE_FILES:
        ensure((ROOT / rel).exists(), f"缺少状态文件: {rel}")

    required_state_keys = {
        "workflow",
        "scheduler",
        "message_queue",
        "state_store",
        "event_bus",
        "worker_pools",
        "workers",
        "tasks",
    }
    ensure(required_state_keys.issubset(set(state.keys())), "state.json 缺少关键链路字段")



def validate_readme_and_docs(readme_text: str, doc_text: str) -> None:
    """Validate README markers and docs flow mapping."""
    ensure(EXPECTED_FLOW_BULLET in readme_text, "README 未包含标准 Flow order（使用 • 分隔）")

    for key in EXPECTED_MARKER_KEYS:
        start = f"<!--START_SECTION:{key}-->"
        end = f"<!--END_SECTION:{key}-->"
        ensure(start in readme_text and end in readme_text, f"README 缺少区块 marker: {key}")

    for item in EXPECTED_FLOW:
        ensure(item in doc_text, f"文档缺少链路项: {item}")



def validate_scripts_alignment(renderer_text: str, controller_text: str) -> None:
    """Validate script-level chain mapping and module split."""
    marker_keys = extract_renderer_marker_keys(renderer_text)
    ensure(marker_keys == EXPECTED_MARKER_KEYS, "workflow_renderer.py 的 marker 顺序与链路顺序不一致")

    ensure("from workflow_runtime import" in controller_text, "workflow_controller.py 未接入 workflow_runtime 分层")
    ensure("from workflow_state import" in controller_text, "workflow_controller.py 未接入 workflow_state 分层")



def run() -> None:
    """Run all validations and print a compact report."""
    workflow_manager_text = WORKFLOW_MANAGER_PATH.read_text(encoding="utf-8")
    workflow_spec = load_json(WORKFLOW_SPEC_PATH)
    registry = load_json(REGISTRY_PATH)
    state = load_json(STATE_PATH)
    readme_text = README_PATH.read_text(encoding="utf-8")
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    renderer_text = RENDERER_PATH.read_text(encoding="utf-8")
    controller_text = CONTROLLER_PATH.read_text(encoding="utf-8")

    validate_manager_and_registry(workflow_spec, registry, workflow_manager_text)
    validate_state_artifacts(state)
    validate_readme_and_docs(readme_text, doc_text)
    validate_scripts_alignment(renderer_text, controller_text)

    print("workflow-chain validation passed")
    print(f"flow: {EXPECTED_FLOW_BULLET}")



if __name__ == "__main__":
    try:
        run()
    except ValidationError as exc:
        print(f"workflow-chain validation failed: {exc}")
        sys.exit(1)
