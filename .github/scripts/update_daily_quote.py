from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from readme_utils import update_readme_section


QUOTES = [
    ("The future depends on what you do today.", "Mahatma Gandhi"),
    ("Small steps, taken consistently, build remarkable systems.", "Anonymous"),
    ("First make it work, then make it right, then make it fast.", "Kent Beck"),
    ("Discipline is choosing between what you want now and what you want most.", "Abraham Lincoln"),
    ("Simplicity is prerequisite for reliability.", "Edsger W. Dijkstra"),
    ("A little progress each day adds up to big results.", "Satya Nani"),
    ("Well done is better than well said.", "Benjamin Franklin"),
    ("Quality is not an act, it is a habit.", "Aristotle"),
    ("Stay hungry, stay foolish.", "Steve Jobs"),
    ("Consistency compounds faster than intensity.", "Anonymous")
]

README_PATH = Path("README.md")
START_MARKER = "<!--START_SECTION:daily_quote-->"
END_MARKER = "<!--END_SECTION:daily_quote-->"


def main() -> None:
    today = datetime.now(timezone.utc).date()
    quote, author = QUOTES[today.toordinal() % len(QUOTES)]
    block = f"> {quote}\n>\n> — {author}"
    update_readme_section(README_PATH, START_MARKER, END_MARKER, block)
    print(f"Updated daily quote: {author}")


if __name__ == "__main__":
    main()