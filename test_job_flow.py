import unittest
from unittest.mock import patch

import marketing_pipeline


class MarketingJobFlowTests(unittest.TestCase):
    def setUp(self):
        self.research = {
            "obsidian": [
                {
                    "file": "store/매장정보.md",
                    "updated": "2026-08-20 09:00",
                    "excerpt": "홍대 레드로드점 매장정보",
                }
            ],
            "trends": [],
            "trends_all": [],
        }
        self.selection = {
            "topic": "홍대 모임에서 메뉴를 고르는 기준",
            "angle": "모임 구성에 맞춘 선택",
            "reason": "매장정보에 근거",
            "source_files": ["store/매장정보.md"],
            "trend": None,
        }

    @patch.object(marketing_pipeline, "record_topic_history")
    @patch.object(marketing_pipeline, "send_marketing_channels")
    @patch.object(marketing_pipeline, "openrouter_request", return_value="generated content")
    @patch.object(marketing_pipeline, "select_daily_topic")
    @patch.object(marketing_pipeline, "load_topic_history", return_value=[])
    @patch.object(marketing_pipeline, "research_snapshot")
    def test_records_topic_only_after_discord_success(
        self,
        research_snapshot,
        load_history,
        select_topic,
        openrouter,
        send_channels,
        record_history,
    ):
        research_snapshot.return_value = self.research
        select_topic.return_value = self.selection

        result = marketing_pipeline.run_marketing_job()

        send_channels.assert_called_once()
        record_history.assert_called_once()
        self.assertEqual(record_history.call_args.args[0], self.selection["topic"])
        self.assertEqual(result["selection"], self.selection)

    @patch.object(marketing_pipeline, "record_topic_history")
    @patch.object(marketing_pipeline, "send_marketing_channels", side_effect=RuntimeError("Discord failed"))
    @patch.object(marketing_pipeline, "openrouter_request", return_value="generated content")
    @patch.object(marketing_pipeline, "select_daily_topic")
    @patch.object(marketing_pipeline, "load_topic_history", return_value=[])
    @patch.object(marketing_pipeline, "research_snapshot")
    def test_does_not_record_topic_when_discord_fails(
        self,
        research_snapshot,
        load_history,
        select_topic,
        openrouter,
        send_channels,
        record_history,
    ):
        research_snapshot.return_value = self.research
        select_topic.return_value = self.selection

        with self.assertRaisesRegex(RuntimeError, "Discord failed"):
            marketing_pipeline.run_marketing_job()

        record_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
