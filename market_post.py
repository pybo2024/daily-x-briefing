"""End-to-end US market pulse generator: fetch quotes, summarize, render PNG card, and post to X."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from fetch_market import (
    build_market_snapshot,
    detect_session,
    is_nyse_trading_day,
    now_et,
)
from post_to_x import X_MAX_WEIGHTED_CHARS, _truncate_weighted, post_image_to_x, x_weighted_length
from render_market_card import render_market_card


def _limit_cashtags(text: str, max_cashtags: int = 1) -> str:
    """Keep at most max_cashtags $SYMBOL tokens; strip '$' from extra cashtags."""
    count = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        if count <= max_cashtags:
            return match.group(0)
        return match.group(1)

    return re.sub(r"\$([A-Za-z]{1,6})\b", _repl, text)


def format_market_tweet(
    snapshot: dict[str, Any], max_weighted_len: int = X_MAX_WEIGHTED_CHARS
) -> str:
    """Format a concise, 280-weighted-char-safe Chinese tweet for standard X accounts."""
    session_cn = snapshot.get("session_cn", "盘面速览")
    et_date = snapshot.get("et_date", "")
    summary = snapshot.get("summary", {})

    reason = _limit_cashtags(str(summary.get("tweet_reason", "")).strip(), max_cashtags=1)
    takeaway = _limit_cashtags(str(summary.get("tweet_takeaway", "")).strip(), max_cashtags=0)
    fallback_body = _limit_cashtags(str(summary.get("tweet_summary", "")).strip(), max_cashtags=1)

    header = f"📊 美股{session_cn} ({et_date})"
    footer = "#美股 #半导体 #加密概念"

    if reason and takeaway:
        takeaway_line = f"💡 {takeaway}"
        candidate = f"{header}\n\n{reason}\n\n{takeaway_line}\n\n{footer}"
        if x_weighted_length(candidate) <= max_weighted_len:
            return candidate
        # Trim reason first so the sharp takeaway is preserved intact
        fixed = f"{header}\n\n\n\n{takeaway_line}\n\n{footer}"
        reason_budget = max_weighted_len - x_weighted_length(fixed)
        if reason_budget >= 30:
            trimmed_reason = _truncate_weighted(reason, reason_budget)
            return f"{header}\n\n{trimmed_reason}\n\n{takeaway_line}\n\n{footer}"

    body = fallback_body or reason or takeaway
    candidate = f"{header}\n\n{body}\n\n{footer}"
    if x_weighted_length(candidate) <= max_weighted_len:
        return candidate

    overhead = x_weighted_length(f"{header}\n\n\n\n{footer}")
    budget = max(40, max_weighted_len - overhead)
    trimmed_body = _truncate_weighted(body, budget)
    return _truncate_weighted(f"{header}\n\n{trimmed_body}\n\n{footer}", max_weighted_len)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate US market pulse card (open/intraday/close) and optionally post to X"
    )
    parser.add_argument(
        "--session",
        choices=["auto", "open", "intraday", "close"],
        default="auto",
        help="Market session to summarize (default: auto by ET clock)",
    )
    parser.add_argument(
        "--skip-non-trading",
        action="store_true",
        help="Exit cleanly if today (ET) is not an NYSE trading day",
    )
    parser.add_argument("--post-to-x", action="store_true", help="Upload card PNG and tweet to X")
    parser.add_argument("--dry-run", action="store_true", help="Preview tweet without calling X API")
    parser.add_argument(
        "--skip-if-unconfigured",
        action="store_true",
        help="Exit cleanly if X API credentials are not set",
    )
    args = parser.parse_args()

    et_now = now_et()
    if args.skip_non_trading and not is_nyse_trading_day(et_now.date()):
        print(f"[Market] {et_now.date()} is not an NYSE trading day. Skipping.")
        return

    session = detect_session(et_now) if args.session == "auto" else args.session
    root = Path(__file__).parent
    tag = f"{et_now.strftime('%Y-%m-%d')}_{session}"

    snapshot_path = root / "data" / f"{tag}_market.json"
    card_path = root / "output" / f"{tag}_market.png"

    snapshot = build_market_snapshot(session=session, output_path=snapshot_path)
    render_market_card(snapshot, card_path)
    tweet_text = format_market_tweet(snapshot)

    print(f"[Market] Tweet preview ({x_weighted_length(tweet_text)}/{X_MAX_WEIGHTED_CHARS}):\n{tweet_text}")

    if args.post_to_x or args.dry_run:
        post_image_to_x(
            card_path,
            tweet_text,
            dry_run=args.dry_run,
            skip_if_unconfigured=args.skip_if_unconfigured,
        )


if __name__ == "__main__":
    main()
