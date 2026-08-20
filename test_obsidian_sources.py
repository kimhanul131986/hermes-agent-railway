import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import marketing_pipeline


class ObsidianSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.environment = patch.dict(
            os.environ,
            {
                "GDRIVE_LOCAL_PATH": str(self.root),
                "OBSIDIAN_MAX_FILES": "4",
                "OBSIDIAN_PRIORITY_MAX_FILES": "2",
                "OBSIDIAN_MAX_CHARS": "5000",
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def write_markdown(self, relative_path, content, modified_time):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (modified_time, modified_time))
        return path

    def test_rules_and_store_files_are_not_displaced_by_recent_notes(self):
        old_time = time.time() - 10000
        self.write_markdown("00_운영규칙/THREADS_RULE.md", "반드시 반말", old_time)
        self.write_markdown("store/매장정보.md", "홍대 레드로드점", old_time + 1)
        for index in range(6):
            self.write_markdown(
                f"02_시장정보/최근메모{index}.md",
                f"최근 소재 {index}",
                time.time() + index,
            )

        sources = marketing_pipeline.read_obsidian_sources()
        files = [source["file"] for source in sources]
        self.assertEqual(len(files), 4)
        self.assertIn("00_운영규칙/THREADS_RULE.md", files)
        self.assertIn("store/매장정보.md", files)
        self.assertEqual(len([name for name in files if "최근메모" in name]), 2)

    def test_hidden_markdown_is_ignored(self):
        self.write_markdown(".obsidian/private.md", "ignore", time.time())
        self.write_markdown("marketing/topic.md", "include", time.time())
        sources = marketing_pipeline.read_obsidian_sources()
        self.assertEqual([source["file"] for source in sources], ["marketing/topic.md"])


if __name__ == "__main__":
    unittest.main()
