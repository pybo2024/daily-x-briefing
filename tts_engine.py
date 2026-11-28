"""TTS engine supporting GPT-SoVITS (api_v2.py) with automatic edge-tts fallback."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

import edge_tts
import imageio_ffmpeg

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = "+8%"


def get_audio_duration(audio_path: Path) -> float:
    """Measure audio duration in seconds using bundled ffmpeg."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run(
        [ffmpeg_exe, "-i", str(audio_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not match:
        return 5.0
    h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return h * 3600 + m * 60 + s


def split_caption_chunks(text: str, max_chars: int = 18) -> list[str]:
    """Split Chinese narration into readable short caption lines."""
    raw_parts = re.split(r"[，。！？；：、\n]+", text.strip())
    chunks: list[str] = []
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        while len(part) > max_chars:
            chunks.append(part[:max_chars])
            part = part[max_chars:]
        if part:
            chunks.append(part)
    return chunks or [text.strip()]


def _rate_to_speed_factor(rate: str) -> float:
    """Convert edge-tts style rate (e.g. '+8%', '-10%') into GPT-SoVITS speed_factor."""
    match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)%\s*", rate or "")
    if not match:
        return 1.0
    factor = 1.0 + float(match.group(1)) / 100.0
    return max(0.5, min(2.0, factor))


def _synth_gpt_sovits(
    text: str,
    out_wav: Path,
    *,
    api_url: str,
    ref_audio_path: str,
    prompt_text: str = "",
    prompt_lang: str = "zh",
    text_lang: str = "zh",
    speed_factor: float = 1.0,
    api_key: str = "",
) -> None:
    """Call GPT-SoVITS api_v2.py POST /tts endpoint and save WAV audio."""
    endpoint = f"{api_url.rstrip('/')}/tts"
    payload = json.dumps(
        {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "prompt_lang": prompt_lang,
            "prompt_text": prompt_text,
            "text_split_method": "cut5",
            "speed_factor": speed_factor,
            "media_type": "wav",
            "streaming_mode": False,
        }
    ).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            audio_bytes = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"GPT-SoVITS HTTP {exc.code}: {err_body}") from exc

    if len(audio_bytes) <= 44:
        raise RuntimeError(f"GPT-SoVITS returned empty/invalid audio ({len(audio_bytes)} bytes)")
    out_wav.write_bytes(audio_bytes)


async def _synth_segment_async(
    text: str, out_mp3: Path, voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE
) -> None:
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(out_mp3))


def synthesize_narration_segments(
    texts: Iterable[str],
    work_dir: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    *,
    tts_backend: str = "auto",
    gpt_sovits_url: str | None = None,
    gpt_sovits_ref_audio: str | None = None,
    gpt_sovits_prompt_text: str | None = None,
) -> list[tuple[Path, float, list[tuple[float, float, str]]]]:
    """Synthesize each slide's narration and compute relative subtitle cues.

    Uses GPT-SoVITS (api_v2.py) when configured (or tts_backend='gpt-sovits'),
    and automatically falls back to edge-tts on failure or when unconfigured.

    Returns list of (audio_path, duration_sec, [(rel_start, rel_end, caption_text), ...]).
    """
    audio_dir = work_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    api_url = (gpt_sovits_url or os.environ.get("GPT_SOVITS_API_URL", "")).strip()
    ref_audio = (gpt_sovits_ref_audio or os.environ.get("GPT_SOVITS_REF_AUDIO", "")).strip()
    prompt_text = (
        gpt_sovits_prompt_text
        if gpt_sovits_prompt_text is not None
        else os.environ.get("GPT_SOVITS_PROMPT_TEXT", "")
    ).strip()
    prompt_lang = os.environ.get("GPT_SOVITS_PROMPT_LANG", "zh").strip() or "zh"
    text_lang = os.environ.get("GPT_SOVITS_TEXT_LANG", "zh").strip() or "zh"
    api_key = os.environ.get("GPT_SOVITS_API_KEY", "").strip()
    speed_override = os.environ.get("GPT_SOVITS_SPEED_FACTOR", "").strip()
    speed_factor = float(speed_override) if speed_override else _rate_to_speed_factor(rate)

    backend = (tts_backend or os.environ.get("TTS_BACKEND", "auto")).strip().lower()
    if backend == "gpt-sovits":
        if not api_url or not ref_audio:
            raise RuntimeError(
                "tts_backend='gpt-sovits' requires GPT_SOVITS_API_URL and GPT_SOVITS_REF_AUDIO"
            )
        use_gpt_sovits = True
    elif backend == "edge-tts":
        use_gpt_sovits = False
    else:
        use_gpt_sovits = bool(api_url and ref_audio)

    if use_gpt_sovits:
        print(f"[1/4] Using GPT-SoVITS API ({api_url}) with ref_audio={ref_audio}")
    else:
        print(f"[1/4] Using edge-tts ({voice}, rate={rate})")

    results: list[tuple[Path, float, list[tuple[float, float, str]]]] = []
    for idx, text in enumerate(texts):
        audio_path: Path | None = None
        if use_gpt_sovits:
            wav_path = audio_dir / f"seg_{idx:02d}.wav"
            try:
                _synth_gpt_sovits(
                    text,
                    wav_path,
                    api_url=api_url,
                    ref_audio_path=ref_audio,
                    prompt_text=prompt_text,
                    prompt_lang=prompt_lang,
                    text_lang=text_lang,
                    speed_factor=speed_factor,
                    api_key=api_key,
                )
                audio_path = wav_path
            except Exception as exc:
                print(
                    f"[TTS] GPT-SoVITS failed on segment {idx} ({exc}); "
                    f"falling back to edge-tts ({voice})..."
                )

        if audio_path is None:
            mp3_path = audio_dir / f"seg_{idx:02d}.mp3"
            asyncio.run(_synth_segment_async(text, mp3_path, voice=voice, rate=rate))
            audio_path = mp3_path

        duration = get_audio_duration(audio_path)

        chunks = split_caption_chunks(text)
        total_weight = sum(max(len(c), 1) for c in chunks)
        cues: list[tuple[float, float, str]] = []
        cursor = 0.0
        for chunk in chunks:
            seg_dur = duration * (max(len(chunk), 1) / total_weight)
            cues.append((cursor, min(cursor + seg_dur, duration), chunk))
            cursor += seg_dur

        results.append((audio_path, duration, cues))

    return results


def _fmt_ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_ass_subtitles(
    cues: list[tuple[float, float, str]], out_ass: Path
) -> Path:
    """Write styled ASS subtitles for 1080x1350 portrait video."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1350
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,42,&H00FFFFFF,&H000000FF,&H001E1B18,&HD01E1B18,1,0,0,0,100,100,1,0,3,14,0,2,50,50,62,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, text in cues:
        clean = text.replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Default,,0,0,0,,{clean}\n"
        )
    out_ass.write_text("".join(lines), encoding="utf-8")
    return out_ass
