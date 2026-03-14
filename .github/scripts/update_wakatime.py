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


def _badge_url(label: str, message: str, color: str, logo: str | None = None) -> str:
    # Use query-string API to avoid path-separator parsing issues (e.g. '-' in timestamps).
    encoded_label = urllib.parse.quote(label, safe="")
    encoded_message = urllib.parse.quote(message, safe="")
    encoded_color = urllib.parse.quote(color, safe="")
    url = (
        "https://img.shields.io/static/v1"
        f"?label={encoded_label}"
        f"&message={encoded_message}"
        f"&color={encoded_color}"
        "&style=for-the-badge"
    )
    if logo:
        url += f"&logo={urllib.parse.quote(logo, safe='')}"
    return url


def _render_ranked_lines(items: list[dict], label: str, limit: int = 5) -> list[str]:
    rows = items[:limit]
    if not rows:
        return []

    lines = [f"{label}:"]

    width = max(len((row.get("name") or "Unknown")) for row in rows)
    for row in rows:
        name = row.get("name") or "Unknown"
        text = row.get("text") or row.get("digital") or "0 mins"
        percent = float(row.get("percent", 0) or 0)
        bar = _progress_bar(percent)
        lines.append(f"  {name.ljust(width)}  {text.ljust(12)}  [{bar}] {percent:5.1f}%")
    lines.append("")
    return lines


def _top_item(items: list[dict]) -> tuple[str, str, float]:
    if not items:
        return "N/A", "0 mins", 0.0
    item = items[0]
    name = item.get("name") or "Unknown"
    text = item.get("text") or item.get("digital") or "0 mins"
    percent = float(item.get("percent", 0) or 0)
    return name, text, percent


def build_stats_block(payload: dict) -> str:
    data = payload.get("data", {})
    languages = data.get("languages", [])[:5]
    editors = data.get("editors", [])[:5]
    projects = data.get("projects", [])[:5]
    operating_systems = data.get("operating_systems", [])[:5]
    total = _get_total_text(data)
    average = _get_average_text(data)
    synced_at = datetime.now(ASIA_SHANGHAI).replace(microsecond=0).strftime("%Y-%m-%d %H:%M CST")
    has_any_data = any([languages, editors, projects, operating_systems])
    top_lang_name, top_lang_time, top_lang_percent = _top_item(languages)
    top_project_name, top_project_time, top_project_percent = _top_item(projects)
    top_editor_name, _, _ = _top_item(editors)

    code_time_dark = _badge_url("Code Time", total, "334155", logo="wakatime")
    code_time_light = _badge_url("Code Time", total, "2563eb", logo="wakatime")
    average_dark = _badge_url("Daily Average", average, "475569")
    average_light = _badge_url("Daily Average", average, "0f172a")
    sync_dark = _badge_url("Last Sync", synced_at, "1e293b")
    sync_light = _badge_url("Last Sync", synced_at, "1d4ed8")
    top_lang_dark = _badge_url("Top Language", top_lang_name, "0f766e")
    top_lang_light = _badge_url("Top Language", top_lang_name, "0d9488")
    top_project_dark = _badge_url("Top Project", top_project_name, "4c1d95")
    top_project_light = _badge_url("Top Project", top_project_name, "6d28d9")

    header_lines = [
        '<div align="center">',
        "",
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="{code_time_dark}" />',
        f'  <img src="{code_time_light}" alt="code time" />',
        "</picture>",
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="{average_dark}" />',
        f'  <img src="{average_light}" alt="daily average" />',
        "</picture>",
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="{sync_dark}" />',
        f'  <img src="{sync_light}" alt="last sync" />',
        "</picture>",
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="{top_lang_dark}" />',
        f'  <img src="{top_lang_light}" alt="top language" />',
        "</picture>",
        "<picture>",
        f'  <source media="(prefers-color-scheme: dark)" srcset="{top_project_dark}" />',
        f'  <img src="{top_project_light}" alt="top project" />',
        "</picture>",
        "",
        f"<sub>Focus: {top_lang_name} ({top_lang_time}, {top_lang_percent:.1f}%) | Project: {top_project_name} ({top_project_time}, {top_project_percent:.1f}%) | Editor: {top_editor_name}</sub>",
        "",
        "</div>",
        "",
        "<details>",
        "<summary><b>Weekly Breakdown | 本周明细</b></summary>",
        "",
        "```text",
        "Timezone: Asia/Shanghai (UTC+8)",
        f"Updated At (CST): {synced_at}",
        ""
    ]

    body_lines: list[str] = []
    body_lines.extend(_render_ranked_lines(languages, "Languages"))
    body_lines.extend(_render_ranked_lines(editors, "Editors"))
    body_lines.extend(_render_ranked_lines(projects, "Projects"))
    body_lines.extend(_render_ranked_lines(operating_systems, "Operating Systems"))

    if not has_any_data:
        body_lines.append("No activity tracked yet")

    body_lines.append("Generated by workflow-manager")
    body_lines.append("```")
    body_lines.append("")
    body_lines.append("</details>")
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