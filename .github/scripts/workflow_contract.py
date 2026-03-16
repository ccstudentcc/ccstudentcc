from __future__ import annotations

"""Helpers for normalizing and validating managed worker contract data."""

from typing import Any

REQUIRED_CONTRACT_FIELDS = (
    "execution_mode",
    "workflow",
    "required_secrets",
    "commit_scope",
    "optional_readme_markers",
    "summary_label",
)
CONTRACT_METADATA_FIELDS = REQUIRED_CONTRACT_FIELDS
LIST_CONTRACT_FIELDS = (
    "required_secrets",
    "commit_scope",
    "optional_readme_markers",
)
ALLOWED_EXECUTION_MODES = {"python", "bash"}


def normalize_worker_contract(worker: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow normalized worker contract copy.

    Args:
        worker: Raw worker registry entry.

    Returns:
        Normalized worker contract data with optional list defaults.
    """
    normalized = dict(worker)
    for field in LIST_CONTRACT_FIELDS:
        if field in worker:
            value = worker[field]
            normalized[field] = list(value) if isinstance(value, list) else value
        else:
            normalized[field] = []
    return normalized


def validate_worker_contract(worker: dict[str, Any]) -> None:
    """Validate one worker contract payload.

    Raises:
        ValueError: If any required contract field is missing or malformed.
    """
    for field in REQUIRED_CONTRACT_FIELDS:
        if field not in worker:
            raise ValueError(f"Worker {worker.get('name', '<unknown>')} is missing required contract field: {field}")

    normalized = normalize_worker_contract(worker)

    name = normalized.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Worker contract name must be a non-empty string")

    execution_mode = normalized["execution_mode"]
    if execution_mode not in ALLOWED_EXECUTION_MODES:
        raise ValueError(
            f"Worker {name} has unsupported execution_mode: {execution_mode}"
        )

    workflow = normalized["workflow"]
    if not isinstance(workflow, str) or not workflow.startswith(".github/workflows/"):
        raise ValueError(f"Worker {name} must declare a workflow under .github/workflows/")

    summary_label = normalized["summary_label"]
    if not isinstance(summary_label, str) or not summary_label.strip():
        raise ValueError(f"Worker {name} must declare a non-empty summary_label")

    command = normalized.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError(f"Worker {name} command must remain a non-empty string list")

    for field in LIST_CONTRACT_FIELDS:
        raw_value = worker[field]
        value = normalized[field]
        if not isinstance(raw_value, list) or not isinstance(value, list):
            raise ValueError(f"Worker {name} field {field} must be a list of non-empty strings")
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError(f"Worker {name} field {field} must be a list of non-empty strings")


def extract_contract_metadata(worker: dict[str, Any]) -> dict[str, Any]:
    """Return only the persisted contract metadata fields for one worker."""
    normalized = normalize_worker_contract(worker)
    return {field: normalized[field] for field in CONTRACT_METADATA_FIELDS}


def worker_contracts_by_name(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return validated worker contracts keyed by worker name."""
    workers = registry.get("workers", [])
    if not isinstance(workers, list):
        raise ValueError("Registry workers must be a list")

    contracts: dict[str, dict[str, Any]] = {}
    for worker in workers:
        if not isinstance(worker, dict):
            raise ValueError("Registry worker entries must be objects")
        validate_worker_contract(worker)
        normalized = normalize_worker_contract(worker)
        name = normalized["name"]
        if name in contracts:
            raise ValueError(f"Duplicate worker contract: {name}")
        contracts[name] = normalized
    return contracts
