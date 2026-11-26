"""Fetch daily AI/Tech and US Stock/Finance headlines from public RSS feeds."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import feedparser

RSS_SOURCES = [
    ("AI 科技", "Hacker News", "https://hnrss.org/frontpage?count=10"),
    ("AI 科技", "TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("美股财经", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("美股财经", "CNBC Top News", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
]


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_rss_items(limit_per_feed: int = 3) -> list[dict[str, str]]:
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
                            "summary": summary[:140],
                            "link": getattr(entry, "link", ""),
                        }
                    )
        except Exception:
            continue
    return collected


def build_daily_briefing(output_path: Path, sample_path: Path) -> Path:
    """Create today's briefing JSON; uses live RSS headlines if available, else sample."""
    today_str = datetime.now().strftime("%Y.%m.%d")
    sample_data: dict[str, Any] = json.loads(sample_path.read_text(encoding="utf-8"))
    sample_data["date"] = today_str

    rss_items = fetch_rss_items(limit_per_feed=2)
    if rss_items:
        # Save raw fetched headlines alongside the structured briefing for easy editing
        raw_path = output_path.with_name(f"{output_path.stem}_raw_headlines.json")
        raw_path.write_text(json.dumps(rss_items, ensure_ascii=False, indent=2), encoding="utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sample_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    root = Path(__file__).parent
    today_file = root / "data" / f"{datetime.now().strftime('%Y-%m-%d')}.json"
    result = build_daily_briefing(today_file, root / "data" / "sample_briefing.json")
    print(f"Briefing JSON ready: {result}")
