from __future__ import annotations

"""Unit tests for workflow runtime scheduling and health computation.

Covers health computation, ready task collection, and recovery paths.
"""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workflow_runtime import collect_ready_tasks, compute_health  # type: ignore[import-not-found]


class WorkflowRuntimeTests(unittest.TestCase):
    """Test health computation and scheduler ready-task recovery."""
    def test_compute_health_returns_unknown_for_invalid_heartbeat(self) -> None:
        """Return Unknown health when heartbeat timestamps are invalid."""
        worker_state = {"last_heartbeat_at": "not-a-valid-timestamp"}

        self.assertEqual(compute_health(worker_state, grace_seconds=120), "Unknown")

    def test_collect_ready_tasks_recovers_invalid_scheduled_at(self) -> None:
        """Recover invalid scheduled_at values while collecting ready tasks."""
        task_specs = {
            "snapshot": {
                "name": "snapshot",
                "depends_on": [],
                "condition": "always",
            }
        }
        state = {
            "tasks": {
                "snapshot": {
                    "status": "Pending",
                    "worker": "snapshot",
                    "scheduled_at": "bad-time-format",
                    "priority": 10,
                    "message": "Queued by scheduler",
                }
            },
            "workers": {
                "snapshot": {
                    "enabled": True,
                }
            },
        }

        ready = collect_ready_tasks(task_specs, state)

        self.assertEqual(ready, ["snapshot"])
        self.assertEqual(state["tasks"]["snapshot"]["status"], "Pending")
        self.assertEqual(state["tasks"]["snapshot"]["message"], "Ready for scheduler dispatch")
        self.assertNotEqual(state["tasks"]["snapshot"]["scheduled_at"], "bad-time-format")


if __name__ == "__main__":
    unittest.main()
