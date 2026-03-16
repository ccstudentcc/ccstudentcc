from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workflow_state import build_persist_signature  # type: ignore[import-not-found]


class WorkflowStatePersistenceTests(unittest.TestCase):
    def test_build_persist_signature_ignores_running_heartbeat_noise(self) -> None:
        state = {
            "workflow": {"status": "Running"},
            "managed_jobs": ["snapshot"],
            "standalone_jobs": ["featured-projects"],
            "scheduler": {
                "ready_queue": [],
                "deferred_tasks": 0,
                "running_tasks": 1,
                "completed_tasks": 0,
            },
            "message_queue": {
                "current_depth": 0,
                "total_dispatched": 1,
                "total_completed": 0,
                "retry_entries": 0,
                "running_leases": 1,
            },
            "event_bus": {
                "published_events": 1,
                "recent_events": [{"type": "task.dispatched"}],
            },
            "worker_pools": {
                "content-pool": {
                    "desired_workers": 1,
                    "active_workers": 1,
                    "queued_tasks": 0,
                    "completed_tasks": 0,
                    "last_scale_reason": "queue 0, active 1",
                }
            },
            "workers": {
                "snapshot": {
                    "enabled": True,
                    "health": "Healthy",
                    "managed_by_default": True,
                    "last_exit_code": None,
                },
                "featured-projects": {
                    "enabled": True,
                    "health": "Unknown",
                    "managed_by_default": False,
                    "last_exit_code": None,
                },
            },
            "tasks": {
                "snapshot": {
                    "status": "Running",
                    "attempt": 1,
                    "scheduled_at": "2026-03-16T08:00:00Z",
                    "message": "Heartbeat OK on pool content-pool (attempt 1/2)",
                }
            },
        }
        noisy_state = copy.deepcopy(state)
        noisy_state["tasks"]["snapshot"]["message"] = "Heartbeat OK on pool content-pool (attempt 1/2)"
        noisy_state["tasks"]["snapshot"]["updated_at"] = "2026-03-16T08:00:02Z"
        noisy_state["workers"]["snapshot"]["last_heartbeat_at"] = "2026-03-16T08:00:02Z"

        self.assertEqual(build_persist_signature(state, []), build_persist_signature(noisy_state, []))

    def test_build_persist_signature_changes_when_task_status_changes(self) -> None:
        state = {
            "workflow": {"status": "Running"},
            "managed_jobs": ["snapshot"],
            "standalone_jobs": [],
            "scheduler": {
                "ready_queue": [],
                "deferred_tasks": 0,
                "running_tasks": 1,
                "completed_tasks": 0,
            },
            "message_queue": {
                "current_depth": 0,
                "total_dispatched": 1,
                "total_completed": 0,
                "retry_entries": 0,
                "running_leases": 1,
            },
            "event_bus": {
                "published_events": 1,
                "recent_events": [{"type": "task.dispatched"}],
            },
            "worker_pools": {},
            "workers": {
                "snapshot": {
                    "enabled": True,
                    "health": "Healthy",
                    "managed_by_default": True,
                    "last_exit_code": None,
                }
            },
            "tasks": {
                "snapshot": {
                    "status": "Running",
                    "attempt": 1,
                    "scheduled_at": "2026-03-16T08:00:00Z",
                    "message": "Heartbeat OK on pool content-pool (attempt 1/2)",
                }
            },
        }
        completed_state = copy.deepcopy(state)
        completed_state["workflow"]["status"] = "Completed"
        completed_state["tasks"]["snapshot"]["status"] = "Success"
        completed_state["tasks"]["snapshot"]["message"] = "Updated recent repository snapshot"
        completed_state["message_queue"]["total_completed"] = 1
        completed_state["scheduler"]["running_tasks"] = 0
        completed_state["scheduler"]["completed_tasks"] = 1

        self.assertNotEqual(build_persist_signature(state, []), build_persist_signature(completed_state, []))


if __name__ == "__main__":
    unittest.main()
