from __future__ import annotations

"""Refresh snapshot-related README sections and showcase SVG assets."""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from readme_utils import update_readme_section

ASIA_SHANGHAI = timezone(timedelta(hours=8))


README_PATH = Path("README.md")
START_MARKER = "<!--START_SECTION:recent_repos-->"
END_MARKER = "<!--END_SECTION:recent_repos-->"
REALTIME_START_MARKER = "<!--START_SECTION:realtime_panel-->"
REALTIME_END_MARKER = "<!--END_SECTION:realtime_panel-->"
SHOWCASE_START_MARKER = "<!--START_SECTION:showcase_slides-->"
SHOWCASE_END_MARKER = "<!--END_SECTION:showcase_slides-->"
HERO_SUBTITLE_START_MARKER = "<!--START_SECTION:hero_subtitle-->"
HERO_SUBTITLE_END_MARKER = "<!--END_SECTION:hero_subtitle-->"
SHOWCASE_SVG_PATH = Path("assets/showcase-carousel.svg")
SHOWCASE_COLORS = ["1d4ed8", "0891b2", "0f766e"]


def try_update_readme_section(readme_path: Path, start_marker: str, end_marker: str, new_block: str) -> bool:
    """Try updating a README marker section and tolerate missing markers.

    Args:
        readme_path: Target README path.
        start_marker: Marker that starts the managed block.
        end_marker: Marker that ends the managed block.
        new_block: Replacement markdown/html content.

    Returns:
        True when section update succeeds; False when marker pair is absent.
    """
    try:
        update_readme_section(readme_path, start_marker, end_marker, new_block)
        return True
    except ValueError:
        return False


def github_request(url: str) -> list[dict]:
    """Issue a GitHub API GET request and return parsed JSON list.

    Args:
        url: GitHub API URL.

    Returns:
        Decoded JSON list payload.
    """
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
    pushed_at_raw = repo['pushed_at']
    try:
        dt_utc = datetime.fromisoformat(pushed_at_raw.replace("Z", "+00:00"))
        dt_cst = dt_utc.astimezone(ASIA_SHANGHAI)
        pushed_at = dt_cst.strftime("%Y-%m-%d %H:%M CST")
    except (ValueError, AttributeError):
        pushed_at = pushed_at_raw
    return (
        f"- [{repo['name']}](https://github.com/{owner}/{repo['name']})"
        f" - Updated: {pushed_at}"
    )


def short_text(value: str | None, fallback: str, max_len: int = 46) -> str:
    """Return clipped single-line text for badges/cards."""
    source = (value or "").strip() or fallback
    if len(source) <= max_len:
        return source
    return source[: max_len - 3].rstrip() + "..."


def build_static_badge(label: str, message: str, color: str, style: str = "for-the-badge") -> str:
    """Build a shields static/v1 badge URL.

    Args:
        label: Badge label text.
        message: Badge message text.
        color: Badge color.
        style: Shields style value.

    Returns:
        Encoded shields static badge URL.
    """
    label_q = urllib.parse.quote(label, safe="")
    message_q = urllib.parse.quote(message, safe="")
    color_q = urllib.parse.quote(color, safe="")
    return (
        "https://img.shields.io/static/v1"
        f"?label={label_q}&message={message_q}&color={color_q}&style={style}"
    )


def format_showcase_cells(owner: str, repos: list[dict]) -> str:
    """Render showcase slide badges and summaries for top repositories.

    Args:
        owner: GitHub repository owner.
        repos: Recently updated repositories.

    Returns:
        HTML block for README showcase slides.
    """
    if not repos:
        return "<p align=\"center\"><sub>No repositories available for showcase.</sub></p>"

    badge_links: list[str] = []
    summaries: list[str] = []
    for idx, repo in enumerate(repos[:3], start=1):
        color = SHOWCASE_COLORS[(idx - 1) % len(SHOWCASE_COLORS)]
        name = repo.get("name", "unknown")
        desc = short_text(repo.get("description"), "No description yet")
        title = short_text(name, "unknown", 24)
        badge_url = build_static_badge(f"Slide 0{idx}", name, color)
        badge = (
            f'<a href="https://github.com/{owner}/{name}">'
            f'<img src="{badge_url}" alt="slide {idx}" />'
            f'</a>'
        )
        summary = (
            f'<sub><b>{escape(title)}</b>: {escape(desc)} · '
            f'<a href="https://github.com/{owner}/{name}">Open repository</a></sub>'
        )
        badge_links.append(badge)
        summaries.append(summary)

    while len(badge_links) < 3:
        badge_links.append(f'<img src="{build_static_badge("Slide", "Waiting for more projects", "64748b")}" alt="waiting" />')
        summaries.append("<sub>Waiting for more projects.</sub>")

    badges_block = "\n".join(badge_links)
    summary_block = "<br/>\n  ".join(summaries)
    return (
        "<div align=\"center\">\n\n"
        f"{badges_block}\n\n"
        "</div>\n\n"
        "<p align=\"center\">\n"
        f"  {summary_block}\n"
        "</p>"
    )


def build_realtime_panel(owner: str, repos: list[dict]) -> str:
    """Build realtime panel lines for the KPI section.

    Args:
        owner: GitHub repository owner.
        repos: Recently updated repositories.

    Returns:
        Markdown bullet list payload.
    """
    now_cst = datetime.now(ASIA_SHANGHAI).strftime("%Y-%m-%d %H:%M CST")
    top_repo = repos[0]["name"] if repos else "n/a"
    return "\n".join(
        [
            f"- Live sync: {now_cst}",
            "- Data source: GitHub REST API + workflow-manager snapshot worker",
            "- Showcase source: top 3 recently updated public repositories",
            f"- Current top repository: [{top_repo}](https://github.com/{owner}/{top_repo})" if repos else "- Current top repository: n/a"
        ]
    )


def build_showcase_svg(repos: list[dict]) -> str:
    """Build animated showcase SVG content.

    Args:
        repos: Recently updated repositories.

    Returns:
        SVG XML string.
    """
    defaults = [
        {"name": "Project-01", "description": "Repository showcase"},
        {"name": "Project-02", "description": "Repository showcase"},
        {"name": "Project-03", "description": "Repository showcase"}
    ]
    cards = (repos[:3] + defaults)[:3]

    def line1(item: dict) -> str:
        return short_text(item.get("name"), "Repository", 28)

    def line2(item: dict) -> str:
        return short_text(item.get("description"), "No description yet", 44)

    c1, c2, c3 = cards

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="280" viewBox="0 0 1200 280" role="img" aria-label="Animated project carousel">
    <defs>
        <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#e0f2fe" />
            <stop offset="50%" stop-color="#dbeafe" />
            <stop offset="100%" stop-color="#ccfbf1" />
        </linearGradient>
        <linearGradient id="card1" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#1d4ed8" />
            <stop offset="100%" stop-color="#0284c7" />
        </linearGradient>
        <linearGradient id="card2" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#0f766e" />
            <stop offset="100%" stop-color="#0891b2" />
        </linearGradient>
        <linearGradient id="card3" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#0369a1" />
            <stop offset="100%" stop-color="#1d4ed8" />
        </linearGradient>
        <clipPath id="viewport">
            <rect x="20" y="36" width="1160" height="208" rx="24" />
        </clipPath>
        <filter id="shadow" x="-20%" y="-20%" width="140%" height="160%">
            <feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#0f172a" flood-opacity="0.16" />
        </filter>
        <style>
            .title {{ font: 700 26px 'Segoe UI', Arial, sans-serif; fill: #0f172a; }}
            .subtitle {{ font: 500 15px 'Segoe UI', Arial, sans-serif; fill: #1e293b; }}
            .label {{ font: 700 18px 'Segoe UI', Arial, sans-serif; fill: #ffffff; }}
            .desc {{ font: 500 13px 'Segoe UI', Arial, sans-serif; fill: #e2e8f0; }}
            .chip {{ font: 700 12px 'Segoe UI', Arial, sans-serif; fill: #0f172a; }}
            .track {{
                animation: slide 32s linear infinite;
            }}
            @keyframes slide {{
                0% {{ transform: translateX(0); }}
                100% {{ transform: translateX(-1260px); }}
            }}
        </style>
    </defs>

    <rect width="1200" height="280" fill="url(#bg)" rx="28" />
    <text x="36" y="28" class="title">Showcase Motion</text>
    <text x="260" y="28" class="subtitle">Auto-scrolling project highlights</text>

    <g clip-path="url(#viewport)">
        <g class="track" filter="url(#shadow)">
            <g transform="translate(40,56)">
                <rect width="360" height="172" rx="18" fill="url(#card1)" />
                <rect x="18" y="16" width="100" height="26" rx="13" fill="#e0f2fe" />
                <text x="32" y="34" class="chip">Slide 01</text>
                <text x="20" y="82" class="label">{escape(line1(c1))}</text>
                <text x="20" y="112" class="desc">{escape(line2(c1))}</text>
            </g>

            <g transform="translate(430,56)">
                <rect width="360" height="172" rx="18" fill="url(#card2)" />
                <rect x="18" y="16" width="100" height="26" rx="13" fill="#dcfce7" />
                <text x="32" y="34" class="chip">Slide 02</text>
                <text x="20" y="82" class="label">{escape(line1(c2))}</text>
                <text x="20" y="112" class="desc">{escape(line2(c2))}</text>
            </g>

            <g transform="translate(820,56)">
                <rect width="360" height="172" rx="18" fill="url(#card3)" />
                <rect x="18" y="16" width="100" height="26" rx="13" fill="#dbeafe" />
                <text x="32" y="34" class="chip">Slide 03</text>
                <text x="20" y="82" class="label">{escape(line1(c3))}</text>
                <text x="20" y="112" class="desc">{escape(line2(c3))}</text>
            </g>

            <g transform="translate(1210,56)">
                <rect width="360" height="172" rx="18" fill="url(#card1)" />
                <rect x="18" y="16" width="100" height="26" rx="13" fill="#e0f2fe" />
                <text x="32" y="34" class="chip">Slide 01</text>
                <text x="20" y="82" class="label">{escape(line1(c1))}</text>
                <text x="20" y="112" class="desc">{escape(line2(c1))}</text>
            </g>

            <g transform="translate(1600,56)">
                <rect width="360" height="172" rx="18" fill="url(#card2)" />
                <rect x="18" y="16" width="100" height="26" rx="13" fill="#dcfce7" />
                <text x="32" y="34" class="chip">Slide 02</text>
                <text x="20" y="82" class="label">{escape(line1(c2))}</text>
                <text x="20" y="112" class="desc">{escape(line2(c2))}</text>
            </g>

            <g transform="translate(1990,56)">
                <rect width="360" height="172" rx="18" fill="url(#card3)" />
                <rect x="18" y="16" width="100" height="26" rx="13" fill="#dbeafe" />
                <text x="32" y="34" class="chip">Slide 03</text>
                <text x="20" y="82" class="label">{escape(line1(c3))}</text>
                <text x="20" y="112" class="desc">{escape(line2(c3))}</text>
            </g>
        </g>
    </g>
</svg>
'''


def main() -> None:
    """Fetch recent repos and refresh snapshot-driven README sections."""
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

    showcase = selected[:3]

    try_update_readme_section(README_PATH, START_MARKER, END_MARKER, block)
    try_update_readme_section(
        README_PATH,
        SHOWCASE_START_MARKER,
        SHOWCASE_END_MARKER,
        format_showcase_cells(owner, showcase)
    )

    try_update_readme_section(
        README_PATH,
        REALTIME_START_MARKER,
        REALTIME_END_MARKER,
        build_realtime_panel(owner, showcase)
    )
    try_update_readme_section(
        README_PATH,
        HERO_SUBTITLE_START_MARKER,
        HERO_SUBTITLE_END_MARKER,
        build_hero_subtitle(owner, showcase)
    )

    SHOWCASE_SVG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHOWCASE_SVG_PATH.write_text(build_showcase_svg(showcase), encoding="utf-8")
    print(f"Updated recent repository snapshot with {len(selected)} entries and refreshed showcase assets")


def build_hero_subtitle(owner: str, repos: list[dict]) -> str:
    """Build the hero subtitle narrative based on most recent repository."""
    if not repos:
        return "Main narrative: building in public with weekly automation and continuous iteration."

    top = repos[0]
    top_name = top.get("name", "latest-project")
    description = short_text(top.get("description"), "active project exploration", 54)
    return (
        f"Main narrative: shipping around [{top_name}](https://github.com/{owner}/{top_name}) this week, "
        f"with focus on {description}."
    )


if __name__ == "__main__":
    main()