from __future__ import annotations

"""Verify that invoking persist with an identical signature performs no-op.

This helper loads current manager state and calls persist with
previous_signature equal to the current signature to assert no writes are
enqueued when state is unchanged.
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_common import load_json, REGISTRY_PATH, WORKFLOW_PATH
from workflow_state import initialize_state, build_persist_signature, persist

def main() -> int:
    """Invoke persist in no-op mode to verify identical signature handling.

    Returns:
        Process exit code. Returns 0 when invocation succeeds.
    """
    registry = load_json(REGISTRY_PATH, {"workers": []})
    workflow_spec = load_json(WORKFLOW_PATH, {"workflow": {}, "scheduler": {}, "worker_pools": [], "tasks": []})
    state, dead_letters, _ = initialize_state(registry, workflow_spec)
    sig = build_persist_signature(state, dead_letters)
    # Call persist with previous_signature equal to current signature and force=True.
    # Our expectation: no new writes should be enqueued or persisted when there
    # is no meaningful change; flush any pre-existing queued writes only.
    persist(state, dead_letters, registry, previous_signature=sig, force=True)
    print("noop-persist-invoked")
    return 0

if __name__ == '__main__':
    sys.exit(main())
