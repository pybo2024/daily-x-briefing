"""Render 4:5 (1080x1350) editorial briefing slides with staged card reveals."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

TEMPLATE_PATH = Path(__file__).parent / "templates" / "briefing.html"


def _esc(val: str) -> str:
    return html.escape(str(val or ""))


def _build_overview_html(data: dict[str, Any], stage: int) -> tuple[str, str, str, str]:
    items = data.get("items", [])[:5]
    cards_html = []
    for idx, item in enumerate(items, start=1):
        hidden = "hidden-stage" if stage < idx else ""
        cards_html.append(
            f'<div class="overview-item {hidden}">'
            f'  <div class="overview-num">{idx:02d}</div>'
            f'  <div class="overview-body">'
            f'    <div class="overview-title">{_esc(item["title"])}</div>'
            f'    <div class="overview-desc">{_esc(item["summary"])}</div>'
            f'  </div>'
            f'</div>'
        )

    main_html = (
        f'<div class="pill">DAILY BRIEFING · {_esc(data.get("date", ""))}</div>'
        f'<h1 class="title">{_esc(data.get("edition", "AI 科技 · 美股财经早报"))}</h1>'
        f'<div class="subtitle">{_esc(data.get("subtitle_badge", "5 条硬核进展 · 1 条市场边界"))}</div>'
        f'<div class="overview-list">{"".join(cards_html)}</div>'
    )
    return (
        f'{_esc(data.get("edition", ""))} · {_esc(data.get("date", ""))}',
        "HEADLINES",
        main_html,
        data.get("source_footer", "信源：公开财经与科技媒体"),
    )


def _build_item_html(
    data: dict[str, Any], item_idx: int, stage: int
) -> tuple[str, str, str, str]:
    item = data["items"][item_idx]
    hero = item.get("hero_card", {})
    details = item.get("detail_cards", [])

    hero_hidden = "hidden-stage" if stage < 1 else ""
    detail_blocks = []
    for d_idx, card in enumerate(details, start=2):
        d_hidden = "hidden-stage" if stage < d_idx else ""
        detail_blocks.append(
            f'<div class="detail-card {d_hidden}">'
            f'  <div class="detail-label">{_esc(card.get("label", ""))}</div>'
            f'  <div class="detail-text">{_esc(card.get("text", ""))}</div>'
            f'</div>'
        )

    main_html = (
        f'<div class="pill">{_esc(item.get("tag", ""))}</div>'
        f'<h1 class="title">{_esc(item.get("title", ""))}</h1>'
        f'<div class="hero-card {hero_hidden}">'
        f'  <div class="hero-left">'
        f'    <div class="hero-badge">{_esc(hero.get("badge", ""))}</div>'
        f'    <div class="hero-title">{_esc(hero.get("title", ""))}</div>'
        f'    <div class="hero-sub">{_esc(hero.get("subtitle", ""))}</div>'
        f'  </div>'
        f'  <div class="hero-metric">'
        f'    <div class="hero-metric-val">{_esc(hero.get("metric_value", ""))}</div>'
        f'    <div class="hero-metric-lbl">{_esc(hero.get("metric_label", ""))}</div>'
        f'  </div>'
        f'</div>'
        f'<div class="detail-grid">{"".join(detail_blocks)}</div>'
    )
    return (
        f'{_esc(data.get("edition", ""))} · {_esc(data.get("date", ""))}',
        f"ITEM {item_idx + 1:02d}",
        main_html,
        item.get("source", data.get("source_footer", "")),
    )


def render_all_slides(data: dict[str, Any], work_dir: Path) -> list[list[Path]]:
    """Render staged PNG frames for intro + each item slide.

    Returns a list of slides, where each slide is a list of stage PNG paths.
    """
    slides_dir = work_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    template_url = TEMPLATE_PATH.resolve().as_uri()

    all_slides: list[list[Path]] = []
    items = data.get("items", [])[:5]
    total_items = len(items)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)

        page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        page.goto(template_url, wait_until="load")

        # 1. Intro overview slide (stages 0..total_items)
        intro_stages: list[Path] = []
        for stage in range(0, total_items + 1):
            top_l, top_r, main_html, footer_src = _build_overview_html(data, stage)
            page.evaluate(
                """([tl, tr, mh, fs, fp]) => {
                    document.getElementById('top-left').textContent = tl;
                    document.getElementById('top-right').textContent = tr;
                    document.getElementById('main-content').innerHTML = mh;
                    document.getElementById('footer-source').textContent = fs;
                    document.getElementById('footer-page').textContent = fp;
                }""",
                [top_l, top_r, main_html, footer_src, f"00 / {total_items:02d}"],
            )
            out_png = slides_dir / f"slide_00_stage_{stage}.png"
            page.screenshot(path=str(out_png))
            intro_stages.append(out_png)
        all_slides.append(intro_stages)

        # 2. Item slides (stages 0..1+len(detail_cards))
        for idx, item in enumerate(items):
            num_details = len(item.get("detail_cards", []))
            max_stage = 1 + num_details
            item_stages: list[Path] = []
            for stage in range(0, max_stage + 1):
                top_l, top_r, main_html, footer_src = _build_item_html(data, idx, stage)
                page.evaluate(
                    """([tl, tr, mh, fs, fp]) => {
                        document.getElementById('top-left').textContent = tl;
                        document.getElementById('top-right').textContent = tr;
                        document.getElementById('main-content').innerHTML = mh;
                        document.getElementById('footer-source').textContent = fs;
                        document.getElementById('footer-page').textContent = fp;
                    }""",
                    [top_l, top_r, main_html, footer_src, f"{idx + 1:02d} / {total_items:02d}"],
                )
                out_png = slides_dir / f"slide_{idx + 1:02d}_stage_{stage}.png"
                page.screenshot(path=str(out_png))
                item_stages.append(out_png)
            all_slides.append(item_stages)

        browser.close()

    return all_slides
