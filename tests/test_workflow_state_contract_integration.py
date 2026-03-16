from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workflow_state import initialize_state  # type: ignore[import-not-found]


class WorkflowStateContractIntegrationTests(unittest.TestCase):
    def test_initialize_state_rejects_worker_with_invalid_contract(self) -> None:
        registry = {
            "workers": [
                {
                    "name": "daily-quote",
                    "display_name": "Daily Quote",
                    "enabled": True,
                    "worker_type": "engagement-sync",
                    "pool": "engagement-pool",
                    "capabilities": ["readme-write"],
                    "timeout_seconds": 60,
                    "max_retries": 1,
                    "retry_backoff_seconds": 5,
                    "heartbeat_grace_seconds": 120,
                    "command": ["python", ".github/scripts/update_daily_quote.py"],
                    "execution_mode": "python",
                    "required_secrets": [],
                    "commit_scope": ["README.md"],
                    "optional_readme_markers": ["daily_quote"],
                    "summary_label": "Daily Quote",
                }
            ]
        }
        workflow_spec = {
            "workflow": {
                "name": "profile-readme-automation",
                "description": "DAG orchestrated README automation for profile content",
            },
            "scheduler": {
                "cron": "0 4,16 * * *",
                "priority_policy": "higher-first",
                "delay_strategy": "defer-until-ready",
                "parallel_execution": True,
            },
            "worker_pools": [
                {
                    "name": "engagement-pool",
                    "worker_type": "engagement-sync",
                    "min_workers": 1,
                    "max_workers": 1,
                    "queue_target_per_worker": 1,
                    "scale_metric": "queue_depth",
                    "capabilities": ["daily-quote"],
                }
            ],
            "tasks": [
                {
                    "name": "daily-quote",
                    "worker": "daily-quote",
                    "pool": "engagement-pool",
                    "priority": 30,
                    "depends_on": [],
                    "condition": "always",
                    "delay_seconds": 0,
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "workflow"):
            initialize_state(registry, workflow_spec)

    def test_initialize_state_persists_contract_metadata_for_worker_and_task(self) -> None:
        registry = {
            "workers": [
                {
                    "name": "snapshot",
                    "display_name": "Snapshot",
                    "enabled": True,
                    "worker_type": "content-sync",
                    "pool": "content-pool",
                    "capabilities": ["readme-write", "repo-discovery"],
                    "timeout_seconds": 180,
                    "max_retries": 1,
                    "retry_backoff_seconds": 5,
                    "heartbeat_grace_seconds": 240,
                    "command": ["python", ".github/scripts/update_snapshot.py"],
                    "execution_mode": "python",
                    "workflow": ".github/workflows/snapshot.yml",
                    "required_secrets": ["GITHUB_TOKEN"],
                    "commit_scope": ["README.md", "assets/showcase-carousel.svg"],
                    "optional_readme_markers": [
                        "recent_repos",
                        "realtime_panel",
                        "showcase_slides",
                        "hero_subtitle",
                    ],
                    "summary_label": "Snapshot",
                }
            ]
        }
        workflow_spec = {
            "workflow": {
                "name": "profile-readme-automation",
                "description": "DAG orchestrated README automation for profile content",
            },
            "scheduler": {
                "cron": "0 4,16 * * *",
                "priority_policy": "higher-first",
                "delay_strategy": "defer-until-ready",
                "parallel_execution": True,
            },
            "worker_pools": [
                {
                    "name": "content-pool",
                    "worker_type": "content-sync",
                    "min_workers": 1,
                    "max_workers": 2,
                    "queue_target_per_worker": 1,
                    "scale_metric": "queue_depth",
                    "capabilities": ["snapshot"],
                }
            ],
            "tasks": [
                {
                    "name": "snapshot",
                    "worker": "snapshot",
                    "pool": "content-pool",
                    "priority": 60,
                    "depends_on": [],
                    "condition": "always",
                    "delay_seconds": 0,
                }
            ],
        }

        state, dead_letters, task_specs = initialize_state(registry, workflow_spec)

        self.assertEqual(dead_letters, [])
        self.assertEqual([task["name"] for task in task_specs], ["snapshot"])
        self.assertEqual(
            state["workers"]["snapshot"]["contract"],
            {
                "execution_mode": "python",
                "workflow": ".github/workflows/snapshot.yml",
                "required_secrets": ["GITHUB_TOKEN"],
                "commit_scope": ["README.md", "assets/showcase-carousel.svg"],
                "optional_readme_markers": [
                    "recent_repos",
                    "realtime_panel",
                    "showcase_slides",
                    "hero_subtitle",
                ],
                "summary_label": "Snapshot",
            },
        )
        self.assertEqual(
            state["tasks"]["snapshot"]["contract"],
            {
                "execution_mode": "python",
                "workflow": ".github/workflows/snapshot.yml",
                "required_secrets": ["GITHUB_TOKEN"],
                "commit_scope": ["README.md", "assets/showcase-carousel.svg"],
                "optional_readme_markers": [
                    "recent_repos",
                    "realtime_panel",
                    "showcase_slides",
                    "hero_subtitle",
                ],
                "summary_label": "Snapshot",
            },
        )


if __name__ == "__main__":
    unittest.main()
