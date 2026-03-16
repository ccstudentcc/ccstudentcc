from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from readme_utils import (  # type: ignore[import-not-found]
    MarkerConflictError,
    replace_section,
    try_update_readme_section,
)


class ReadmeUtilsTests(unittest.TestCase):
    def test_replace_section_replaces_unique_marker_pair(self) -> None:
        content = "before\n<!--START_SECTION:test-->\nold\n<!--END_SECTION:test-->\nafter\n"

        updated = replace_section(content, "<!--START_SECTION:test-->", "<!--END_SECTION:test-->", "new")

        self.assertIn("\nnew\n", updated)
        self.assertNotIn("old", updated)

    def test_replace_section_rejects_duplicate_marker_pairs(self) -> None:
        content = (
            "<!--START_SECTION:test-->\nold\n<!--END_SECTION:test-->\n"
            "<!--START_SECTION:test-->\nother\n<!--END_SECTION:test-->\n"
        )

        with self.assertRaisesRegex(MarkerConflictError, "appears multiple times"):
            replace_section(content, "<!--START_SECTION:test-->", "<!--END_SECTION:test-->", "new")

    def test_try_update_readme_section_returns_false_for_missing_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            readme_path = Path(temp_dir) / "README.md"
            readme_path.write_text("plain text\n", encoding="utf-8")

            updated = try_update_readme_section(
                readme_path,
                "<!--START_SECTION:test-->",
                "<!--END_SECTION:test-->",
                "new",
            )

            self.assertFalse(updated)
            self.assertEqual(readme_path.read_text(encoding="utf-8"), "plain text\n")

    def test_try_update_readme_section_raises_for_duplicate_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            readme_path = Path(temp_dir) / "README.md"
            readme_path.write_text(
                "<!--START_SECTION:test-->\none\n<!--END_SECTION:test-->\n"
                "<!--START_SECTION:test-->\ntwo\n<!--END_SECTION:test-->\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(MarkerConflictError, "appears multiple times"):
                try_update_readme_section(
                    readme_path,
                    "<!--START_SECTION:test-->",
                    "<!--END_SECTION:test-->",
                    "new",
                )


if __name__ == "__main__":
    unittest.main()
