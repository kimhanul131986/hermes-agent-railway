#!/usr/bin/env python3
"""Minimal marketing review pipeline for the Hermes Railway container."""

import argparse
import difflib
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
DEFAULT_TOPIC_HISTORY_PATH = "/root/.hermes/marketing/topic_history.json"
DEFAULT_TOPIC_HISTORY_LIMIT = 30
DEFAULT_TREND_KEYWORDS = (
    "홍대,홍익대,연남,합정,상수,마포,서울,레드로드,"
    "치킨,치맥,맥주,오븐,구이,맛집,외식,야식,회식,모임,데이트,"
    "외국인,관광,K푸드,K-food,축제,공연,날씨,비,장마,눈,폭염,더위,추위,주말"
)


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


def topic_history_path():
    return Path(os.environ.get("MARKETING_TOPIC_HISTORY_PATH", DEFAULT_TOPIC_HISTORY_PATH))


def load_topic_history(limit=DEFAULT_TOPIC_HISTORY_LIMIT):
    """Return the newest saved marketing topics without modifying the history file."""
    path = topic_history_path()
    if not path.exists():
        log("History", f"no topic history yet: {path}")
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read topic history {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"Topic history must be a JSON array: {path}")

    history = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise RuntimeError(f"Topic history item {index} must be an object: {path}")
        topic = str(item.get("topic", "")).strip()
        if not topic:
            raise RuntimeError(f"Topic history item {index} has no topic: {path}")
        normalized = dict(item)
        normalized["topic"] = topic
        history.append(normalized)
    bounded_limit = max(1, int(limit))
    result = history[:bounded_limit]
    log("History", f"loaded {len(result)} recent topic(s)")
    return result


def record_topic_history(topic, angle="", status="draft_sent", details=None, now=None):
    """Atomically prepend one topic and retain only the newest configured entries."""
    topic = str(topic).strip()
    if not topic:
        raise ValueError("topic must not be empty")
    limit = max(1, int(os.environ.get("MARKETING_TOPIC_HISTORY_LIMIT", str(DEFAULT_TOPIC_HISTORY_LIMIT))))
    history = load_topic_history(limit=limit)
    timestamp = now or datetime.now(configured_timezone())
    entry = {
        "date": timestamp.strftime("%Y-%m-%d"),
        "recorded_at": timestamp.isoformat(),
        "topic": topic,
        "angle": str(angle).strip(),
        "status": str(status).strip() or "draft_sent",
    }
    if details:
        entry["details"] = details

    path = topic_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps([entry] + history[: limit - 1], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        raise RuntimeError(f"Could not write topic history {path}: {exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    log("History", f"recorded topic with status={entry['status']}; retained up to {limit}")
    return entry


def normalize_topic(value):
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value).lower())


def topic_ngrams(value, size=2):
    normalized = normalize_topic(value)
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[index:index + size] for index in range(len(normalized) - size + 1)}


def topic_similarity(left, right):
    left_normalized = normalize_topic(left)
    right_normalized = normalize_topic(right)
    if not left_normalized or not right_normalized:
        return 0.0
    sequence_score = difflib.SequenceMatcher(None, left_normalized, right_normalized).ratio()
    left_ngrams = topic_ngrams(left)
    right_ngrams = topic_ngrams(right)
    union = left_ngrams | right_ngrams
    ngram_score = len(left_ngrams & right_ngrams) / len(union) if union else 0.0
    return max(sequence_score, ngram_score)


def find_duplicate_topic(candidate, history, threshold=None):
    """Return the closest recent history item when a candidate is too similar."""
    candidate = str(candidate).strip()
    if not candidate:
        raise ValueError("candidate topic must not be empty")
    configured_threshold = float(
        threshold if threshold is not None
        else os.environ.get("MARKETING_TOPIC_DUPLICATE_THRESHOLD", "0.72")
    )
    best_match = None
    for item in history[:DEFAULT_TOPIC_HISTORY_LIMIT]:
        previous_topic = str(item.get("topic", "")).strip()
        if not previous_topic:
            continue
        score = topic_similarity(candidate, previous_topic)
        if best_match is None or score > best_match["score"]:
            best_match = {"score": score, "history": item}
    if best_match and best_match["score"] >= configured_threshold:
        log(
            "History",
            f"duplicate topic rejected: similarity={best_match['score']:.2f}, "
            f"previous={best_match['history']['topic']}",
        )
        return best_match
    return None



def clip_text(value, limit):
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def obsidian_priority_rank(path, root):
    relative = path.relative_to(root)
    parts = {part.lower() for part in relative.parts[:-1]}
    filename = relative.name.lower()
    if "00_운영규칙" in parts or filename.endswith("_rule.md"):
        return 0
    if "store" in parts or "매장" in filename or "운영정보" in filename:
        return 1
    if "03_브랜드스타일" in parts or "브랜드" in filename or "말투" in filename:
        return 2
    if "01_제품지식" in parts or "제품지식" in filename or "메뉴" in filename:
        return 3
    return None


def read_obsidian_sources():
    root = Path(os.environ.get("GDRIVE_LOCAL_PATH", DEFAULT_OBSIDIAN_PATH))
    if not root.exists():
        log("Obsidian", f"source folder not found: {root}")
        return []
    max_files = max(1, int(os.environ.get("OBSIDIAN_MAX_FILES", "12")))
    priority_max_files = max(
        1,
        min(max_files, int(os.environ.get("OBSIDIAN_PRIORITY_MAX_FILES", "8"))),
    )
    max_chars = max(400, int(os.environ.get("OBSIDIAN_MAX_CHARS", "12000")))
    discovered = [
        path
        for path in root.rglob("*.md")
        if not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]
    priority_files = sorted(
        (path for path in discovered if obsidian_priority_rank(path, root) is not None),
        key=lambda path: (obsidian_priority_rank(path, root), -path.stat().st_mtime),
    )[:priority_max_files]
    priority_set = set(priority_files)
    recent_files = sorted(
        (path for path in discovered if path not in priority_set),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    files = priority_files + recent_files[: max_files - len(priority_files)]
    sources = []
    remaining = max_chars
    for path in files:
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
    loaded_priority = sum(
        1 for source in sources
        if obsidian_priority_rank(root / source["file"], root) is not None
    )
    log(
        "Obsidian",
        f"loaded {len(sources)} markdown source(s); priority={loaded_priority}, "
        f"recent={len(sources) - loaded_priority}",
    )
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


def trend_relevance_keywords():
    raw = os.environ.get("TREND_RELEVANCE_KEYWORDS", DEFAULT_TREND_KEYWORDS)
    return [keyword.strip().lower() for keyword in raw.split(",") if keyword.strip()]


def filter_relevant_trends(trends, obsidian_sources=None):
    """Keep only trends with an explicit store keyword or a matching Obsidian mention."""
    keywords = trend_relevance_keywords()
    source_text = " ".join(
        source.get("excerpt", "") for source in (obsidian_sources or [])
    ).lower()
    relevant = []
    for trend in trends:
        lowered = str(trend).lower().strip()
        if not lowered:
            continue
        direct_match = any(keyword in lowered for keyword in keywords)
        source_match = len(lowered) >= 2 and lowered in source_text
        if (direct_match or source_match) and trend not in relevant:
            relevant.append(trend)
    log("Trends", f"selected {len(relevant)} relevant trend(s) from {len(trends)}")
    return relevant


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
    all_trends = fetch_korean_trends()
    return {
        "obsidian": obsidian,
        "trends": filter_relevant_trends(all_trends, obsidian),
        "trends_all": all_trends,
    }


def research_summary(research):
    source_files = ", ".join(source["file"] for source in research["obsidian"]) or "없음"
    trends = ", ".join(research["trends"][:10]) or "가져오지 못함"
    return f"Obsidian 자료: {source_files}\n트렌드 후보: {trends}"

def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def openrouter_request(messages, max_tokens=1800, model=None):
    api_key = require_env("OPENROUTER_API_KEY")
    model = (model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)).strip() or DEFAULT_MODEL
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
        choice = body["choices"][0]
        content = choice["message"].get("content")
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Unexpected OpenRouter response shape") from exc
    if not isinstance(content, str) or not content.strip():
        finish_reason = choice.get("finish_reason", "unknown")
        raise RuntimeError(f"OpenRouter returned empty content; finish_reason={finish_reason}")
    return content.strip()


def parse_json_object(value):
    text = str(value).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("LLM JSON response must be an object")
    return payload


def build_topic_selection_prompt(research, history, rejected_topics=None):
    source_text = "\n\n".join(
        f"[파일: {source['file']}]\n{source['excerpt']}"
        for source in research["obsidian"]
    )
    history_text = "\n".join(
        f"- {item['topic']} | 관점: {item.get('angle', '')}"
        for item in history
    ) or "- 없음"
    trend_text = "\n".join(f"- {trend}" for trend in research["trends"]) or "- 관련 트렌드 없음"
    rejected_text = "\n".join(f"- {topic}" for topic in (rejected_topics or [])) or "- 없음"
    return [
        {
            "role": "system",
            "content": (
                "너는 굽네 홍대 레드로드점의 콘텐츠 소재 편집자다. "
                "제공된 자료에 근거해 오늘 사용할 원본 주제 하나만 고른다. "
                "확인되지 않은 사실이나 억지 트렌드 연결을 만들지 않는다."
            ),
        },
        {
            "role": "user",
            "content": f"""
아래 자료를 읽고 오늘의 원본 주제 하나를 선정해.

최근 사용 주제 30개:
{history_text}

이번 실행에서 중복으로 거절된 후보:
{rejected_text}

관련성이 확인된 트렌드:
{trend_text}

Obsidian 자료:
{source_text}

선정 규칙:
- 최근 주제 및 거절 후보와 소재나 관점이 겹치지 않아야 한다.
- 최소 한 개의 Obsidian 파일을 실제 근거로 사용한다.
- 관련 트렌드가 없거나 연결이 약하면 trend는 null로 둔다.
- 매장 홍보 문구가 아니라 여러 플랫폼으로 확장 가능한 원본 주제를 고른다.
- 확인되지 않은 수치, 후기, 운영 경험은 만들지 않는다.

JSON만 출력:
{{
  "topic": "오늘의 원본 주제",
  "angle": "이번 콘텐츠에서 다룰 고유한 관점",
  "reason": "이 주제를 고른 근거",
  "source_files": ["실제로 사용한 Obsidian 파일명"],
  "trend": null
}}
""".strip(),
        },
    ]


def validate_selected_topic(selection, research):
    topic = str(selection.get("topic", "")).strip()
    angle = str(selection.get("angle", "")).strip()
    reason = str(selection.get("reason", "")).strip()
    source_files = selection.get("source_files", [])
    trend = selection.get("trend")
    if not topic or not angle or not reason:
        raise RuntimeError("Selected topic JSON requires topic, angle, and reason")
    if not isinstance(source_files, list) or not source_files:
        raise RuntimeError("Selected topic must cite at least one Obsidian source file")
    available_sources = {source["file"] for source in research["obsidian"]}
    basename_map = {}
    for filename in available_sources:
        basename_map.setdefault(Path(filename).name, []).append(filename)
    resolved_sources = []
    unknown_sources = []
    for name in source_files:
        if name in available_sources:
            resolved_sources.append(name)
            continue
        basename_matches = basename_map.get(Path(str(name)).name, [])
        if len(basename_matches) == 1:
            resolved_sources.append(basename_matches[0])
        else:
            unknown_sources.append(str(name))
    if unknown_sources:
        raise RuntimeError(f"Selected topic cited unknown source file(s): {', '.join(unknown_sources)}")
    if trend is not None and trend not in research["trends"]:
        raise RuntimeError(f"Selected topic cited an unavailable trend: {trend}")
    return {
        "topic": topic,
        "angle": angle,
        "reason": reason,
        "source_files": resolved_sources,
        "trend": trend,
    }


def select_daily_topic(research, history=None):
    history = history if history is not None else load_topic_history()
    max_attempts = max(1, int(os.environ.get("MARKETING_TOPIC_MAX_ATTEMPTS", "3")))
    rejected_topics = []
    for attempt in range(1, max_attempts + 1):
        log("Topic", f"selection request started; attempt={attempt}/{max_attempts}")
        response = openrouter_request(
            build_topic_selection_prompt(research, history, rejected_topics),
            max_tokens=int(os.environ.get("MARKETING_TOPIC_MAX_TOKENS", "1800")),
            model=os.environ.get("OPENROUTER_TOPIC_MODEL", "").strip() or None,
        )
        selection = validate_selected_topic(parse_json_object(response), research)
        duplicate = find_duplicate_topic(selection["topic"], history)
        if duplicate:
            rejected_topics.append(selection["topic"])
            continue
        log("Topic", f"selected: {selection['topic']}")
        return selection
    raise RuntimeError(f"Could not select a non-duplicate topic after {max_attempts} attempts")


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
    heading_pattern = re.compile(r"^(#{2,6})\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_pattern.finditer(content))
    target = None
    target_text = str(heading).strip().lower()
    for match in matches:
        if target_text in match.group(2).strip().lower():
            target = match
            break
    if target is None:
        return ""
    target_level = len(target.group(1))
    end = len(content)
    for match in matches:
        if match.start() <= target.start():
            continue
        if len(match.group(1)) <= target_level:
            end = match.start()
            break
    return content[target.end():end].strip()


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


def is_rule_source_name(filename):
    normalized = str(filename).replace("\\", "/").lower()
    return "/00_운영규칙/" in f"/{normalized}" or normalized.endswith("_rule.md")


def build_content_prompt(research, selected_topic):
    store_name = os.environ.get("STORE_NAME", "굽네치킨 홍대 레드로드점")
    cited_files = set(selected_topic["source_files"])
    sources = [
        source for source in research["obsidian"]
        if source["file"] in cited_files or is_rule_source_name(source["file"])
    ]
    source_text = "\n\n".join(
        f"[Obsidian: {source['file']} | updated {source.get('updated', 'unknown')}]\n{source['excerpt']}"
        for source in sources
    ) or "(동기화된 Obsidian 자료 없음)"
    selection_text = json.dumps(selected_topic, ensure_ascii=False, indent=2)
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

오늘의 원본 주제는 이미 아래와 같이 확정됐다. 다른 주제로 바꾸지 마.
--- 선정 결과 ---
{selection_text}

아래는 선정에 사용된 근거와 항상 적용할 운영규칙이다.
--- Obsidian 자료 ---
{source_text}

하나의 원본 주제를 모든 플랫폼 형식으로 변환해 한국어 초안을 작성해줘.

필수 원칙:
- 모든 형식은 선정된 topic과 angle을 공유한다. 서로 다른 소재를 만들지 않는다.
- 사용한 Obsidian 파일명과 선정된 trend만 정확히 표시한다.
- 근거에 없는 행사·가격·메뉴·후기·운영 정보는 넣지 않는다.
- trend가 null이면 트렌드를 새로 끌어오거나 억지로 연결하지 않는다.
- 문장마다 과장된 감탄사와 흔한 AI 표현을 피한다.
- 직원이 사실 여부를 빠르게 확인할 수 있게 검수 포인트를 준다.
- Threads는 THREADS_RULE의 반말·화자 규칙을 반드시 적용한다.

출력 형식:
## 오늘의 콘텐츠 방향
확정된 원본 주제와 관점, 선정 이유 2~3문장

## 사용 근거
- Obsidian: 파일명 또는 없음
- Trend: 키워드 또는 사용 안 함
- 확인 필요: 직원 확인 항목

## Threads
공식 계정용 짧은 글 1안. THREADS_RULE에 따라 자연스러운 반말과 운영자 화자를 사용하고, 근거 없는 개인 경험을 만들지 않는다.

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
    history = load_topic_history()
    selection = select_daily_topic(research, history)
    log("LLM", "request started")
    content = openrouter_request(
        build_content_prompt(research, selection),
        max_tokens=int(os.environ.get("MARKETING_MAX_TOKENS", "3200")),
        model=os.environ.get("OPENROUTER_CONTENT_MODEL", "").strip() or None,
    )
    log("LLM", "success")
    now = datetime.now(configured_timezone())
    log("Discord", "send started")
    send_marketing_channels(content, research, now)
    log("Discord", "success")
    record_topic_history(
        selection["topic"],
        angle=selection["angle"],
        status="draft_sent",
        details={
            "reason": selection["reason"],
            "source_files": selection["source_files"],
            "trend": selection["trend"],
        },
        now=now,
    )
    log("Job", "completed")
    return {"selection": selection, "content": content}


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
