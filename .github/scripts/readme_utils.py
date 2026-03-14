from __future__ import annotations

from pathlib import Path


def replace_section(content: str, start_marker: str, end_marker: str, new_block: str) -> str:
    start_index = content.find(start_marker)
    end_index = content.find(end_marker)

    if start_index == -1 or end_index == -1 or end_index < start_index:
        raise ValueError(f"Unable to find marker pair: {start_marker} / {end_marker}")

    start_index += len(start_marker)
    block = f"\n{new_block.rstrip()}\n"
    return content[:start_index] + block + content[end_index:]


def update_readme_section(readme_path: Path, start_marker: str, end_marker: str, new_block: str) -> None:
    original = readme_path.read_text(encoding="utf-8")
    updated = replace_section(original, start_marker, end_marker, new_block)
    readme_path.write_text(updated, encoding="utf-8")