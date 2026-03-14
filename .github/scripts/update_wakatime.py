from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from readme_utils import update_readme_section


README_PATH = Path("README.md")
START_MARKER = "<!--START_SECTION:waka-->"
END_MARKER = "<!--END_SECTION:waka-->"
WAKATIME_URL = "https://api.wakatime.com/api/v1/users/current/stats/last_7_days"
ASIA_SHANGHAI = timezone(timedelta(hours=8))


def _request_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_stats(api_key: str) -> dict:
    # WakaTime Basic auth uses "api_key:" as credentials.
    clean_key = api_key.strip()
    auth = base64.b64encode(f"{clean_key}:".encode("utf-8")).decode("ascii")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {auth}",
        "User-Agent": "workflow-manager"
    }
    try:
        payload = _request_json(WAKATIME_URL, headers)
    except urllib.error.HTTPError as error:
        if error.code not in (401, 403):
            raise
        # Fallback path for environments where proxy/auth middlewares reject Basic auth.
        fallback_url = f"{WAKATIME_URL}?api_key={urllib.parse.quote(clean_key)}"
        payload = _request_json(fallback_url, {"Accept": "application/json", "User-Agent": "workflow-manager"})

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected WakaTime response: payload is not a JSON object")
    if "error" in payload:
        raise RuntimeError(f"WakaTime API error: {payload['error']}")
    return payload


def _get_total_text(data: dict) -> str:
    return (
        data.get("human_readable_total_including_other_language")
        or data.get("human_readable_total")
        or data.get("grand_total", {}).get("text")
        or data.get("cumulative_total", {}).get("text")
        or "0 secs"
    )


def _get_average_text(data: dict) -> str:
    return (
        data.get("human_readable_daily_average_including_other_language")
        or data.get("human_readable_daily_average")
        or data.get("daily_average_including_other_language")
        or data.get("daily_average")
        or "0 secs"
    )


def _progress_bar(percent: float, width: int = 26) -> str:
    clamped = max(0.0, min(100.0, float(percent)))
    filled = int(round(clamped / 100.0 * width))
    return "#" * filled + "-" * (width - filled)


def _render_ranked_lines(items: list[dict], label: str, limit: int = 5) -> list[str]:
    rows = items[:limit]
    lines = [f"{label}:"]
    if not rows:
        lines.append("  No tracked data")
        lines.append("")
        return lines

    width = max(len((row.get("name") or "Unknown")) for row in rows)
    for row in rows:
        name = row.get("name") or "Unknown"
        text = row.get("text") or row.get("digital") or "0 mins"
        percent = float(row.get("percent", 0) or 0)
        bar = _progress_bar(percent)
        lines.append(f"  {name.ljust(width)}  {text.ljust(12)}  [{bar}] {percent:5.1f}%")
    lines.append("")
    return lines


def build_stats_block(payload: dict) -> str:
    data = payload.get("data", {})
    languages = data.get("languages", [])[:5]
    editors = data.get("editors", [])[:5]
    projects = data.get("projects", [])[:5]
    operating_systems = data.get("operating_systems", [])[:5]
    total = _get_total_text(data)
    average = _get_average_text(data)
    synced_at = datetime.now(ASIA_SHANGHAI).replace(microsecond=0).strftime("%Y-%m-%d %H:%M CST")

    header_lines = [
        '<div align="center">',
        "",
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/Code%20Time-{total.replace(" ", "%20")}-334155?style=for-the-badge&logo=wakatime" />',
        f'  <img src="https://img.shields.io/badge/Code%20Time-{total.replace(" ", "%20")}-2563eb?style=for-the-badge&logo=wakatime" alt="code time" />',
        "</picture>",
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/Daily%20Average-{average.replace(" ", "%20")}-475569?style=for-the-badge" />',
        f'  <img src="https://img.shields.io/badge/Daily%20Average-{average.replace(" ", "%20")}-0f172a?style=for-the-badge" alt="daily average" />',
        "</picture>",
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/Last%20Sync-{synced_at.replace(" ", "%20")}-1e293b?style=for-the-badge" />',
        f'  <img src="https://img.shields.io/badge/Last%20Sync-{synced_at.replace(" ", "%20")}-1d4ed8?style=for-the-badge" alt="last sync" />',
        "</picture>",
        "",
        "</div>",
        "",
        "```text",
        "Timezone: Asia/Shanghai (UTC+8)",
        ""
    ]

    body_lines: list[str] = []
    body_lines.extend(_render_ranked_lines(languages, "Languages"))
    body_lines.extend(_render_ranked_lines(editors, "Editors"))
    body_lines.extend(_render_ranked_lines(projects, "Projects"))
    body_lines.extend(_render_ranked_lines(operating_systems, "Operating Systems"))

    if not languages and not editors and not projects and not operating_systems:
        body_lines.append("No activity tracked yet")

    body_lines.append("```")
    return "\n".join(header_lines + body_lines)


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