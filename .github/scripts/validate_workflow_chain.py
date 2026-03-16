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

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_common import CANONICAL_FLOW_ORDER
from workflow_contract import worker_contracts_by_name

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_MANAGER_PATH = ROOT / ".github/workflows/workflow-manager.yml"
WORKFLOW_SPEC_PATH = ROOT / ".github/manager/workflow.json"
REGISTRY_PATH = ROOT / ".github/manager/registry.json"
STATE_PATH = ROOT / ".github/manager/state/state.json"
README_PATH = ROOT / "README.md"
DOC_PATH = ROOT / "docs/workflows-automation-guide.md"
RENDERER_PATH = ROOT / ".github/scripts/workflow_renderer.py"
CONTROLLER_PATH = ROOT / ".github/scripts/workflow_controller.py"

EXPECTED_FLOW = CANONICAL_FLOW_ORDER
EXPECTED_FLOW_BULLET = " • ".join(EXPECTED_FLOW)
EXPECTED_FLOW_CONSOLE = " -> ".join(EXPECTED_FLOW)
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



def shell_command_from_contract(contract: dict[str, Any]) -> str:
    """Return the worker contract command as used by the managed wrapper."""
    command = contract.get("command", [])
    execution_mode = contract.get("execution_mode")
    ensure(isinstance(command, list) and bool(command), f"Worker {contract.get('name')} command 无效")
    if execution_mode == "bash" and len(command) == 2 and command[0] == "bash":
        return command[1]
    return " ".join(command)



def quoted_yaml_value(value: str) -> str:
    """Return simple regex alternates for quoted/unquoted YAML scalar matching."""
    escaped = re.escape(value)
    return rf"(?:'{escaped}'|\"{escaped}\"|{escaped})"



def ensure_wrapper_field(content: str, field: str, expected: str, worker_name: str) -> None:
    """Ensure a workflow wrapper `with:` field matches expected scalar content."""
    pattern = rf"^\s+{re.escape(field)}:\s*{quoted_yaml_value(expected)}\s*$"
    ensure(
        re.search(pattern, content, re.MULTILINE) is not None,
        f"Worker {worker_name} wrapper {field} 与 contract 不一致: expected {expected}",
    )



def validate_worker_workflow_wrapper(contract: dict[str, Any], workflow_text: str, workflow_path: Path) -> None:
    """Validate one standalone worker workflow wrapper against contract metadata."""
    worker_name = str(contract.get("name", workflow_path.stem))
    ensure(
        "uses: ./.github/workflows/_managed-readme-worker.yml" in workflow_text,
        f"Worker {worker_name} 未使用共享 managed worker workflow shell",
    )

    ensure_wrapper_field(workflow_text, "execution_mode", str(contract["execution_mode"]), worker_name)
    ensure_wrapper_field(workflow_text, "command", shell_command_from_contract(contract), worker_name)
    ensure_wrapper_field(workflow_text, "summary_label", str(contract["summary_label"]), worker_name)
    ensure_wrapper_field(workflow_text, "commit_scope", " ".join(contract["commit_scope"]), worker_name)

    required_secrets = contract["required_secrets"]
    expected_required_secrets = "None" if not required_secrets else " ".join(required_secrets)
    ensure_wrapper_field(workflow_text, "required_secrets", expected_required_secrets, worker_name)

    if not required_secrets:
        ensure(
            re.search(r"^\s+required_secrets:\s*(?:'None'|\"None\"|None)\s*$", workflow_text, re.MULTILINE) is not None,
            f"Worker {worker_name} 缺少 required_secrets=None 标记",
        )
        return

    for secret_name in required_secrets:
        secret_declared = re.search(rf"^\s+{re.escape(secret_name)}:\s*$", workflow_text, re.MULTILINE) is not None

        if secret_name == "GITHUB_TOKEN":
            ensure(
                not secret_declared,
                f"Worker {worker_name} 不应在 workflow_call.secrets 中声明保留 secret: {secret_name}",
            )
        else:
            ensure(
                secret_declared,
                f"Worker {worker_name} workflow_call 未声明 secret: {secret_name}",
            )

        ensure(
            f"${{{{ secrets.{secret_name} }}}}" in workflow_text,
            f"Worker {worker_name} wrapper 未引用 secret: {secret_name}",
        )



def validate_registry_worker_workflows(registry: dict[str, Any], root: Path = ROOT) -> None:
    """Validate worker registry contracts against standalone workflow wrappers."""
    contracts = worker_contracts_by_name(registry)
    ensure(bool(contracts), "registry.json 缺少 worker contracts")

    for worker_name, contract in contracts.items():
        workflow_rel = contract["workflow"]
        workflow_path = root / workflow_rel
        ensure(workflow_path.exists(), f"Worker {worker_name} 声明的 workflow 不存在: {workflow_rel}")
        workflow_text = workflow_path.read_text(encoding="utf-8")
        validate_worker_workflow_wrapper(contract, workflow_text, workflow_path)



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
        "flow_order",
        "worker_pools",
        "workers",
        "tasks",
    }
    ensure(required_state_keys.issubset(set(state.keys())), "state.json 缺少关键链路字段")

    flow_order = state.get("flow_order", {})
    ensure(flow_order.get("expected_sequence") == EXPECTED_FLOW, "state.json 中的 flow_order.expected_sequence 与标准链路不一致")
    latest_cycle = flow_order.get("latest_completed_cycle")
    ensure(isinstance(latest_cycle, dict), "state.json 缺少 flow_order.latest_completed_cycle")
    ensure(latest_cycle.get("completed_sequence") == EXPECTED_FLOW, "最新 flow cycle 未完整覆盖标准链路")
    ensure(bool(latest_cycle.get("is_in_order")), "最新 flow cycle 未按标准顺序执行")
    ensure(bool(latest_cycle.get("is_complete")), "最新 flow cycle 未标记为 complete")



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
    ensure("from workflow_contract import worker_contracts_by_name" in controller_text, "workflow_controller.py 未导入 worker_contracts_by_name")
    ensure("worker_contracts_by_name(registry)" in controller_text, "workflow_controller.py 未使用 worker_contracts_by_name")



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
    validate_registry_worker_workflows(registry)
    validate_state_artifacts(state)
    validate_readme_and_docs(readme_text, doc_text)
    validate_scripts_alignment(renderer_text, controller_text)

    print("workflow-chain validation passed")
    print(f"flow: {EXPECTED_FLOW_CONSOLE}")



if __name__ == "__main__":
    try:
        run()
    except ValidationError as exc:
        print(f"workflow-chain validation failed: {exc}")
        sys.exit(1)
