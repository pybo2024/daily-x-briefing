"""Fetch daily AI/Tech and US Stock/Finance headlines and summarize with Gemini."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import feedparser

RSS_SOURCES = [
    ("AI 科技", "Hacker News", "https://hnrss.org/frontpage?count=12"),
    ("AI 科技", "TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("美股财经", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    (
        "美股财经",
        "CNBC Top News",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    ),
]

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_rss_items(limit_per_feed: int = 5) -> list[dict[str, str]]:
    """Collect recent headlines across AI/Tech and Finance RSS feeds."""
    collected: list[dict[str, str]] = []
    for category, source_name, url in RSS_SOURCES:
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:limit_per_feed]:
                title = _clean_text(getattr(entry, "title", ""))
                summary = _clean_text(getattr(entry, "summary", ""))
                if title:
                    collected.append(
                        {
                            "category": category,
                            "source": source_name,
                            "title": title,
                            "summary": summary[:200],
                            "link": getattr(entry, "link", ""),
                        }
                    )
        except Exception:
            continue
    return collected


def _summarize_with_gemini(
    rss_items: list[dict[str, str]], today_str: str, api_key: str
) -> dict[str, Any] | None:
    """Call Gemini REST API to curate 5 structured Chinese briefing items."""
    headlines_text = "\n".join(
        f"- [{it['category']} | {it['source']}] {it['title']} — {it['summary']}"
        for it in rss_items
    )

    prompt = f"""你是一位专业的美股财经与 AI 科技早报主编。今天是 {today_str}。
请根据以下最新抓取的原始资讯，精选并编译成一份适合 4:5 竖屏短视频的中文早报 JSON（严格输出 JSON，不要 markdown 代码块）。

要求：
1. 必须包含 5 条新闻（`items` 数组长度必须为 5）：前 4 条覆盖 AI/科技热点与美股/财经动态，第 5 条可以是行业快讯或「事实边界/风险提示」（基于现有信息客观表述，不编造未出现的数据）。
2. 每条新闻包含：
   - `tag`: 简短分类标签（如 "AI 算力 · 美股风向"）
   - `title`: 12~20 字中文主标题
   - `summary`: 18~28 字一句话摘要（用于总览页）
   - `narration`: 65~95 字自然口语播报稿（适合女声朗读，避免英文缩写堆砌）
   - `hero_card`: {{"badge": "8~12字小标签", "title": "14~22字核心结论", "subtitle": "18~30字补充说明", "metric_value": "2~6字关键数字或状态", "metric_label": "4~6字指标名"}}
   - `detail_cards`: 2 个对象，每个 {{"label": "4字标签", "text": "18~30字要点"}}
   - `source`: "信源：具体媒体名"
3. 顶层字段：
   - `date`: "{today_str}"
   - `edition`: "AI 科技 · 美股财经早报"
   - `subtitle_badge`: "5 条硬核进展 · 1 条市场边界"
   - `source_footer`: "信源：公开财经与科技媒体 · 每日晨间编发"
   - `intro_narration`: 50~75 字开场白，概括今天 5 大看点
   - `outro_narration`: "以上就是今天的全部早报内容，感谢收看，我们明天见。"

原始资讯如下：
{headlines_text}
"""

    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.4,
            },
        }
    ).encode("utf-8")

    for model in GEMINI_MODELS:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            if (
                isinstance(parsed, dict)
                and isinstance(parsed.get("items"), list)
                and len(parsed["items"]) == 5
                and parsed.get("intro_narration")
            ):
                parsed["date"] = today_str
                return parsed
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
            continue
    return None


def build_daily_briefing(output_path: Path, sample_path: Path) -> Path:
    """Create today's briefing JSON; uses Gemini + live RSS if GEMINI_API_KEY is set."""
    today_str = datetime.now().strftime("%Y.%m.%d")
    rss_items = fetch_rss_items(limit_per_feed=5)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if rss_items:
        raw_path = output_path.with_name(f"{output_path.stem}_raw_headlines.json")
        raw_path.write_text(json.dumps(rss_items, ensure_ascii=False, indent=2), encoding="utf-8")

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    briefing_data: dict[str, Any] | None = None
    if api_key and rss_items:
        print("[0/4] Summarizing live RSS headlines via Gemini API...")
        briefing_data = _summarize_with_gemini(rss_items, today_str, api_key)

    if briefing_data is None:
        if api_key:
            print("[0/4] Gemini summary unavailable, falling back to sample template.")
        briefing_data = json.loads(sample_path.read_text(encoding="utf-8"))
        briefing_data["date"] = today_str

    output_path.write_text(json.dumps(briefing_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    root = Path(__file__).parent
    today_file = root / "data" / f"{datetime.now().strftime('%Y-%m-%d')}.json"
    result = build_daily_briefing(today_file, root / "data" / "sample_briefing.json")
    print(f"Briefing JSON ready: {result}")
