import unittest

import marketing_pipeline


class MarkdownSectionTests(unittest.TestCase):
    def test_keeps_nested_headings_inside_platform_section(self):
        content = """## Threads
짧은 글

## Blog
### 제목
블로그 제목
### 도입
도입 본문
### 마무리
마무리 본문

## Card News
카드 내용
"""
        section = marketing_pipeline.extract_markdown_section(content, "Blog")
        self.assertIn("### 제목", section)
        self.assertIn("### 마무리", section)
        self.assertNotIn("## Card News", section)

    def test_accepts_decorated_heading(self):
        content = "## 1. ✍️ Threads 초안\n반말 본문\n\n## Blog\n블로그"
        self.assertEqual(
            marketing_pipeline.extract_markdown_section(content, "Threads"),
            "반말 본문",
        )


if __name__ == "__main__":
    unittest.main()
