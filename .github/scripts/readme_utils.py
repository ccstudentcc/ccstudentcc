from __future__ import annotations

"""Utilities for README marker-based section replacement."""

import argparse
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


LOCK_TIMEOUT_SECONDS = 120.0
LOCK_POLL_INTERVAL_SECONDS = 0.1
STALE_LOCK_SECONDS = 600.0


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


def _lock_path_for(readme_path: Path) -> Path:
    """Return the lock file path used to serialize README writes."""
    return readme_path.with_name(f"{readme_path.name}.lock")


@contextmanager
def readme_update_lock(readme_path: Path):
    """Acquire a cross-process lock using an exclusive lock file."""
    lock_path = _lock_path_for(readme_path)
    started = time.monotonic()
    pid = os.getpid()

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"pid={pid}\nstarted={time.time():.6f}\n")
            break
        except FileExistsError:
            try:
                age_seconds = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue

            if age_seconds >= STALE_LOCK_SECONDS:
                try:
                    lock_path.unlink()
                    continue
                except FileNotFoundError:
                    continue

            if time.monotonic() - started >= LOCK_TIMEOUT_SECONDS:
                raise TimeoutError(f"Timed out waiting for README lock: {lock_path}")
            time.sleep(LOCK_POLL_INTERVAL_SECONDS)

    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def write_text_atomic(path: Path, content: str) -> None:
    """Atomically replace a text file using a temporary file in the same directory."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, newline="") as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def update_readme_section(readme_path: Path, start_marker: str, end_marker: str, new_block: str) -> None:
    """Update a README file section identified by start/end markers.

    Args:
        readme_path: Path to README file.
        start_marker: Marker starting the managed block.
        end_marker: Marker ending the managed block.
        new_block: New content inserted between markers.
    """
    with readme_update_lock(readme_path):
        original = readme_path.read_text(encoding="utf-8")
        updated = replace_section(original, start_marker, end_marker, new_block)
        write_text_atomic(readme_path, updated)


def main() -> None:
    """Provide a small CLI so non-Python workers can reuse the locked updater."""
    parser = argparse.ArgumentParser(description="Update a marker-delimited README section with a shared file lock.")
    parser.add_argument("readme_path")
    parser.add_argument("start_marker")
    parser.add_argument("end_marker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--block")
    group.add_argument("--block-file")
    args = parser.parse_args()

    if args.block_file:
        new_block = Path(args.block_file).read_text(encoding="utf-8")
    else:
        new_block = args.block

    update_readme_section(Path(args.readme_path), args.start_marker, args.end_marker, new_block)


if __name__ == "__main__":
    main()