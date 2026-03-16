from __future__ import annotations

"""Fetch WakaTime stats and render the README WakaTime section."""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from readme_utils import update_readme_section


README_PATH = Path("README.md")
START_MARKER = "<!--START_SECTION:waka-->"
END_MARKER = "<!--END_SECTION:waka-->"
WAKATIME_SUMMARIES_BASE_URL = "https://api.wakatime.com/api/v1/users/current/summaries"
WAKATIME_STATS_URL = "https://api.wakatime.com/api/v1/users/current/stats/last_7_days"
WAKATIME_ALL_TIME_URL = "https://api.wakatime.com/api/v1/users/current/all_time_since_today"
WAKATIME_STATUS_BAR_TODAY_URL = "https://api.wakatime.com/api/v1/users/current/status_bar/today"
ASIA_SHANGHAI = timezone(timedelta(hours=8))


def _request_json(url: str, headers: dict[str, str]) -> dict:
    """Issue HTTP request and parse JSON response.

    Args:
        url: Request URL.
        headers: HTTP headers.

    Returns:
        Parsed JSON object.
    """
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _request_with_fallback(url: str, clean_key: str, headers: dict[str, str]) -> dict:
    """Request WakaTime endpoint with Basic-auth primary and query fallback.

    Args:
        url: WakaTime API endpoint.
        clean_key: Trimmed API key.
        headers: Base HTTP headers.

    Returns:
        Parsed JSON payload.
    """
    try:
        return _request_json(url, headers)
    except urllib.error.HTTPError as error:
        if error.code not in (401, 403):
            raise
        separator = "&" if "?" in url else "?"
        fallback_url = f"{url}{separator}api_key={urllib.parse.quote(clean_key)}"
        return _request_json(fallback_url, {"Accept": "application/json", "User-Agent": "workflow-manager"})


def _build_current_week_summaries_url(now: datetime | None = None) -> str:
    """Build summaries URL for the current week (Monday to today) in Asia/Shanghai."""
    local_now = now.astimezone(ASIA_SHANGHAI) if now else datetime.now(ASIA_SHANGHAI)
    today = local_now.date()
    monday = today - timedelta(days=today.weekday())
    params = urllib.parse.urlencode(
        {
            "start": monday.isoformat(),
            "end": today.isoformat(),
            "timezone": "Asia/Shanghai",
        }
    )
    return f"{WAKATIME_SUMMARIES_BASE_URL}?{params}"


def fetch_stats(api_key: str) -> tuple[dict, dict | None, dict | None, dict | None]:
    """Fetch WakaTime payloads used by README rendering.

    Args:
        api_key: WakaTime API key.

    Returns:
        Tuple of (summaries_payload, stats_payload_or_none, today_payload_or_none, all_time_payload_or_none).

    Raises:
        RuntimeError: If response is malformed or contains API errors.
    """
    clean_key = api_key.strip()
    auth = base64.b64encode(f"{clean_key}:".encode("utf-8")).decode("ascii")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Basic {auth}",
        "User-Agent": "workflow-manager"
    }
    summaries_url = _build_current_week_summaries_url()
    payload = _request_with_fallback(summaries_url, clean_key, headers)

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected WakaTime response: payload is not a JSON object")
    if "error" in payload:
        raise RuntimeError(f"WakaTime API error: {payload['error']}")

    stats_payload: dict | None = None
    today_payload: dict | None = None
    all_time_payload: dict | None = None
    try:
        candidate = _request_with_fallback(WAKATIME_STATS_URL, clean_key, headers)
        if isinstance(candidate, dict) and "error" not in candidate:
            stats_payload = candidate
    except Exception:
        stats_payload = None

    try:
        candidate = _request_with_fallback(WAKATIME_STATUS_BAR_TODAY_URL, clean_key, headers)
        if isinstance(candidate, dict) and "error" not in candidate:
            today_payload = candidate
    except Exception:
        today_payload = None

    try:
        candidate = _request_with_fallback(WAKATIME_ALL_TIME_URL, clean_key, headers)
        if isinstance(candidate, dict) and "error" not in candidate:
            all_time_payload = candidate
    except Exception:
        all_time_payload = None

    return payload, stats_payload, today_payload, all_time_payload


def _get_all_time_text(all_time_payload: dict | None) -> str | None:
    """Extract all-time code-time text from all_time_since_today payload."""
    if not all_time_payload:
        return None
    data = all_time_payload.get("data", {}) if isinstance(all_time_payload, dict) else {}
    if not isinstance(data, dict):
        return None
    return (
        data.get("text")
        or data.get("human_readable_total")
        or data.get("total")
        or None
    )


def _get_total_text(payload: dict) -> str:
    cumulative_total = payload.get("cumulative_total", {}) if isinstance(payload, dict) else {}
    return (
        cumulative_total.get("text")
        or cumulative_total.get("digital")
        or "0 secs"
    )


def _get_average_text(payload: dict) -> str:
    daily_average = payload.get("daily_average", {}) if isinstance(payload, dict) else {}
    return (
        daily_average.get("text_including_other_language")
        or daily_average.get("text")
        or "0 secs"
    )


def _get_today_total_text(today_payload: dict | None) -> str | None:
    """Extract today's coding total from status_bar/today payload."""
    if not today_payload or not isinstance(today_payload, dict):
        return None
    data = today_payload.get("data", {})
    if not isinstance(data, dict):
        return None
    grand_total = data.get("grand_total", {})
    if not isinstance(grand_total, dict):
        return None
    return grand_total.get("text") or grand_total.get("digital") or None


def _get_stats_data(stats_payload: dict | None) -> dict:
    """Return stats.data object or empty dict when unavailable."""
    if not stats_payload or not isinstance(stats_payload, dict):
        return {}
    data = stats_payload.get("data", {})
    return data if isinstance(data, dict) else {}


def _is_zero_like(text: str | None) -> bool:
    """Best-effort check for placeholder zero durations."""
    if not text:
        return True
    normalized = text.strip().lower()
    return normalized in {"", "0", "0 sec", "0 secs", "0 min", "0 mins", "0 hr", "0 hrs", "00:00"}


def _normalize_stats_items(stats_data: dict, key: str) -> list[dict]:
    """Normalize stats endpoint category arrays to renderable rows."""
    raw_items = stats_data.get(key, []) if isinstance(stats_data, dict) else []
    if not isinstance(raw_items, list):
        return []

    rows: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        seconds = float(item.get("total_seconds", 0) or 0)
        rows.append(
            {
                "name": str(item.get("name") or "Unknown"),
                "total_seconds": seconds,
                "text": item.get("text") or item.get("digital") or _humanize_seconds(seconds),
                "percent": float(item.get("percent", 0) or 0),
            }
        )
    return rows


def _get_stats_total_text(stats_data: dict) -> str | None:
    """Extract total time text from stats/last_7_days payload."""
    return (
        stats_data.get("human_readable_total_including_other_language")
        or stats_data.get("human_readable_total")
        or None
    )


def _get_stats_average_text(stats_data: dict) -> str | None:
    """Extract daily average text from stats/last_7_days payload."""
    return (
        stats_data.get("human_readable_daily_average_including_other_language")
        or stats_data.get("human_readable_daily_average")
        or None
    )


def _humanize_seconds(total_seconds: float) -> str:
    """Convert seconds to a compact human readable string."""
    rounded_seconds = max(0, int(round(total_seconds)))
    hours, remainder = divmod(rounded_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hr" if hours == 1 else f"{hours} hrs")
    if minutes:
        parts.append(f"{minutes} min" if minutes == 1 else f"{minutes} mins")
    if not parts:
        parts.append(f"{seconds} sec" if seconds == 1 else f"{seconds} secs")
    return " ".join(parts)


def _aggregate_summary_items(days: list[dict], key: str) -> list[dict]:
    """Aggregate per-day summary rows into one ranked list by total_seconds."""
    totals: dict[str, float] = {}
    for day in days:
        for item in day.get(key, []) or []:
            name = str(item.get("name") or "Unknown")
            totals[name] = totals.get(name, 0.0) + float(item.get("total_seconds", 0) or 0)

    total_seconds = sum(totals.values())
    ranked = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0].lower()))
    aggregated: list[dict] = []
    for name, seconds in ranked:
        percent = (seconds / total_seconds * 100.0) if total_seconds else 0.0
        aggregated.append(
            {
                "name": name,
                "total_seconds": seconds,
                "text": _humanize_seconds(seconds),
                "percent": percent,
            }
        )
    return aggregated


def _progress_bar(percent: float, width: int = 26) -> str:
    """Render fixed-width ASCII progress bar from percentage."""
    clamped = max(0.0, min(100.0, float(percent)))
    filled = int(round(clamped / 100.0 * width))
    return "#" * filled + "-" * (width - filled)


def _badge_url(label: str, message: str, color: str, logo: str | None = None) -> str:
    """Build shields static/v1 badge URL for README badges."""
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
    """Render ranked text table lines for category breakdown."""
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
    """Return (name, time_text, percent) tuple for top ranked item."""
    if not items:
        return "N/A", "0 mins", 0.0
    item = items[0]
    name = item.get("name") or "Unknown"
    text = item.get("text") or item.get("digital") or "0 mins"
    percent = float(item.get("percent", 0) or 0)
    return name, text, percent


def build_stats_block(
    payload: dict,
    stats_payload: dict | None = None,
    today_payload: dict | None = None,
    all_time_payload: dict | None = None,
) -> str:
    """Build markdown/HTML block for README WakaTime section.

    Args:
        payload: WakaTime API payload.

    Returns:
        Section content for marker replacement.
    """
    days = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(days, list):
        raise RuntimeError("Unexpected WakaTime summaries response: data is not a list")

    stats_data = _get_stats_data(stats_payload)

    languages = _aggregate_summary_items(days, "languages")[:5] or _normalize_stats_items(stats_data, "languages")[:5]
    editors = _aggregate_summary_items(days, "editors")[:5] or _normalize_stats_items(stats_data, "editors")[:5]
    projects = _aggregate_summary_items(days, "projects")[:5] or _normalize_stats_items(stats_data, "projects")[:5]
    operating_systems = _aggregate_summary_items(days, "operating_systems")[:5] or _normalize_stats_items(stats_data, "operating_systems")[:5]
    machines = _aggregate_summary_items(days, "machines")[:5] or _normalize_stats_items(stats_data, "machines")[:5]

    weekly_total = _get_total_text(payload)
    stats_total = _get_stats_total_text(stats_data)
    if _is_zero_like(weekly_total) and stats_total:
        weekly_total = stats_total

    today_total = _get_today_total_text(today_payload)
    all_time_total = _get_all_time_text(all_time_payload)
    total = weekly_total
    average = _get_average_text(payload)
    stats_average = _get_stats_average_text(stats_data)
    if _is_zero_like(average) and stats_average:
        average = stats_average

    code_time_badge_text = today_total if (today_total and not _is_zero_like(today_total)) else total
    synced_at = datetime.now(ASIA_SHANGHAI).replace(microsecond=0).strftime("%Y-%m-%d %H:%M CST")
    has_any_data = any([languages, editors, projects, operating_systems, machines])
    top_lang_name, top_lang_time, top_lang_percent = _top_item(languages)
    top_project_name, top_project_time, top_project_percent = _top_item(projects)
    top_editor_name, _, _ = _top_item(editors)

    code_time_dark = _badge_url("Code Time", code_time_badge_text, "334155", logo="wakatime")
    code_time_light = _badge_url("Code Time", code_time_badge_text, "2563eb", logo="wakatime")
    average_dark = _badge_url("Daily Average", average, "475569")
    average_light = _badge_url("Daily Average", average, "0f172a")
    sync_dark = _badge_url("Last Sync", synced_at, "1e293b")
    sync_light = _badge_url("Last Sync", synced_at, "1d4ed8")
    top_lang_dark = _badge_url("Top Language", top_lang_name, "0f766e")
    top_lang_light = _badge_url("Top Language", top_lang_name, "0d9488")
    top_project_dark = _badge_url("Top Project", top_project_name, "4c1d95")
    top_project_light = _badge_url("Top Project", top_project_name, "6d28d9")
    all_time_dark = _badge_url("All Time", all_time_total, "475569", logo="wakatime") if all_time_total else None
    all_time_light = _badge_url("All Time", all_time_total, "334155", logo="wakatime") if all_time_total else None

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
    ]

    if all_time_dark and all_time_light:
        header_lines.extend(
            [
                "<picture>",
                f'  <source media="(prefers-color-scheme: dark)" srcset="{all_time_dark}" />',
                f'  <img src="{all_time_light}" alt="all time code time" />',
                "</picture>",
            ]
        )

    header_lines.extend(
        [
        "",
        f"<sub>Focus: {top_lang_name} ({top_lang_time}, {top_lang_percent:.1f}%) | Project: {top_project_name} ({top_project_time}, {top_project_percent:.1f}%) | Editor: {top_editor_name}</sub>",
        "<sub>Code Time badge scope: Today (fallback: Last 7 Days)</sub>",
        "",
        "</div>",
        "",
        "<details>",
        "<summary><b>Weekly Breakdown | 本周明细</b></summary>",
        "",
        "```text",
        "Timezone: Asia/Shanghai (UTC+8)",
        f"Updated At (CST): {synced_at}",
        f"Window: This Week | Total: {weekly_total}",
        ""
    ]
    )

    body_lines: list[str] = []
    body_lines.extend(_render_ranked_lines(languages, "Languages"))
    body_lines.extend(_render_ranked_lines(editors, "Editors"))
    body_lines.extend(_render_ranked_lines(projects, "Projects"))
    body_lines.extend(_render_ranked_lines(operating_systems, "Operating Systems"))
    body_lines.extend(_render_ranked_lines(machines, "Machines"))

    if not has_any_data:
        body_lines.append("No activity tracked yet")

    body_lines.append("Generated by workflow-manager")
    body_lines.append("```")
    body_lines.append("")
    body_lines.append("</details>")
    return "\n".join(header_lines + body_lines)


def main() -> None:
    """Fetch stats, render section block, and update README markers."""
    api_key = os.getenv("WAKATIME_API_KEY")
    if not api_key:
        raise RuntimeError("WAKATIME_API_KEY is not configured")

    try:
        payload, stats_payload, today_payload, all_time_payload = fetch_stats(api_key)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WakaTime API request failed: {error.code} {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"WakaTime API request failed: {error.reason}") from error

    block = build_stats_block(payload, stats_payload, today_payload, all_time_payload)
    update_readme_section(README_PATH, START_MARKER, END_MARKER, block)
    print("Updated WakaTime section")


if __name__ == "__main__":
    main()