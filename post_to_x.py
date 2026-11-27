"""Auto-post daily 4:5 briefing video to X (Twitter) using Tweepy."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import tweepy

X_MAX_WEIGHTED_CHARS = 280


def x_weighted_length(text: str) -> int:
    """Estimate X (Twitter) weighted character length.

    X counts most Latin/ASCII characters as 1 and CJK / emoji characters (> U+10FF) as 2.
    """
    total = 0
    for ch in text:
        total += 1 if ord(ch) <= 0x10FF else 2
    return total


def _truncate_weighted(text: str, max_weight: int) -> str:
    """Truncate text so its X weighted length does not exceed max_weight."""
    if x_weighted_length(text) <= max_weight:
        return text
    ellipsis = "…"
    budget = max(0, max_weight - x_weighted_length(ellipsis))
    out: list[str] = []
    used = 0
    for ch in text:
        w = 1 if ord(ch) <= 0x10FF else 2
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out).rstrip() + ellipsis


def format_briefing_tweet(
    briefing_data: dict[str, Any], max_weighted_len: int = X_MAX_WEIGHTED_CHARS
) -> str:
    """Format a concise, X-limit-safe Chinese post from the daily briefing JSON."""
    date_str = str(briefing_data.get("date", "")).strip()
    edition = str(briefing_data.get("edition", "AI 科技 · 美股财经早报")).strip()
    items = briefing_data.get("items", [])[:5]

    header = f"📊 {edition} ({date_str})" if date_str else f"📊 {edition}"
    footer = "#AI #美股 #科技早报"

    titles = [str(it.get("title", "")).strip() for it in items if it.get("title")]
    if not titles:
        return _truncate_weighted(f"{header}\n\n{footer}", max_weighted_len)

    # 1) Try including all 5 headlines
    body_all = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    candidate = f"{header}\n\n{body_all}\n\n{footer}"
    if x_weighted_length(candidate) <= max_weighted_len:
        return candidate

    # 2) Try top N headlines + pointer to full video
    for count in (4, 3, 2, 1):
        if count >= len(titles):
            continue
        more_line = f"……完整 {len(titles)} 条速览见视频"
        subset_lines = [f"{i + 1}. {t}" for i, t in enumerate(titles[:count])]
        candidate = "\n".join([header, "", *subset_lines, more_line, "", footer])
        if x_weighted_length(candidate) <= max_weighted_len:
            return candidate

    # 3) Fallback: truncate top 3 headlines to fit strictly within limit
    fixed_overhead = x_weighted_length(f"{header}\n\n1. \n2. \n3. \n\n{footer}")
    per_item_budget = max(12, (max_weighted_len - fixed_overhead) // min(3, len(titles)))
    trimmed = [
        f"{i + 1}. {_truncate_weighted(t, per_item_budget)}"
        for i, t in enumerate(titles[:3])
    ]
    candidate = "\n".join([header, "", *trimmed, "", footer])
    return _truncate_weighted(candidate, max_weighted_len)


def get_x_credentials() -> tuple[dict[str, str] | None, list[str]]:
    """Read X API OAuth 1.0a credentials from environment variables."""
    mapping = {
        "api_key": ("X_API_KEY", "TWITTER_API_KEY"),
        "api_secret": ("X_API_SECRET", "TWITTER_API_SECRET"),
        "access_token": ("X_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN"),
        "access_token_secret": ("X_ACCESS_TOKEN_SECRET", "TWITTER_ACCESS_TOKEN_SECRET"),
    }
    creds: dict[str, str] = {}
    missing: list[str] = []
    for field, env_names in mapping.items():
        val = ""
        for name in env_names:
            val = os.environ.get(name, "").strip()
            if val:
                break
        if val:
            creds[field] = val
        else:
            missing.append(env_names[0])
    if missing:
        return None, missing
    return creds, []


def _write_step_summary(tweet_url: str, text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n### X (Twitter) Auto-Post\n")
            fh.write(f"- **Tweet URL**: {tweet_url}\n")
            fh.write(f"\n```text\n{text}\n```\n")
    except OSError as exc:
        print(f"[X] Warning: failed to write GITHUB_STEP_SUMMARY: {exc}")


def post_video_to_x(
    video_path: Path,
    text: str,
    *,
    dry_run: bool = False,
    skip_if_unconfigured: bool = False,
) -> dict[str, Any] | None:
    """Upload MP4 video via X v1.1 chunked media upload and publish Tweet via v2."""
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    size_mb = video_path.stat().st_size / (1024 * 1024)
    if size_mb <= 0:
        raise ValueError(f"Video file is empty: {video_path}")

    weighted_len = x_weighted_length(text)
    if weighted_len > X_MAX_WEIGHTED_CHARS:
        raise ValueError(
            f"Tweet text exceeds X weighted limit ({weighted_len}/{X_MAX_WEIGHTED_CHARS}):\n{text}"
        )

    if dry_run:
        print("[X] Dry run mode — skipping API upload.")
        print(f"[X] Video: {video_path} ({size_mb:.2f} MB)")
        print(f"[X] Weighted length: {weighted_len}/{X_MAX_WEIGHTED_CHARS}")
        print(f"[X] Tweet text:\n{text}")
        return {
            "dry_run": True,
            "video": str(video_path),
            "weighted_length": weighted_len,
            "text": text,
        }

    creds, missing = get_x_credentials()
    if creds is None:
        msg = f"Missing X API credentials in environment: {', '.join(missing)}"
        if skip_if_unconfigured:
            print(f"[X] Skipping auto-post ({msg}).")
            return None
        raise RuntimeError(msg)

    print(f"[X] Uploading video ({size_mb:.2f} MB) to X media endpoint...")
    auth = tweepy.OAuth1UserHandler(
        creds["api_key"],
        creds["api_secret"],
        creds["access_token"],
        creds["access_token_secret"],
    )
    api = tweepy.API(auth, wait_on_rate_limit=True)
    media = api.media_upload(
        filename=str(video_path),
        media_category="tweet_video",
        chunked=True,
        wait_for_async_finalize=True,
    )
    media_id = str(media.media_id)
    print(f"[X] Video uploaded & processed (media_id={media_id}). Publishing tweet...")

    client = tweepy.Client(
        consumer_key=creds["api_key"],
        consumer_secret=creds["api_secret"],
        access_token=creds["access_token"],
        access_token_secret=creds["access_token_secret"],
        wait_on_rate_limit=True,
    )
    response = client.create_tweet(text=text, media_ids=[media_id])
    tweet_id = str(response.data["id"])
    tweet_url = f"https://x.com/i/web/status/{tweet_id}"
    print(f"[X] Published! Tweet URL: {tweet_url}")
    _write_step_summary(tweet_url, text)
    return {
        "tweet_id": tweet_id,
        "url": tweet_url,
        "media_id": media_id,
        "text": text,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Post daily briefing MP4 to X (Twitter)")
    parser.add_argument("--video", type=Path, default=None, help="Path to MP4 video")
    parser.add_argument("--briefing", type=Path, default=None, help="Path to briefing JSON")
    parser.add_argument("--text", default=None, help="Custom tweet text (overrides briefing JSON)")
    parser.add_argument("--dry-run", action="store_true", help="Preview tweet without posting")
    parser.add_argument(
        "--skip-if-unconfigured",
        action="store_true",
        help="Exit cleanly if X API credentials are not set",
    )
    args = parser.parse_args()

    root = Path(__file__).parent
    today_tag = datetime.now().strftime("%Y-%m-%d")
    video_path = args.video or (root / "output" / f"{today_tag}_daily_briefing.mp4")
    briefing_path = args.briefing or (root / "data" / f"{today_tag}.json")

    if args.text:
        tweet_text = args.text
    elif briefing_path.exists():
        data = json.loads(briefing_path.read_text(encoding="utf-8"))
        tweet_text = format_briefing_tweet(data)
    else:
        tweet_text = f"📊 AI 科技 · 美股财经早报 ({today_tag})\n\n#AI #美股 #科技早报"

    post_video_to_x(
        video_path,
        tweet_text,
        dry_run=args.dry_run,
        skip_if_unconfigured=args.skip_if_unconfigured,
    )


if __name__ == "__main__":
    main()
