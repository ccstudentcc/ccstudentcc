from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from readme_utils import update_readme_section


README_PATH = Path("README.md")
START_MARKER = "<!--START_SECTION:waka-->"
END_MARKER = "<!--END_SECTION:waka-->"
WAKATIME_URL = "https://api.wakatime.com/api/v1/users/current/stats/last_7_days"


def fetch_stats(api_key: str) -> dict:
    auth = base64.b64encode(api_key.encode("utf-8")).decode("ascii")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {auth}",
        "User-Agent": "workflow-manager"
    }
    request = urllib.request.Request(WAKATIME_URL, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def build_stats_block(payload: dict) -> str:
    data = payload.get("data", {})
    languages = data.get("languages", [])[:5]
    total = data.get("human_readable_total_including_other_language") or data.get("human_readable_total") or "0 secs"
    average = data.get("human_readable_daily_average_including_other_language") or data.get("human_readable_daily_average") or "0 secs"

    lines = [
        "```text",
        f"Total Time: {total}",
        f"Daily Average: {average}",
        ""
    ]

    if not languages:
        lines.append("No activity tracked yet")
    else:
        width = max(len(language.get("name", "")) for language in languages)
        for language in languages:
            name = language.get("name", "Unknown")
            text = language.get("text") or language.get("digital") or "0 mins"
            percent = language.get("percent", 0)
            lines.append(f"{name.ljust(width)}  {text.ljust(12)}  {percent:>5.1f}%")

    lines.append("```")
    return "\n".join(lines)


def main() -> None:
    api_key = os.getenv("WAKATIME_API_KEY")
    if not api_key:
        raise RuntimeError("WAKATIME_API_KEY is not configured")

    try:
        payload = fetch_stats(api_key)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WakaTime API request failed: {error.code} {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"WakaTime API request failed: {error.reason}") from error

    block = build_stats_block(payload)
    update_readme_section(README_PATH, START_MARKER, END_MARKER, block)
    print("Updated WakaTime section")


if __name__ == "__main__":
    main()