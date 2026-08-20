import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import marketing_pipeline


class TopicHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.history_path = os.path.join(self.temporary_directory.name, "topic_history.json")
        self.environment = patch.dict(
            os.environ,
            {
                "MARKETING_TOPIC_HISTORY_PATH": self.history_path,
                "MARKETING_TOPIC_HISTORY_LIMIT": "3",
                "TZ": "Asia/Seoul",
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_missing_history_returns_empty_list(self):
        self.assertEqual(marketing_pipeline.load_topic_history(), [])

    def test_record_prepends_and_trims_history(self):
        for day, topic in enumerate(("첫 번째", "두 번째", "세 번째", "네 번째"), start=1):
            marketing_pipeline.record_topic_history(
                topic,
                angle=f"관점 {day}",
                now=datetime(2026, 8, day, 9, 0, tzinfo=timezone(timedelta(hours=9))),
            )

        history = marketing_pipeline.load_topic_history()
        self.assertEqual([item["topic"] for item in history], ["네 번째", "세 번째", "두 번째"])
        self.assertTrue(all(item["status"] == "draft_sent" for item in history))
        with open(self.history_path, encoding="utf-8") as history_file:
            self.assertEqual(json.load(history_file), history)

    def test_invalid_history_fails_without_overwriting(self):
        with open(self.history_path, "w", encoding="utf-8") as history_file:
            history_file.write("not-json")
        with self.assertRaises(RuntimeError):
            marketing_pipeline.record_topic_history("새 주제")
        with open(self.history_path, encoding="utf-8") as history_file:
            self.assertEqual(history_file.read(), "not-json")

    def test_duplicate_topic_detects_reordered_phrase(self):
        history = [{"topic": "홍대에서 외국인 친구와 치맥하기 좋은 이유"}]
        match = marketing_pipeline.find_duplicate_topic(
            "외국인 친구와 홍대에서 치맥하기 좋은 이유",
            history,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["history"]["topic"], history[0]["topic"])

    def test_distinct_topic_is_not_duplicate(self):
        history = [{"topic": "비 오는 날 홍대에서 치맥하기"}]
        match = marketing_pipeline.find_duplicate_topic(
            "오븐구이 메뉴를 고르는 기준",
            history,
        )
        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
