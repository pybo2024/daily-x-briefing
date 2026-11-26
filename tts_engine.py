"""Free open-source TTS via edge-tts + synchronized ASS subtitle generator."""

from __future__ import annotations

import asyncio
import re
import subprocess
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
) -> list[tuple[Path, float, list[tuple[float, float, str]]]]:
    """Synthesize each slide's narration and compute relative subtitle cues.

    Returns list of (mp3_path, duration_sec, [(rel_start, rel_end, caption_text), ...]).
    """
    audio_dir = work_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[Path, float, list[tuple[float, float, str]]]] = []
    for idx, text in enumerate(texts):
        mp3_path = audio_dir / f"seg_{idx:02d}.mp3"
        asyncio.run(_synth_segment_async(text, mp3_path, voice=voice, rate=rate))
        duration = get_audio_duration(mp3_path)

        chunks = split_caption_chunks(text)
        total_weight = sum(max(len(c), 1) for c in chunks)
        cues: list[tuple[float, float, str]] = []
        cursor = 0.0
        for chunk in chunks:
            seg_dur = duration * (max(len(chunk), 1) / total_weight)
            cues.append((cursor, min(cursor + seg_dur, duration), chunk))
            cursor += seg_dur

        results.append((mp3_path, duration, cues))

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
