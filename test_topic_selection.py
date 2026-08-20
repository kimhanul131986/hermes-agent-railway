import json
import os
import unittest
from unittest.mock import patch

import marketing_pipeline


class TopicSelectionTests(unittest.TestCase):
    def setUp(self):
        self.research = {
            "obsidian": [
                {"file": "store/매장정보.md", "excerpt": "홍대 레드로드에 있는 오븐구이 치킨 매장"},
                {"file": "00_운영규칙/THREADS_RULE.md", "excerpt": "스레드는 반말"},
            ],
            "trends": ["홍대 거리 축제"],
            "trends_all": ["홍대 거리 축제", "야구 경기"],
        }

    def response(self, topic, trend=None):
        return json.dumps(
            {
                "topic": topic,
                "angle": "방문 상황에 맞는 선택 기준",
                "reason": "매장정보에 근거한 주제",
                "source_files": ["store/매장정보.md"],
                "trend": trend,
            },
            ensure_ascii=False,
        )

    def test_retries_duplicate_and_returns_distinct_topic(self):
        history = [{"topic": "홍대에서 외국인 친구와 치맥하기 좋은 이유"}]
        responses = [
            self.response("외국인 친구와 홍대에서 치맥하기 좋은 이유"),
            self.response("홍대 모임에서 오븐구이 메뉴를 고르는 기준"),
        ]
        with patch.dict(os.environ, {"MARKETING_TOPIC_MAX_ATTEMPTS": "2"}), patch.object(
            marketing_pipeline,
            "openrouter_request",
            side_effect=responses,
        ) as request:
            selection = marketing_pipeline.select_daily_topic(self.research, history)
        self.assertEqual(selection["topic"], "홍대 모임에서 오븐구이 메뉴를 고르는 기준")
        self.assertEqual(request.call_count, 2)

    def test_uses_dedicated_topic_model_when_configured(self):
        with patch.dict(os.environ, {"OPENROUTER_TOPIC_MODEL": "openai/gpt-4.1-mini"}), patch.object(
            marketing_pipeline,
            "openrouter_request",
            return_value=self.response("홍대 모임 장소 선택 기준"),
        ) as request:
            marketing_pipeline.select_daily_topic(self.research, [])
        self.assertEqual(request.call_args.kwargs["model"], "openai/gpt-4.1-mini")

    def test_rejects_unknown_source_file(self):
        response = json.dumps(
            {
                "topic": "새 주제",
                "angle": "새 관점",
                "reason": "근거",
                "source_files": ["없는파일.md"],
                "trend": None,
            },
            ensure_ascii=False,
        )
        with patch.object(marketing_pipeline, "openrouter_request", return_value=response):
            with self.assertRaises(RuntimeError):
                marketing_pipeline.select_daily_topic(self.research, [])

    def test_accepts_only_available_trend(self):
        with patch.object(
            marketing_pipeline,
            "openrouter_request",
            return_value=self.response("홍대 축제 날 모임 장소를 고르는 기준", "홍대 거리 축제"),
        ):
            selection = marketing_pipeline.select_daily_topic(self.research, [])
        self.assertEqual(selection["trend"], "홍대 거리 축제")

    def test_resolves_unique_source_basename(self):
        response = self.response("홍대 모임 장소 선택 기준")
        payload = json.loads(response)
        payload["source_files"] = ["매장정보.md"]
        with patch.object(
            marketing_pipeline,
            "openrouter_request",
            return_value=json.dumps(payload, ensure_ascii=False),
        ):
            selection = marketing_pipeline.select_daily_topic(self.research, [])
        self.assertEqual(selection["source_files"], ["store/매장정보.md"])

    def test_content_prompt_locks_one_topic_and_keeps_rules(self):
        selection = {
            "topic": "홍대 모임에서 오븐구이 메뉴를 고르는 기준",
            "angle": "모임 구성원에 맞춘 메뉴 선택",
            "reason": "매장정보에 근거",
            "source_files": ["store/매장정보.md"],
            "trend": None,
        }
        messages = marketing_pipeline.build_content_prompt(self.research, selection)
        prompt = messages[1]["content"]
        self.assertIn(selection["topic"], prompt)
        self.assertIn("store/매장정보.md", prompt)
        self.assertIn("00_운영규칙/THREADS_RULE.md", prompt)
        self.assertIn("서로 다른 소재를 만들지 않는다", prompt)


if __name__ == "__main__":
    unittest.main()
