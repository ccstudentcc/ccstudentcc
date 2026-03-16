from __future__ import annotations

"""Worker process that runs multiple persist cycles and counts actual JSON writes.

Run as a separate process so `WORKFLOW_WRITE_BATCHING` can be toggled via env.
"""

import os
import sys
import time
from pathlib import Path

import json

# Ensure repo root is on sys.path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))
sys.path.insert(0, str(ROOT))

import workflow_common as wc  # type: ignore
from workflow_state import initialize_state, persist  # type: ignore


def main() -> int:
    """Run persist benchmark iterations and report JSON write counts.
    
    Returns:
        Process exit code.
    """
    iterations = int(os.environ.get("BENCH_ITERATIONS", "50"))
    sleep_between = float(os.environ.get("BENCH_SLEEP_BETWEEN", "0.1"))

    # Monkeypatch workflow_common.save_json to count actual writes
    original_save = wc.save_json

    counter = {"writes": 0}

    def counting_save(path, payload):
        """Count save operations while delegating to the original saver.
        
        Args:
            path: Target JSON path.
            payload: JSON-serializable payload.
        
        Returns:
            Result returned by the original save function.
        """
        counter["writes"] += 1
        return original_save(path, payload)

    wc.save_json = counting_save

    # Load inputs
    workflow_path = ROOT / ".github" / "manager" / "workflow.json"
    registry_path = ROOT / ".github" / "manager" / "registry.json"

    workflow_spec = json.loads(workflow_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    state, dead_letters, _ = initialize_state(registry, workflow_spec)

    # perform iterations
    start = time.time()
    prev_sig = None
    for i in range(iterations):
        # Force persistence each iteration to measure write behavior
        sig = persist(state, dead_letters, registry, previous_signature=prev_sig, force=True)
        prev_sig = sig
        time.sleep(sleep_between)

    elapsed = time.time() - start

    # Ensure any queued writes are flushed at the end for accurate counting
    try:
        wc.flush_json_writes(force=True)
    except Exception:
        pass

    report = {
        "env_batching": os.environ.get("WORKFLOW_WRITE_BATCHING", "false"),
        "iterations": iterations,
        "sleep_between": sleep_between,
        "elapsed_seconds": elapsed,
        "save_json_calls": counter["writes"],
    }

    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
