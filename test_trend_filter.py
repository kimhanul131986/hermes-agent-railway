import os
import unittest
from unittest.mock import patch

import marketing_pipeline


class TrendFilterTests(unittest.TestCase):
    def test_keeps_directly_relevant_trends(self):
        trends = ["홍대 거리 축제", "이번 주 장마", "프로야구 순위", "유명 배우 결혼"]
        self.assertEqual(
            marketing_pipeline.filter_relevant_trends(trends),
            ["홍대 거리 축제", "이번 주 장마"],
        )

    def test_keeps_trend_mentioned_in_obsidian(self):
        sources = [{"excerpt": "이번 캠페인은 불꽃축제 방문객을 위한 모임 콘텐츠다."}]
        self.assertEqual(
            marketing_pipeline.filter_relevant_trends(["불꽃축제", "테니스 결승"], sources),
            ["불꽃축제"],
        )

    def test_does_not_force_unrelated_trend(self):
        with patch.dict(os.environ, {"TREND_RELEVANCE_KEYWORDS": "홍대,치킨"}):
            self.assertEqual(
                marketing_pipeline.filter_relevant_trends(["주식 시장", "야구 경기"]),
                [],
            )


if __name__ == "__main__":
    unittest.main()
