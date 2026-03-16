from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workflow_contract import (  # type: ignore[import-not-found]
    normalize_worker_contract,
    validate_worker_contract,
    worker_contracts_by_name,
)


class WorkflowContractTests(unittest.TestCase):
    def test_normalize_worker_contract_populates_defaults(self) -> None:
        worker = {
            "name": "daily-quote",
            "command": ["python", ".github/scripts/update_daily_quote.py"],
            "execution_mode": "python",
            "workflow": ".github/workflows/daily-quote.yml",
            "required_secrets": [],
            "commit_scope": ["README.md"],
            "summary_label": "Daily Quote",
        }

        normalized = normalize_worker_contract(worker)

        self.assertEqual(normalized["name"], "daily-quote")
        self.assertEqual(normalized["execution_mode"], "python")
        self.assertEqual(normalized["workflow"], ".github/workflows/daily-quote.yml")
        self.assertEqual(normalized["required_secrets"], [])
        self.assertEqual(normalized["commit_scope"], ["README.md"])
        self.assertEqual(normalized["optional_readme_markers"], [])
        self.assertEqual(normalized["summary_label"], "Daily Quote")

    def test_validate_worker_contract_rejects_missing_required_field(self) -> None:
        worker = {
            "name": "wakatime",
            "command": ["python", ".github/scripts/update_wakatime.py"],
            "execution_mode": "python",
            "required_secrets": ["WAKATIME_API_KEY"],
            "commit_scope": ["README.md"],
            "optional_readme_markers": [],
            "summary_label": "WakaTime",
        }

        with self.assertRaisesRegex(ValueError, "workflow"):
            validate_worker_contract(worker)

    def test_validate_worker_contract_rejects_missing_required_secrets_field(self) -> None:
        worker = {
            "name": "daily-quote",
            "command": ["python", ".github/scripts/update_daily_quote.py"],
            "execution_mode": "python",
            "workflow": ".github/workflows/daily-quote.yml",
            "commit_scope": ["README.md"],
            "optional_readme_markers": [],
            "summary_label": "Daily Quote",
        }

        with self.assertRaisesRegex(ValueError, "required_secrets"):
            validate_worker_contract(worker)

    def test_validate_worker_contract_rejects_string_commit_scope(self) -> None:
        worker = {
            "name": "snapshot",
            "command": ["python", ".github/scripts/update_snapshot.py"],
            "execution_mode": "python",
            "workflow": ".github/workflows/snapshot.yml",
            "required_secrets": ["GITHUB_TOKEN"],
            "commit_scope": "README.md",
            "optional_readme_markers": ["recent_repos"],
            "summary_label": "Snapshot",
        }

        with self.assertRaisesRegex(ValueError, "commit_scope"):
            validate_worker_contract(worker)

    def test_worker_contracts_by_name_normalizes_each_registry_worker(self) -> None:
        registry = {
            "workers": [
                {
                    "name": "snapshot",
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

        contracts = worker_contracts_by_name(registry)

        self.assertEqual(list(contracts.keys()), ["snapshot"])
        self.assertEqual(contracts["snapshot"]["summary_label"], "Snapshot")
        self.assertEqual(
            contracts["snapshot"]["optional_readme_markers"],
            ["recent_repos", "realtime_panel", "showcase_slides", "hero_subtitle"],
        )


if __name__ == "__main__":
    unittest.main()
