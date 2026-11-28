"""Fetch US market quotes (Indices, Mag 7, Semis, Crypto) and summarize with Gemini."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal
import yfinance as yf

from fetch_news import GEMINI_MODELS, RETRYABLE_HTTP_CODES, RETRY_DELAYS, _strip_json_fences, fetch_rss_items

ET_TZ = ZoneInfo("America/New_York")

SESSION_LABELS = {
    "open": "开盘快报",
    "intraday": "盘中巡检",
    "close": "收盘总结",
}

WATCHLIST: dict[str, list[tuple[str, str]]] = {
    "indices": [
        ("^GSPC", "标普500"),
        ("^IXIC", "纳斯达克"),
        ("^DJI", "道琼斯"),
        ("^VIX", "VIX恐慌"),
    ],
    "mag7": [
        ("NVDA", "英伟达"),
        ("AAPL", "苹果"),
        ("MSFT", "微软"),
        ("AMZN", "亚马逊"),
        ("GOOGL", "谷歌"),
        ("META", "Meta"),
        ("TSLA", "特斯拉"),
    ],
    "semis": [
        ("SOXX", "半导体ETF"),
        ("AVGO", "博通"),
        ("AMD", "AMD"),
        ("TSM", "台积电"),
        ("MU", "美光"),
        ("ASML", "阿斯麦"),
        ("INTC", "英特尔"),
    ],
    "crypto": [
        ("BTC-USD", "比特币"),
        ("MSTR", "MicroStrategy"),
        ("COIN", "Coinbase"),
        ("MARA", "MARA"),
        ("RIOT", "Riot"),
        ("HOOD", "Robinhood"),
    ],
}


def now_et() -> datetime:
    return datetime.now(ET_TZ)


def is_nyse_trading_day(target_date: date | None = None) -> bool:
    """Return True if target_date (ET) is a valid NYSE trading session."""
    d = target_date or now_et().date()
    nyse = mcal.get_calendar("NYSE")
    sched = nyse.schedule(start_date=d.isoformat(), end_date=d.isoformat())
    return not sched.empty


def detect_session(et_dt: datetime | None = None) -> str:
    """Infer open / intraday / close from current US Eastern time."""
    dt = et_dt or now_et()
    minutes = dt.hour * 60 + dt.minute
    if minutes < 11 * 60 + 30:
        return "open"
    if minutes < 15 * 60 + 45:
        return "intraday"
    return "close"


def _fetch_single_quote(symbol: str, name: str) -> dict[str, Any]:
    """Fetch latest price and % change vs previous close for a single symbol."""
    price: float | None = None
    prev_close: float | None = None

    ticker = yf.Ticker(symbol)
    try:
        fi = ticker.fast_info
        last_p = fi.get("lastPrice") or fi.get("regularMarketPrice")
        prev_c = fi.get("previousClose") or fi.get("regularMarketPreviousClose")
        if last_p is not None and prev_c is not None and float(prev_c) > 0:
            price = float(last_p)
            prev_close = float(prev_c)
    except Exception:
        pass

    if price is None or prev_close is None:
        try:
            hist = ticker.history(period="5d", interval="1d", auto_adjust=False)
            if not hist.empty:
                closes = hist["Close"].dropna()
                if len(closes) >= 2:
                    price = float(closes.iloc[-1])
                    prev_close = float(closes.iloc[-2])
                elif len(closes) == 1:
                    price = float(closes.iloc[-1])
                    prev_close = float(hist["Open"].iloc[-1])
        except Exception as exc:
            print(f"[Market] Failed {symbol}: {exc}")

    if price is None or not prev_close:
        return {
            "symbol": symbol,
            "display_symbol": symbol.replace("^", "").replace("-USD", ""),
            "name": name,
            "price": None,
            "change_pct": None,
        }

    change_pct = ((price - prev_close) / prev_close) * 100.0
    return {
        "symbol": symbol,
        "display_symbol": symbol.replace("^", "").replace("-USD", ""),
        "name": name,
        "price": round(price, 2),
        "prev_close": round(prev_close, 2),
        "change_pct": round(change_pct, 2),
    }


def fetch_all_quotes() -> dict[str, list[dict[str, Any]]]:
    """Fetch quotes for all tracked sectors."""
    result: dict[str, list[dict[str, Any]]] = {}
    for sector, items in WATCHLIST.items():
        sector_quotes = []
        for sym, name in items:
            sector_quotes.append(_fetch_single_quote(sym, name))
        result[sector] = sector_quotes
    return result


def fetch_mover_news(quotes: dict[str, list[dict[str, Any]]], top_n: int = 3) -> list[dict[str, str]]:
    """Fetch recent Yahoo news for the biggest movers in Mag 7, Semis, and Crypto."""
    candidates: list[dict[str, Any]] = []
    for sector in ("mag7", "semis", "crypto"):
        for q in quotes.get(sector, []):
            if q.get("change_pct") is not None and q["symbol"] not in ("SOXX", "BTC-USD"):
                candidates.append(q)

    candidates.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    headlines: list[dict[str, str]] = []
    for q in candidates[:top_n]:
        sym = q["symbol"]
        try:
            news_items = yf.Ticker(sym).news or []
            for entry in news_items[:2]:
                content = entry.get("content", {}) if isinstance(entry, dict) else {}
                title = (
                    entry.get("title")
                    or content.get("title")
                    or ""
                ).strip()
                if title:
                    headlines.append(
                        {
                            "symbol": sym,
                            "change_pct": f"{q['change_pct']:+.2f}%",
                            "title": title,
                        }
                    )
        except Exception:
            pass
    return headlines


def _format_quotes_for_prompt(quotes: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = []
    sector_names = {
        "indices": "大盘指数",
        "mag7": "Mag 7",
        "semis": "半导体",
        "crypto": "加密概念",
    }
    for key, label in sector_names.items():
        parts = []
        for q in quotes.get(key, []):
            if q.get("price") is None:
                continue
            parts.append(f"{q['display_symbol']}({q['name']}): {q['price']} ({q['change_pct']:+.2f}%)")
        if parts:
            lines.append(f"[{label}] " + " | ".join(parts))
    return "\n".join(lines)


def _summarize_market_with_gemini(
    session: str,
    et_now: datetime,
    quotes: dict[str, list[dict[str, Any]]],
    mover_news: list[dict[str, str]],
    rss_items: list[dict[str, str]],
    api_key: str,
) -> dict[str, Any] | None:
    session_cn = SESSION_LABELS.get(session, "盘面速览")
    quotes_text = _format_quotes_for_prompt(quotes)
    mover_text = "\n".join(
        f"- {m['symbol']} ({m['change_pct']}): {m['title']}" for m in mover_news
    ) or "无明显个股异动新闻"
    rss_text = "\n".join(
        f"- [{it['source']}] {it['title']}" for it in rss_items[:8]
    ) or "暂无"

    session_focus = {
        "open": "聚焦：三大指数开盘定调 + 盘前核心催化（宏观/财报/情绪） + 半导体与加密板块开盘强弱。",
        "intraday": "聚焦：盘中走势变化 + 领涨/领跌龙头及异动原因 + 半导体与加密概念资金风向。",
        "close": "聚焦：收盘涨跌格局 + 全天核心主线复盘 + 半导体/加密板块表现及盘后看点。",
    }.get(session, "")

    prompt = f"""你是一位风格犀利、数据驱动的美股财经 X 博主。现在是美东时间 {et_now.strftime('%Y-%m-%d %H:%M')}（{session_cn}）。
{session_focus}

请严格输出合法 JSON（不要 markdown 代码块），字段如下：
1. `card_headline`: 12~20 字中文卡片主标题（概括今日大盘与主线，不带引号）。
2. `sentiment`: 只能是 "偏强"、"震荡" 或 "偏弱" 之一。
3. `card_bullets`: 长度为 3 的数组，每条为一个对象 {{"label": "4~5字标签", "text": "18~28字精炼分析"}}，分别覆盖：
   - 第1条 label 为 "大盘风向"
   - 第2条 label 为 "半导体·AI"
   - 第3条 label 为 "加密概念"
4. `tweet_summary`: 55~85 字中文推文正文（不含标题和 hashtag），要求：
   - 像真实财经博主点评，信息密度高、有观点、不说废话；
   - 必须结合下方真实涨跌数据，最多只允许带 1 个 Cashtag（如 $NVDA，其余标的直接写代码或中文名，不要加 $ 符号）；
   - 严格控制在 85 个汉字以内，以适配 X 普通账号 280 加权字符限制。

实时行情数据：
{quotes_text}

异动个股新闻：
{mover_text}

财经头条参考：
{rss_text}
"""

    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.35,
            },
        }
    ).encode("utf-8")

    for model in GEMINI_MODELS:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        max_attempts = 1 + len(RETRY_DELAYS)
        for attempt in range(1, max_attempts + 1):
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                raw_text = body["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(_strip_json_fences(raw_text))
                if (
                    isinstance(parsed, dict)
                    and parsed.get("tweet_summary")
                    and isinstance(parsed.get("card_bullets"), list)
                    and len(parsed["card_bullets"]) >= 3
                ):
                    parsed["card_bullets"] = parsed["card_bullets"][:3]
                    print(f"[Market] Gemini ({model}) generated {session} summary.")
                    return parsed
                break
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="ignore")[:300]
                print(f"[Gemini:{model}] Attempt {attempt}/{max_attempts} HTTP {exc.code}: {err_body}")
                if exc.code in RETRYABLE_HTTP_CODES and attempt < max_attempts:
                    time.sleep(RETRY_DELAYS[attempt - 1])
                    continue
                break
            except Exception as exc:
                print(f"[Gemini:{model}] Attempt {attempt}/{max_attempts} Error: {exc}")
                if attempt < max_attempts:
                    time.sleep(RETRY_DELAYS[attempt - 1])
                    continue
                break
    return None


def _build_deterministic_summary(
    session: str, quotes: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Fallback summary built directly from live quotes if Gemini is unavailable."""
    idx_map = {q["display_symbol"]: q for q in quotes.get("indices", [])}
    spx = idx_map.get("GSPC", {})
    ixic = idx_map.get("IXIC", {})
    spx_chg = spx.get("change_pct") or 0.0
    ixic_chg = ixic.get("change_pct") or 0.0

    if ixic_chg >= 0.6 and spx_chg >= 0.4:
        sentiment = "偏强"
    elif ixic_chg <= -0.6 and spx_chg <= -0.4:
        sentiment = "偏弱"
    else:
        sentiment = "震荡"

    def _top_mover(sector: str) -> dict[str, Any] | None:
        valid = [q for q in quotes.get(sector, []) if q.get("change_pct") is not None]
        if not valid:
            return None
        return max(valid, key=lambda x: abs(x["change_pct"]))

    semi_top = _top_mover("semis")
    crypto_top = _top_mover("crypto")
    mag_top = _top_mover("mag7")

    semi_desc = (
        f"{semi_top['display_symbol']} {semi_top['change_pct']:+.2f}% 领动板块"
        if semi_top
        else "板块整体窄幅整理"
    )
    crypto_desc = (
        f"{crypto_top['display_symbol']} {crypto_top['change_pct']:+.2f}% 波动居前"
        if crypto_top
        else "随 BTC 区间震荡"
    )
    mag_desc = (
        f"${mag_top['display_symbol']}({mag_top['change_pct']:+.2f}%)"
        if mag_top
        else "权重股分化"
    )

    session_cn = SESSION_LABELS.get(session, "盘面")
    card_headline = f"纳指 {ixic_chg:+.2f}% · 半导体与加密板块聚焦"
    tweet_summary = (
        f"美股{session_cn}：标普 {spx_chg:+.2f}%、纳指 {ixic_chg:+.2f}%，大盘整体{sentiment}。"
        f"权重股 {mag_desc}；半导体关注 {semi_desc}；加密概念 {crypto_desc}。"
    )

    return {
        "card_headline": card_headline,
        "sentiment": sentiment,
        "card_bullets": [
            {"label": "大盘风向", "text": f"标普 {spx_chg:+.2f}%、纳指 {ixic_chg:+.2f}%，市场情绪{sentiment}。"},
            {"label": "半导体·AI", "text": f"{semi_desc}，科技权重 {mag_desc}。"},
            {"label": "加密概念", "text": f"{crypto_desc}，留意链上风险偏好变化。"},
        ],
        "tweet_summary": tweet_summary,
        "is_fallback_summary": True,
    }


def build_market_snapshot(session: str = "auto", output_path: Path | None = None) -> dict[str, Any]:
    """Fetch live US market quotes + news and generate structured summary JSON."""
    et_now = now_et()
    resolved_session = detect_session(et_now) if session == "auto" else session
    if resolved_session not in SESSION_LABELS:
        raise ValueError(f"Unsupported session: {resolved_session}")

    print(f"[Market] Fetching quotes for ET {et_now.strftime('%Y-%m-%d %H:%M')} ({resolved_session})...")
    quotes = fetch_all_quotes()
    mover_news = fetch_mover_news(quotes, top_n=3)

    rss_items = [
        it for it in fetch_rss_items(limit_per_feed=4) if it.get("category") == "美股财经"
    ]

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    summary: dict[str, Any] | None = None
    if api_key:
        summary = _summarize_market_with_gemini(
            resolved_session, et_now, quotes, mover_news, rss_items, api_key
        )

    if summary is None:
        print("[Market] Using deterministic live-quote summary (GEMINI_API_KEY missing or failed).")
        summary = _build_deterministic_summary(resolved_session, quotes)

    payload: dict[str, Any] = {
        "et_date": et_now.strftime("%Y-%m-%d"),
        "et_time": et_now.strftime("%H:%M"),
        "session": resolved_session,
        "session_cn": SESSION_LABELS[resolved_session],
        "quotes": quotes,
        "mover_news": mover_news,
        "summary": summary,
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[Market] Saved snapshot to {output_path}")

    return payload


if __name__ == "__main__":
    root = Path(__file__).parent
    et = now_et()
    sess = detect_session(et)
    out = root / "data" / f"{et.strftime('%Y-%m-%d')}_{sess}_market.json"
    build_market_snapshot(session=sess, output_path=out)
