import unittest

import marketing_pipeline


class BlogGroundingTests(unittest.TestCase):
    def setUp(self):
        self.research = {
            "obsidian": [
                {
                    "file": "00_운영규칙/BLOG_RULE.md",
                    "updated": "2026-08-21 09:00",
                    "excerpt": "CONNECT → CONVINCE → CONVERT, 존댓말",
                },
                {
                    "file": "00_운영규칙/THREADS_RULE.md",
                    "updated": "2026-08-21 09:00",
                    "excerpt": "반말",
                },
                {
                    "file": "00_운영규칙/SOURCE_RULE.md",
                    "updated": "2026-08-21 09:00",
                    "excerpt": "근거 우선",
                },
                {
                    "file": "store/확정정보.md",
                    "updated": "2026-08-21 09:00",
                    "excerpt": "굽네치킨 홍대 레드로드점",
                },
            ],
            "trends": [],
            "trends_all": [],
        }
        self.selection = {
            "topic": "홍대 레드로드 치킨",
            "angle": "방문 맥락",
            "reason": "확정정보",
            "source_files": ["store/확정정보.md"],
            "trend": None,
        }

    def test_platform_rules_are_separated(self):
        shared_sources = marketing_pipeline.content_sources(
            self.research, self.selection, platform="threads"
        )
        shared_names = {source["file"] for source in shared_sources}
        self.assertIn("00_운영규칙/THREADS_RULE.md", shared_names)
        self.assertNotIn("00_운영규칙/BLOG_RULE.md", shared_names)

        blog_sources = marketing_pipeline.content_sources(
            self.research, self.selection, platform="blog"
        )
        blog_names = {source["file"] for source in blog_sources}
        self.assertIn("00_운영규칙/BLOG_RULE.md", blog_names)
        self.assertNotIn("00_운영규칙/THREADS_RULE.md", blog_names)

    def test_rejects_explicitly_unverified_topic_source(self):
        research = dict(self.research)
        research["obsidian"] = self.research["obsidian"] + [
            {
                "file": "store/매장_스토리.md",
                "updated": "2026-08-21 09:00",
                "excerpt": "핵심 서사 (초안 — 대표님 확인 필요)",
            }
        ]
        selection = dict(self.selection)
        selection["source_files"] = ["store/매장_스토리.md"]
        with self.assertRaisesRegex(RuntimeError, "unverified source"):
            marketing_pipeline.validate_selected_topic(selection, research)

        review_sources = marketing_pipeline.content_sources(
            research,
            selection,
            platform="blog",
            include_unverified_citations=False,
        )
        review_names = {source["file"] for source in review_sources}
        self.assertNotIn("store/매장_스토리.md", review_names)
        self.assertIn("00_운영규칙/BLOG_RULE.md", review_names)

    def test_replaces_only_blog_section(self):
        content = "## Threads\n짧은 글\n\n## Blog\n기존 글\n\n## Card News\n카드"
        replaced = marketing_pipeline.replace_markdown_section(
            content, "Blog", "### 제목 제안\n새 글"
        )
        self.assertIn("## Threads\n짧은 글", replaced)
        self.assertIn("## Blog\n### 제목 제안\n새 글", replaced)
        self.assertIn("## Card News\n카드", replaced)
        self.assertNotIn("기존 글", replaced)


if __name__ == "__main__":
    unittest.main()
