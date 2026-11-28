"""Render a 4:5 (1080x1350) static US market pulse PNG card using Playwright."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

MARKET_TEMPLATE_PATH = Path(__file__).parent / "templates" / "market_card.html"


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def _chg_classes(chg: float | None) -> tuple[str, str]:
    if chg is None:
        return "flat", "bg-flat"
    if chg > 0:
        return "up", "bg-up"
    if chg < 0:
        return "down", "bg-down"
    return "flat", "bg-flat"


def _fmt_price(price: float | None, is_btc: bool = False) -> str:
    if price is None:
        return "--"
    if is_btc or price >= 1000:
        return f"{price:,.0f}" if price >= 10000 else f"{price:,.2f}"
    return f"{price:.2f}"


def _fmt_chg(chg: float | None) -> str:
    if chg is None:
        return "--"
    return f"{chg:+.2f}%"


def render_market_card(snapshot: dict[str, Any], output_png: Path) -> Path:
    """Render snapshot dict into a 1080x1350 PNG image."""
    output_png.parent.mkdir(parents=True, exist_ok=True)
    summary = snapshot.get("summary", {})
    quotes = snapshot.get("quotes", {})

    sentiment = summary.get("sentiment", "震荡")
    sentiment_cls = (
        "sentiment-up"
        if sentiment == "偏强"
        else ("sentiment-down" if sentiment == "偏弱" else "sentiment-flat")
    )

    bullets_html = "".join(
        f'<div class="bullet-row">'
        f'  <span class="bullet-tag">{_esc(b.get("label", ""))}</span>'
        f'  <span class="bullet-text">{_esc(b.get("text", ""))}</span>'
        f'</div>'
        for b in summary.get("card_bullets", [])[:3]
    )

    indices_html = ""
    for q in quotes.get("indices", []):
        txt_cls, _ = _chg_classes(q.get("change_pct"))
        indices_html += (
            f'<div class="index-card">'
            f'  <div class="index-name">{_esc(q.get("name", q.get("display_symbol")))}</div>'
            f'  <div class="index-price">{_fmt_price(q.get("price"))}</div>'
            f'  <div class="index-chg {txt_cls}">{_fmt_chg(q.get("change_pct"))}</div>'
            f'</div>'
        )

    sector_defs = [
        ("mag7", "Mag 7 科技巨头", "核心权重风向标", "ticker-grid"),
        ("semis", "半导体与算力芯片", "SOXX · AI 硬件链", "ticker-grid"),
        ("crypto", "加密概念股 & BTC", "数字资产联动", "ticker-grid cols-3"),
    ]

    sectors_html = ""
    for key, title, sub, grid_cls in sector_defs:
        chips = ""
        for q in quotes.get(key, []):
            _, bg_cls = _chg_classes(q.get("change_pct"))
            is_btc = q.get("symbol") == "BTC-USD"
            price_str = _fmt_price(q.get("price"), is_btc=is_btc)
            if q.get("price") is not None:
                price_str = f"${price_str}"
            chips += (
                f'<div class="ticker-chip">'
                f'  <div class="ticker-left">'
                f'    <span class="ticker-sym">{_esc(q.get("display_symbol"))}</span>'
                f'    <span class="ticker-name">{_esc(q.get("name"))}</span>'
                f'  </div>'
                f'  <div class="ticker-right">'
                f'    <span class="ticker-price">{price_str}</span>'
                f'    <span class="chg-badge {bg_cls}">{_fmt_chg(q.get("change_pct"))}</span>'
                f'  </div>'
                f'</div>'
            )
        sectors_html += (
            f'<div class="sector-panel">'
            f'  <div class="sector-title"><span>{title}</span><span class="sector-sub">{sub}</span></div>'
            f'  <div class="{grid_cls}">{chips}</div>'
            f'</div>'
        )

    top_left = f'美股盘面速览 · {snapshot.get("session_cn", "盘面")}'
    top_right = f'ET {snapshot.get("et_date", "")} {snapshot.get("et_time", "")}'
    headline = summary.get("card_headline", "美股盘面速览")

    template_url = MARKET_TEMPLATE_PATH.resolve().as_uri()
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)

        page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        page.goto(template_url, wait_until="load")
        page.evaluate(
            """([tl, tr, sentText, sentCls, hl, bullets, idxHtml, secHtml]) => {
                document.getElementById('top-left').textContent = tl;
                document.getElementById('top-right').textContent = tr;
                const pill = document.getElementById('sentiment-pill');
                pill.textContent = sentText;
                pill.className = 'sentiment-pill ' + sentCls;
                document.getElementById('headline').textContent = hl;
                document.getElementById('bullets-box').innerHTML = bullets;
                document.getElementById('indices-grid').innerHTML = idxHtml;
                document.getElementById('sectors-wrap').innerHTML = secHtml;
            }""",
            [
                top_left,
                top_right,
                sentiment,
                sentiment_cls,
                headline,
                bullets_html,
                indices_html,
                sectors_html,
            ],
        )
        page.screenshot(path=str(output_png))
        browser.close()

    print(f"[Render] Market card saved to {output_png}")
    return output_png
