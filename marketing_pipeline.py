#!/usr/bin/env python3
"""Minimal marketing review pipeline for the Hermes Railway container."""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DISCORD_API_URL = "https://discord.com/api/v10"
DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_TOPIC = "홍대에서 외국인 친구와 치맥하기 좋은 이유"


def log(stage, message):
    print(f"[{stage}] {message}", flush=True)


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def openrouter_request(messages, max_tokens=1800):
    api_key = require_env("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(os.environ.get("MARKETING_TEMPERATURE", "0.7")),
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get("APP_PUBLIC_URL", "https://railway.app"),
            "X-Title": "Goobne Hermes Marketing Pipeline",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(os.environ.get("LLM_TIMEOUT_SECONDS", "90"))) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenRouter network error: {exc.reason}") from exc
    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenRouter response shape: {json.dumps(body)[:800]}") from exc


def split_discord_message(content):
    limit = 1900
    if len(content) <= limit:
        return [content]
    chunks = []
    current = []
    current_len = 0
    for line in content.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if line_len > limit:
            for start in range(0, len(line), limit):
                chunks.append(line[start:start + limit])
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_discord_message(content):
    token = require_env("DISCORD_BOT_TOKEN")
    channel_id = require_env("DISCORD_CHANNEL_ID")
    url = f"{DISCORD_API_URL}/channels/{channel_id}/messages"
    for index, chunk in enumerate(split_discord_message(content), start=1):
        payload = {"content": chunk}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "goobne-hermes-marketing-pipeline/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(os.environ.get("DISCORD_TIMEOUT_SECONDS", "30"))) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"Discord HTTP {exc.code} on chunk {index}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Discord network error on chunk {index}: {exc.reason}") from exc


def build_content_prompt(topic):
    store_name = os.environ.get("STORE_NAME", "굽네치킨 홍대 레드로드점")
    return [
        {
            "role": "system",
            "content": "너는 한국 치킨 매장의 실전 마케팅 담당자다. 과장 광고를 피하고, 사람이 Discord에서 검수하기 좋은 초안만 만든다.",
        },
        {
            "role": "user",
            "content": f"""
매장: {store_name}
오늘의 원본 주제: {topic}

하루에 서로 다른 콘텐츠 3개가 아니라, 하나의 원본 주제를 아래 3개 형식으로 변환해줘.

출력 형식:
## Source
원본 주제 한 줄

## Threads
짧고 자연스러운 SNS 글. 이모지 과다 사용 금지.

## Blog
검색 유입을 고려한 블로그 초안. 제목, 도입, 본문 소제목 3개, 마무리 포함.

## Card News
7~10장 카드뉴스 카피. 각 장은 "카드 1:" 형식.

검수자가 바로 볼 수 있도록 한국어로 작성해.
""".strip(),
        },
    ]


def run_llm_test():
    log("LLM", "request started")
    result = openrouter_request([{"role": "user", "content": "say hello"}], max_tokens=80)
    log("LLM", "success")
    print(result)


def run_discord_test():
    log("Discord", "send started")
    send_discord_message("Hermes Discord connection test")
    log("Discord", "success")


def run_marketing_job():
    log("Job", "started")
    topic = os.environ.get("MARKETING_SOURCE_TOPIC", DEFAULT_TOPIC).strip() or DEFAULT_TOPIC
    log("Hermes", "job started")
    log("LLM", "request started")
    content = openrouter_request(build_content_prompt(topic))
    log("LLM", "success")
    now = datetime.now(ZoneInfo(os.environ.get("TZ", "Asia/Seoul")))
    message = f"## 굽네 마케팅 검수 초안\n- 생성시각: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n- Source: {topic}\n\n{content}"
    log("Discord", "send started")
    send_discord_message(message)
    log("Discord", "success")
    log("Job", "completed")


def scheduler_loop():
    timezone = ZoneInfo(os.environ.get("TZ", "Asia/Seoul"))
    run_time = os.environ.get("MARKETING_RUN_TIME", "09:00").strip()
    hour, minute = [int(part) for part in run_time.split(":", 1)]
    log("Scheduler", f"started; daily run at {run_time} {timezone.key}")
    while True:
        now = datetime.now(timezone)
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        sleep_seconds = max(1, int((next_run - now).total_seconds()))
        log("Scheduler", f"next run at {next_run.isoformat()}")
        time.sleep(sleep_seconds)
        try:
            run_marketing_job()
        except Exception as exc:
            log("SCHEDULER ERROR", str(exc))


def main():
    parser = argparse.ArgumentParser(description="Goobne Hermes marketing pipeline")
    parser.add_argument("--once", action="store_true", help="Run the full marketing pipeline once")
    parser.add_argument("--test-llm", action="store_true", help="Send a tiny OpenRouter request")
    parser.add_argument("--test-discord", action="store_true", help="Send a Discord test message")
    parser.add_argument("--scheduler", action="store_true", help="Run the daily scheduler loop")
    args = parser.parse_args()
    try:
        if args.test_llm:
            run_llm_test()
        elif args.test_discord:
            run_discord_test()
        elif args.scheduler:
            scheduler_loop()
        elif args.once:
            run_marketing_job()
        else:
            parser.print_help()
    except Exception as exc:
        label = "ERROR"
        text = str(exc)
        if "OpenRouter" in text or "OPENROUTER" in text:
            label = "LLM ERROR"
        elif "Discord" in text or "DISCORD" in text:
            label = "DISCORD ERROR"
        log(label, text)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
