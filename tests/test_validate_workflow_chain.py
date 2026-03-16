from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_workflow_chain import (  # type: ignore[import-not-found]
    ValidationError,
    validate_readme_and_docs,
    validate_registry_worker_workflows,
    validate_worker_workflow_wrapper,
)


class WorkflowChainContractValidationTests(unittest.TestCase):
    def test_validate_worker_workflow_wrapper_accepts_python_worker_contract(self) -> None:
        contract = {
            "name": "snapshot",
            "command": ["python", ".github/scripts/update_snapshot.py"],
            "execution_mode": "python",
            "workflow": ".github/workflows/snapshot.yml",
            "required_secrets": ["GITHUB_TOKEN"],
            "commit_scope": ["README.md", "assets/showcase-carousel.svg"],
            "optional_readme_markers": ["recent_repos"],
            "summary_label": "Snapshot",
        }
        workflow_text = """name: Snapshot

on:
  workflow_call:
  workflow_dispatch:

jobs:
  update-snapshot:
    uses: ./.github/workflows/_managed-readme-worker.yml
    with:
      execution_mode: python
      command: python .github/scripts/update_snapshot.py
      summary_label: Snapshot
      commit_scope: README.md assets/showcase-carousel.svg
      required_secrets: GITHUB_TOKEN
    secrets:
      repo_token: ${{ secrets.GITHUB_TOKEN }}
"""

        validate_worker_workflow_wrapper(contract, workflow_text, Path("snapshot.yml"))

    def test_validate_worker_workflow_wrapper_accepts_bash_worker_shell_command(self) -> None:
        contract = {
            "name": "featured-projects",
            "command": ["bash", ".github/scripts/update_featured_projects.sh"],
            "execution_mode": "bash",
            "workflow": ".github/workflows/featured-projects.yml",
            "required_secrets": ["GITHUB_TOKEN"],
            "commit_scope": ["README.md"],
            "optional_readme_markers": ["featured"],
            "summary_label": "Featured Projects",
        }
        workflow_text = """name: Update Featured Projects

on:
  workflow_call:
  workflow_dispatch:

jobs:
  update-featured-projects:
    uses: ./.github/workflows/_managed-readme-worker.yml
    with:
      execution_mode: bash
      command: .github/scripts/update_featured_projects.sh
      summary_label: Featured Projects
      commit_scope: README.md
      required_secrets: GITHUB_TOKEN
    secrets:
      repo_token: ${{ secrets.GITHUB_TOKEN }}
"""

        validate_worker_workflow_wrapper(contract, workflow_text, Path("featured-projects.yml"))

    def test_validate_worker_workflow_wrapper_rejects_execution_mode_drift(self) -> None:
        contract = {
            "name": "daily-quote",
            "command": ["python", ".github/scripts/update_daily_quote.py"],
            "execution_mode": "python",
            "workflow": ".github/workflows/daily-quote.yml",
            "required_secrets": [],
            "commit_scope": ["README.md"],
            "optional_readme_markers": ["daily_quote"],
            "summary_label": "Daily Quote",
        }
        workflow_text = """jobs:
  update-daily-quote:
    uses: ./.github/workflows/_managed-readme-worker.yml
    with:
      execution_mode: bash
      command: python .github/scripts/update_daily_quote.py
      summary_label: Daily Quote
      commit_scope: README.md
      required_secrets: 'None'
"""

        with self.assertRaisesRegex(ValidationError, "execution_mode"):
            validate_worker_workflow_wrapper(contract, workflow_text, Path("daily-quote.yml"))

    def test_validate_worker_workflow_wrapper_rejects_missing_secret_reference(self) -> None:
        contract = {
            "name": "wakatime",
            "command": ["python", ".github/scripts/update_wakatime.py"],
            "execution_mode": "python",
            "workflow": ".github/workflows/wakatime.yml",
            "required_secrets": ["WAKATIME_API_KEY"],
            "commit_scope": ["README.md"],
            "optional_readme_markers": ["waka"],
            "summary_label": "WakaTime",
        }
        workflow_text = """on:
  workflow_call:
    secrets:
      WAKATIME_API_KEY:
        required: true
jobs:
  update-readme:
    uses: ./.github/workflows/_managed-readme-worker.yml
    with:
      execution_mode: python
      command: python .github/scripts/update_wakatime.py
      summary_label: WakaTime
      commit_scope: README.md
      required_secrets: WAKATIME_API_KEY
"""

        with self.assertRaisesRegex(ValidationError, "WAKATIME_API_KEY"):
            validate_worker_workflow_wrapper(contract, workflow_text, Path("wakatime.yml"))

    def test_validate_worker_workflow_wrapper_rejects_reserved_github_token_declaration(self) -> None:
        contract = {
            "name": "snapshot",
            "command": ["python", ".github/scripts/update_snapshot.py"],
            "execution_mode": "python",
            "workflow": ".github/workflows/snapshot.yml",
            "required_secrets": ["GITHUB_TOKEN"],
            "commit_scope": ["README.md", "assets/showcase-carousel.svg"],
            "optional_readme_markers": ["recent_repos"],
            "summary_label": "Snapshot",
        }
        workflow_text = """name: Snapshot

on:
  workflow_call:
    secrets:
      GITHUB_TOKEN:
        required: true
  workflow_dispatch:

jobs:
  update-snapshot:
    uses: ./.github/workflows/_managed-readme-worker.yml
    with:
      execution_mode: python
      command: python .github/scripts/update_snapshot.py
      summary_label: Snapshot
      commit_scope: README.md assets/showcase-carousel.svg
      required_secrets: GITHUB_TOKEN
    secrets:
      repo_token: ${{ secrets.GITHUB_TOKEN }}
"""

        with self.assertRaisesRegex(ValidationError, "不应在 workflow_call.secrets 中声明保留 secret"):
            validate_worker_workflow_wrapper(contract, workflow_text, Path("snapshot.yml"))

    def test_validate_registry_worker_workflows_rejects_missing_workflow_file(self) -> None:
        registry = {
            "workers": [
                {
                    "name": "snapshot",
                    "command": ["python", ".github/scripts/update_snapshot.py"],
                    "execution_mode": "python",
                    "workflow": ".github/workflows/missing.yml",
                    "required_secrets": ["GITHUB_TOKEN"],
                    "commit_scope": ["README.md"],
                    "optional_readme_markers": ["recent_repos"],
                    "summary_label": "Snapshot",
                }
            ]
        }

        with self.assertRaisesRegex(ValidationError, "missing.yml"):
            validate_registry_worker_workflows(registry, Path(__file__).resolve().parents[1])
    def test_validate_readme_and_docs_rejects_duplicate_markers(self) -> None:
        readme_text = (
            "Orchestrator • DAG • Scheduler • Queue • State Store • Event Bus • Worker Pools • Registry • Health • Tasks • DLQ\n"
            "<!--START_SECTION:automation_status-->\n"
            "<!--END_SECTION:automation_status-->\n"
            "<!--START_SECTION:automation_status-->\n"
            "<!--END_SECTION:automation_status-->\n"
            "<!--START_SECTION:workflow_dag-->\n<!--END_SECTION:workflow_dag-->\n"
            "<!--START_SECTION:scheduler_state-->\n<!--END_SECTION:scheduler_state-->\n"
            "<!--START_SECTION:message_queue-->\n<!--END_SECTION:message_queue-->\n"
            "<!--START_SECTION:state_store-->\n<!--END_SECTION:state_store-->\n"
            "<!--START_SECTION:event_bus-->\n<!--END_SECTION:event_bus-->\n"
            "<!--START_SECTION:worker_pools-->\n<!--END_SECTION:worker_pools-->\n"
            "<!--START_SECTION:worker_registry-->\n<!--END_SECTION:worker_registry-->\n"
            "<!--START_SECTION:worker_health-->\n<!--END_SECTION:worker_health-->\n"
            "<!--START_SECTION:task_state-->\n<!--END_SECTION:task_state-->\n"
            "<!--START_SECTION:dead_letters-->\n<!--END_SECTION:dead_letters-->\n"
        )
        doc_text = "Orchestrator DAG Scheduler Queue State Store Event Bus Worker Pools Registry Health Tasks DLQ"

        with self.assertRaisesRegex(ValidationError, "marker 必须唯一"):
            validate_readme_and_docs(readme_text, doc_text)


if __name__ == "__main__":
    unittest.main()
