from __future__ import annotations

"""Run benchmark worker with batching enabled/disabled and print results."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
worker = ROOT / "scripts" / "benchmarks" / "_benchmark_worker.py"


def run(mode: str) -> dict:
    """Execute the benchmark worker once for a batching mode.
    
    Args:
        mode: Value for WORKFLOW_WRITE_BATCHING.
    
    Returns:
        Parsed benchmark report or an error payload.
    """
    env = os.environ.copy()
    env["WORKFLOW_WRITE_BATCHING"] = mode
    env["BENCH_ITERATIONS"] = env.get("BENCH_ITERATIONS", "50")
    env["BENCH_SLEEP_BETWEEN"] = env.get("BENCH_SLEEP_BETWEEN", "0.1")

    proc = subprocess.run([sys.executable, str(worker)], env=env, capture_output=True, text=True)
    out = proc.stdout.strip() or proc.stderr.strip()
    try:
        return json.loads(out)
    except Exception:
        return {"error": out}


def main() -> int:
    """Run benchmark worker with batching disabled and enabled.
    
    Returns:
        Process exit code.
    """
    print("Running benchmark with batching=disabled")
    res_false = run("false")
    print(json.dumps(res_false, indent=2, ensure_ascii=False))

    print("\nRunning benchmark with batching=enabled")
    res_true = run("true")
    print(json.dumps(res_true, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
