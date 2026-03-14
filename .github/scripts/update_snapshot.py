from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from readme_utils import update_readme_section


README_PATH = Path("README.md")
START_MARKER = "<!--START_SECTION:recent_repos-->"
END_MARKER = "<!--END_SECTION:recent_repos-->"


def github_request(url: str) -> list[dict]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "workflow-manager"
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def format_repo_line(owner: str, repo: dict) -> str:
    return (
        f"- [{repo['name']}](https://github.com/{owner}/{repo['name']})"
        f" - Updated: {repo['pushed_at'].replace('T', ' ').replace('Z', ' UTC')}"
    )


def main() -> None:
    owner = os.getenv("GITHUB_REPOSITORY_OWNER", "ccstudentcc")
    url = f"https://api.github.com/users/{owner}/repos?sort=updated&per_page=100"
    repos = github_request(url)

    filtered = [
        repo for repo in repos
        if not repo.get("fork") and not repo.get("archived") and repo.get("name") != owner
    ]
    filtered.sort(key=lambda item: item.get("pushed_at", ""), reverse=True)
    selected = filtered[:5]

    if not selected:
        block = "- No repositories found."
    else:
        block = "\n".join(format_repo_line(owner, repo) for repo in selected)

    update_readme_section(README_PATH, START_MARKER, END_MARKER, block)
    print(f"Updated recent repository snapshot with {len(selected)} entries")


if __name__ == "__main__":
    main()