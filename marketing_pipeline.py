#!/usr/bin/env python3
"""Minimal marketing review pipeline for the Hermes Railway container."""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DISCORD_API_URL = "https://discord.com/api/v10"
USER_AGENT = "goobne-hermes-marketing-pipeline/1.0"
DEFAULT_MODEL = "~anthropic/claude-sonnet-latest"
DEFAULT_TOPIC = "홍대에서 외국인 친구와 치맥하기 좋은 이유"
DEFAULT_ENV_FILES = ("/root/.hermes/.env", ".env")
DEFAULT_OBSIDIAN_PATH = "/root/.hermes/drive/obsidian"
DEFAULT_TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=KR"


def configured_timezone():
    name = os.environ.get("TZ", "Asia/Seoul")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9), "KST")


def log(stage, message):
    print(f"[{stage}] {message}", flush=True)


def load_env_files():
    for path in DEFAULT_ENV_FILES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError as exc:
            log("ENV ERROR", f"Could not read {path}: {exc}")



def clip_text(value, limit):
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def read_obsidian_sources():
    root = Path(os.environ.get("GDRIVE_LOCAL_PATH", DEFAULT_OBSIDIAN_PATH))
    if not root.exists():
        log("Obsidian", f"source folder not found: {root}")
        return []
    max_files = max(1, int(os.environ.get("OBSIDIAN_MAX_FILES", "12")))
    max_chars = max(400, int(os.environ.get("OBSIDIAN_MAX_CHARS", "12000")))
    files = sorted(
        (
            path
            for path in root.rglob("*.md")
            if not any(part.startswith(".") for part in path.relative_to(root).parts)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    sources = []
    remaining = max_chars
    for path in files[:max_files]:
        if remaining <= 0:
            break
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log("Obsidian", f"could not read {path.name}: {exc}")
            continue
        excerpt = clip_text(raw, min(1800, remaining))
        if not excerpt:
            continue
        sources.append({
            "file": str(path.relative_to(root)).replace("\\", "/"),
            "updated": datetime.fromtimestamp(path.stat().st_mtime, configured_timezone()).strftime("%Y-%m-%d %H:%M"),
            "excerpt": excerpt,
        })
        remaining -= len(excerpt)
    log("Obsidian", f"loaded {len(sources)} markdown source(s)")
    return sources


def fetch_korean_trends():
    url = os.environ.get("TRENDS_RSS_URL", DEFAULT_TRENDS_RSS_URL).strip()
    limit = max(1, int(os.environ.get("TRENDS_MAX_ITEMS", "20")))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=int(os.environ.get("TRENDS_TIMEOUT_SECONDS", "20"))) as response:
            root = ET.fromstring(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError) as exc:
        log("Trends", f"could not fetch Korean trends: {exc}")
        return []
    trends = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        if title and title not in trends:
            trends.append(title)
        if len(trends) >= limit:
            break
    log("Trends", f"loaded {len(trends)} Korean trend(s)")
    return trends


def research_snapshot():
    obsidian = read_obsidian_sources()
    require_obsidian = os.environ.get("MARKETING_REQUIRE_OBSIDIAN", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if require_obsidian and not obsidian:
        raise RuntimeError(
            "Obsidian source check failed: no markdown files were loaded from "
            f"{os.environ.get('GDRIVE_LOCAL_PATH', DEFAULT_OBSIDIAN_PATH)}"
        )
    return {"obsidian": obsidian, "trends": fetch_korean_trends()}


def research_summary(research):
    source_files = ", ".join(source["file"] for source in research["obsidian"]) or "없음"
    trends = ", ".join(research["trends"][:10]) or "가져오지 못함"
    return f"Obsidian 자료: {source_files}\n트렌드 후보: {trends}"

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


def discord_webhook_url():
    return (
        os.environ.get("DISCORD_WEBHOOK_REVIEW", "").strip()
        or os.environ.get("DISCORD_WEBHOOK_검수대기", "").strip()
        or os.environ.get("DISCORD_WEBHOOK_승인대기", "").strip()
    )


def send_discord_webhook(content, url=None):
    url = (url or discord_webhook_url()).strip()
    if not url:
        return False
    for index, chunk in enumerate(split_discord_message(content), start=1):
        payload = {"content": chunk}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(os.environ.get("DISCORD_TIMEOUT_SECONDS", "30"))) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"Discord webhook HTTP {exc.code} on chunk {index}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Discord webhook network error on chunk {index}: {exc.reason}") from exc
    return True


def send_discord_bot_message(content, channel_id=None):
    token = require_env("DISCORD_BOT_TOKEN")
    channel_id = channel_id or require_env("DISCORD_CHANNEL_ID")
    url = f"{DISCORD_API_URL}/channels/{channel_id}/messages"
    for index, chunk in enumerate(split_discord_message(content), start=1):
        payload = {"content": chunk}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(os.environ.get("DISCORD_TIMEOUT_SECONDS", "30"))) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"Discord bot HTTP {exc.code} on chunk {index}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Discord bot network error on chunk {index}: {exc.reason}") from exc


def send_discord_message(content):
    if send_discord_webhook(content):
        return
    send_discord_bot_message(content)


def extract_markdown_section(content, heading):
    pattern = re.compile(
        rf"^#{{2,4}}\s*[^\n]*{re.escape(heading)}[^\n]*$\n?(.*?)(?=^#{{2,4}}\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(content)
    return match.group(1).strip() if match else ""


def discord_content_targets():
    return {
        "Threads": {
            "webhook": os.environ.get("DISCORD_WEBHOOK_짧은글", "").strip(),
            "channel": os.environ.get("DISCORD_CHANNEL_SHORT_DRAFT", "").strip(),
        },
        "Blog": {
            "webhook": os.environ.get("DISCORD_WEBHOOK_블로그", "").strip(),
            "channel": os.environ.get("DISCORD_CHANNEL_BLOG_DRAFT", "").strip(),
        },
        "Card News": {
            "webhook": os.environ.get("DISCORD_WEBHOOK_인스타", "").strip(),
            "channel": os.environ.get("DISCORD_CHANNEL_CARDNEWS_DRAFT", "").strip(),
        },
        "촬영 지시서": {
            "webhook": os.environ.get("DISCORD_WEBHOOK_촬영지시서", "").strip(),
            "channel": os.environ.get("DISCORD_CHANNEL_SHOOTING_GUIDE", "").strip(),
        },
        "Place 새소식": {
            "webhook": os.environ.get("DISCORD_WEBHOOK_플레이스", "").strip(),
            "channel": os.environ.get("DISCORD_CHANNEL_PLACE_DRAFT", "").strip(),
        },
        "승인대기": {
            "webhook": (
                os.environ.get("DISCORD_WEBHOOK_승인대기", "").strip()
                or os.environ.get("DISCORD_WEBHOOK_REVIEW", "").strip()
            ),
            "channel": (
                os.environ.get("DISCORD_CHANNEL_REVIEW", "").strip()
                or os.environ.get("DISCORD_CHANNEL_ID", "").strip()
            ),
        },
    }


def send_discord_target(content, target):
    if target["webhook"]:
        send_discord_webhook(content, target["webhook"])
        return
    if target["channel"]:
        send_discord_bot_message(content, target["channel"])
        return
    raise RuntimeError("Discord target has neither a webhook nor a bot channel")


def send_marketing_channels(content, research, now):
    targets = discord_content_targets()
    draft_targets = {name: target for name, target in targets.items() if name != "승인대기"}
    if not all(target["webhook"] or target["channel"] for target in targets.values()):
        log("Discord", "channel routing incomplete; sending combined draft to legacy review target")
        message = (
            f"## 굽네 마케팅 검수 초안\n"
            f"- 생성시각: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"- {research_summary(research)}\n\n{content}"
        )
        send_discord_message(message)
        return

    for heading, target in draft_targets.items():
        section = extract_markdown_section(content, heading)
        if not section:
            headings = re.findall(r"^#{2,4}\s*.+$", content, re.MULTILINE)
            log("LLM", f"generated headings: {headings[:20]}")
            raise RuntimeError(f"Generated content is missing required section: {heading}")
        message = (
            f"## {heading} 초안\n"
            f"- 생성시각: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
            f"{section}"
        )
        log("Discord", f"send started: {heading}")
        send_discord_target(message, target)
        log("Discord", f"success: {heading}")

    review_parts = []
    for heading in ("오늘의 콘텐츠 방향", "사용 근거", "검수 체크리스트"):
        section = extract_markdown_section(content, heading)
        if section:
            review_parts.append(f"## {heading}\n{section}")
    review_message = (
        f"## 오늘의 콘텐츠 승인대기\n"
        f"- 생성시각: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"- 각 형식별 초안이 전용 채널에 전송되었습니다.\n\n"
        + "\n\n".join(review_parts)
    )
    log("Discord", "send started: 승인대기")
    send_discord_target(review_message, targets["승인대기"])
    log("Discord", "success: 승인대기")


def build_content_prompt(research):
    store_name = os.environ.get("STORE_NAME", "굽네치킨 홍대 레드로드점")
    fallback_topic = os.environ.get("MARKETING_SOURCE_TOPIC", DEFAULT_TOPIC).strip() or DEFAULT_TOPIC
    sources = research["obsidian"]
    trends = research["trends"]
    source_text = "\n\n".join(
        f"[Obsidian: {source['file']} | updated {source['updated']}]\n{source['excerpt']}"
        for source in sources
    ) or "(동기화된 Obsidian 자료 없음)"
    trend_text = "\n".join(f"- {trend}" for trend in trends) or "(트렌드 수집 실패 또는 결과 없음)"
    return [
        {
            "role": "system",
            "content": (
                "너는 굽네치킨 홍대 레드로드점의 시니어 로컬 마케팅 에디터다. "
                "매장 자료에 근거한 내용만 사실처럼 표현하고, 확인되지 않은 가격·할인·영업시간·메뉴·후기를 지어내지 않는다. "
                "전국 트렌드는 홍대 레드로드점 및 치킨 방문 맥락과 자연스럽게 연결될 때만 사용한다. "
                "연결이 억지라면 트렌드를 사용하지 말고 그 이유를 검수 메모에 적는다. "
                "광고 문구보다 구체적 장면, 손님 상황, 실제 방문 동선을 우선한다."
            ),
        },
        {
            "role": "user",
            "content": f"""
매장: {store_name}
자료가 전혀 없을 때만 사용할 보조 주제: {fallback_topic}

아래는 Google Drive에서 동기화된 Obsidian 최근 자료다.
--- Obsidian 자료 ---
{source_text}

아래는 오늘의 한국 검색 트렌드 후보다.
--- 트렌드 후보 ---
{trend_text}

위 근거를 먼저 읽고, 오늘 매장과 가장 잘 맞는 콘텐츠 방향을 하나 선택해 한국어 초안을 작성해줘.

필수 원칙:
- 사용한 Obsidian 파일명과 사용한 트렌드만 정확히 표시한다.
- 근거에 없는 행사·가격·메뉴·후기·운영 정보는 넣지 않는다.
- 트렌드가 매장과 무관하면 억지로 연결하지 않는다.
- 문장마다 과장된 감탄사와 흔한 AI 표현을 피한다.
- 직원이 사실 여부를 빠르게 확인할 수 있게 검수 포인트를 준다.

출력 형식:
## 오늘의 콘텐츠 방향
선택한 주제와 선택 이유 2~3문장

## 사용 근거
- Obsidian: 파일명 또는 없음
- Trend: 키워드 또는 사용 안 함
- 확인 필요: 직원 확인 항목

## Threads
서로 다른 톤의 짧은 글 2안. 각 안에 자연스러운 행동 유도 한 줄 포함.

## Blog
제목 3개, 도입, 소제목 3개가 있는 본문, 마무리. 검색어를 억지로 반복하지 않는다.

## Card News
카드 1~8. 각 카드는 사진/디자인 담당자가 이해할 수 있는 장면 지시 한 줄과 카피 한 줄을 포함한다.

## 촬영 지시서
카드뉴스와 다른 채널에 공통으로 활용할 수 있는 실제 촬영 컷 6~10개. 각 컷마다 촬영 목적, 구도, 필요한 소품, 피해야 할 요소를 짧게 적는다.

## Place 새소식
네이버 플레이스 새소식에 바로 붙여 넣을 수 있는 제목 1개와 본문. 확인되지 않은 가격·행사·할인을 넣지 않고, 매장 방문 맥락과 자연스러운 행동 유도를 포함한다.

## 검수 체크리스트
사실 확인, 표현 수정, 필요한 사진 또는 운영 확인 항목을 3~5개.
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


def run_discord_channels_test():
    targets = discord_content_targets()
    missing = [
        name for name, target in targets.items()
        if not target["webhook"] and not target["channel"]
    ]
    if missing:
        raise RuntimeError(f"Missing Discord channel configuration: {', '.join(missing)}")
    for name, target in targets.items():
        log("Discord", f"channel test started: {name}")
        send_discord_target(f"Hermes channel routing test: {name}", target)
        log("Discord", f"channel test success: {name}")


def run_research_test():
    research = research_snapshot()
    print(research_summary(research))


def run_marketing_job():
    log("Job", "started")
    log("Hermes", "research started")
    research = research_snapshot()
    log("Hermes", "research completed")
    log("LLM", "request started")
    content = openrouter_request(
        build_content_prompt(research),
        max_tokens=int(os.environ.get("MARKETING_MAX_TOKENS", "3200")),
    )
    log("LLM", "success")
    now = datetime.now(configured_timezone())
    log("Discord", "send started")
    send_marketing_channels(content, research, now)
    log("Discord", "success")
    log("Job", "completed")


def scheduler_loop():
    timezone = configured_timezone()
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
    load_env_files()
    parser = argparse.ArgumentParser(description="Goobne Hermes marketing pipeline")
    parser.add_argument("--once", action="store_true", help="Run the full marketing pipeline once")
    parser.add_argument("--test-llm", action="store_true", help="Send a tiny OpenRouter request")
    parser.add_argument("--test-discord", action="store_true", help="Send a Discord test message")
    parser.add_argument("--test-discord-channels", action="store_true", help="Test every marketing Discord channel")
    parser.add_argument("--test-research", action="store_true", help="Inspect Obsidian and Korean trend inputs")
    parser.add_argument("--test-gdrive", action="store_true", help="Verify locally synced Obsidian markdown files")
    parser.add_argument("--scheduler", action="store_true", help="Run the daily scheduler loop")
    args = parser.parse_args()
    try:
        if args.test_llm:
            run_llm_test()
        elif args.test_discord:
            run_discord_test()
        elif args.test_discord_channels:
            run_discord_channels_test()
        elif args.test_research or args.test_gdrive:
            run_research_test()
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
