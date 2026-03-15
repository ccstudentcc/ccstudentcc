from __future__ import annotations

"""Utilities for README marker-based section replacement."""

from pathlib import Path


def replace_section(content: str, start_marker: str, end_marker: str, new_block: str) -> str:
    """Replace content between marker pair with a normalized block.

    Args:
        content: Full markdown document content.
        start_marker: Marker starting the managed block.
        end_marker: Marker ending the managed block.
        new_block: New content inserted between markers.

    Returns:
        Updated markdown content.

    Raises:
        ValueError: If marker pair cannot be located in proper order.
    """
    start_index = content.find(start_marker)
    end_index = content.find(end_marker)

    if start_index == -1 or end_index == -1 or end_index < start_index:
        raise ValueError(f"Unable to find marker pair: {start_marker} / {end_marker}")

    start_index += len(start_marker)
    block = f"\n{new_block.rstrip()}\n"
    return content[:start_index] + block + content[end_index:]


def update_readme_section(readme_path: Path, start_marker: str, end_marker: str, new_block: str) -> None:
    """Update a README file section identified by start/end markers.

    Args:
        readme_path: Path to README file.
        start_marker: Marker starting the managed block.
        end_marker: Marker ending the managed block.
        new_block: New content inserted between markers.
    """
    original = readme_path.read_text(encoding="utf-8")
    updated = replace_section(original, start_marker, end_marker, new_block)
    readme_path.write_text(updated, encoding="utf-8")