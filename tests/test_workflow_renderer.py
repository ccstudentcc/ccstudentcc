from __future__ import annotations

"""Tests for README automation renderer functions.

Validates rendering of task/worker summaries and dead-letter redaction.
"""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workflow_renderer import render_dead_letters, render_task_state, render_worker_registry  # type: ignore[import-not-found]


class WorkflowRendererTests(unittest.TestCase):
    """Test task, worker, and dead-letter rendering behavior."""
    def test_render_task_state_summarizes_optional_marker_skip_message(self) -> None:
        """Summarize optional-marker skip messages without leaking marker text."""
        state = {
            "tasks": {
                "featured-projects": {
                    "status": "Skipped",
                    "priority": 90,
                    "attempt": 1,
                    "max_attempts": 2,
                    "pool": "content-pool",
                    "updated_at": "2026-03-16T08:00:00Z",
                    "message": "Skipped README update because markers were not found: <!--START_SECTION:featured--> / <!--END_SECTION:featured-->",
                }
            }
        }

        rendered = render_task_state(state)

        self.assertIn("Optional README marker missing", rendered)
        self.assertNotIn("markers were not found", rendered)
        self.assertNotIn("<!--START_SECTION:featured-->", rendered)

    def test_render_worker_registry_marks_manual_only_workers(self) -> None:
        """Mark workers as managed or manual-only in registry rendering."""
        state = {
            "workers": {
                "snapshot": {
                    "enabled": True,
                    "worker_type": "content-sync",
                    "pool": "content-pool",
                    "display_name": "Snapshot",
                    "capabilities": ["readme-write"],
                    "managed_by_default": True,
                },
                "featured-projects": {
                    "enabled": True,
                    "worker_type": "content-sync",
                    "pool": "content-pool",
                    "display_name": "Featured Projects",
                    "capabilities": ["readme-write"],
                    "managed_by_default": False,
                },
            }
        }

        rendered = render_worker_registry(state)

        self.assertIn("managed", rendered)
        self.assertIn("manual-only", rendered)
    def test_render_dead_letters_summarizes_traceback_reason(self) -> None:
        """Summarize traceback-based dead-letter reasons for display safety."""
        dead_letters = [
            {
                "task": "snapshot",
                "attempts": 2,
                "failed_at": "2026-03-16T10:07:02Z",
                "reason": "Traceback (most recent call last):\n  File \"update_snapshot.py\", line 1, in <module>\n    main()",
            }
        ]

        rendered = render_dead_letters(dead_letters)

        self.assertIn("Worker failed; inspect event log or workflow run for the full traceback", rendered)
        self.assertNotIn("Traceback (most recent call last)", rendered)


if __name__ == "__main__":
    unittest.main()
