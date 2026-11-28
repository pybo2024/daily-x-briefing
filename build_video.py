"""One-click daily 4:5 X briefing video builder."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import imageio_ffmpeg

from fetch_news import build_daily_briefing
from post_to_x import format_briefing_tweet, post_video_to_x
from render_slides import render_all_slides
from tts_engine import (
    DEFAULT_RATE,
    DEFAULT_VOICE,
    synthesize_narration_segments,
    write_ass_subtitles,
)


def _run_ffmpeg(args: list[str]) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run(
        [ffmpeg_exe, "-y", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{proc.stderr[-1500:]}")


def _build_slide_clip(
    stage_pngs: list[Path],
    audio_mp3: Path,
    duration: float,
    out_mp4: Path,
) -> None:
    """Create one slide video clip with staged card reveals and audio."""
    num_stages = len(stage_pngs)
    # Allocate reveal steps over the first ~2.4s (or 45% of slide duration)
    reveal_window = min(2.4, duration * 0.45)
    step_dur = reveal_window / max(num_stages - 1, 1)

    concat_txt = out_mp4.with_suffix(".txt")
    lines: list[str] = []
    for idx, png in enumerate(stage_pngs):
        d = step_dur if idx < num_stages - 1 else max(0.5, duration - step_dur * (num_stages - 1))
        lines.append(f"file '{png.as_posix()}'\n")
        lines.append(f"duration {d:.4f}\n")
    lines.append(f"file '{stage_pngs[-1].as_posix()}'\n")
    concat_txt.write_text("".join(lines), encoding="utf-8")

    _run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt),
            "-i",
            str(audio_mp3),
            "-vf",
            "fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out_mp4),
        ]
    )


def build_video(
    briefing_json: Path,
    output_mp4: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    tts_backend: str = "auto",
    gpt_sovits_url: str | None = None,
    gpt_sovits_ref_audio: str | None = None,
    gpt_sovits_prompt_text: str | None = None,
) -> Path:
    data = json.loads(briefing_json.read_text(encoding="utf-8"))
    root = Path(__file__).parent
    work_dir = root / ".build_tmp"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare narration texts: Intro + 5 items (append outro to last item)
    items = data.get("items", [])[:5]
    narrations: list[str] = [data.get("intro_narration", "")]
    for idx, item in enumerate(items):
        text = item.get("narration", "")
        if idx == len(items) - 1 and data.get("outro_narration"):
            text = f"{text} {data['outro_narration']}"
        narrations.append(text)

    print("[1/4] Generating neural TTS voiceover...")
    audio_segments = synthesize_narration_segments(
        narrations,
        work_dir,
        voice=voice,
        rate=rate,
        tts_backend=tts_backend,
        gpt_sovits_url=gpt_sovits_url,
        gpt_sovits_ref_audio=gpt_sovits_ref_audio,
        gpt_sovits_prompt_text=gpt_sovits_prompt_text,
    )

    print("[2/4] Rendering 4:5 editorial card slides...")
    slide_stages = render_all_slides(data, work_dir)

    print("[3/4] Assembling slide clips & global subtitles...")
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip_paths: list[Path] = []
    global_cues: list[tuple[float, float, str]] = []
    time_offset = 0.0

    for idx, (stages, (mp3_path, dur, rel_cues)) in enumerate(
        zip(slide_stages, audio_segments)
    ):
        clip_path = clips_dir / f"clip_{idx:02d}.mp4"
        _build_slide_clip(stages, mp3_path, dur, clip_path)
        clip_paths.append(clip_path)
        for s, e, txt in rel_cues:
            global_cues.append((time_offset + s, time_offset + e, txt))
        time_offset += dur

    max_x_seconds = 115.0
    speed = time_offset / max_x_seconds if time_offset > max_x_seconds else 1.0
    if speed > 1.0:
        print(
            f"[4/4] Total duration {time_offset:.1f}s exceeds {max_x_seconds:.0f}s X limit; "
            f"speeding up {speed:.2f}x to fit under 2 minutes."
        )
        global_cues = [(s / speed, e / speed, txt) for s, e, txt in global_cues]

    ass_path = write_ass_subtitles(global_cues, work_dir / "subtitles.ass")

    concat_list = work_dir / "clips_concat.txt"
    concat_list.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in clip_paths), encoding="utf-8"
    )
    merged_raw = work_dir / "merged_raw.mp4"
    _run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(merged_raw),
        ]
    )

    print("[4/4] Burning Chinese subtitles & exporting final X-ready MP4...")
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    # Use relative path for ass filter on Windows to avoid drive-letter colon escaping issues
    ass_rel = ass_path.relative_to(root).as_posix()
    vf_filter = f"setpts=PTS/{speed:.6f},ass={ass_rel}" if speed > 1.0 else f"ass={ass_rel}"
    af_args: list[str] = []
    if speed > 1.0:
        # FFmpeg atempo supports 0.5..2.0 per stage
        rem = speed
        atempo_parts: list[str] = []
        while rem > 2.0:
            atempo_parts.append("atempo=2.0")
            rem /= 2.0
        atempo_parts.append(f"atempo={rem:.6f}")
        af_args = ["-af", ",".join(atempo_parts)]

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-i",
            str(merged_raw),
            "-vf",
            vf_filter,
            *af_args,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_mp4),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg subtitle burn failed:\n{proc.stderr[-1500:]}")

    return output_mp4


def main() -> None:
    parser = argparse.ArgumentParser(description="One-click daily X briefing video generator")
    parser.add_argument("--input", type=Path, default=None, help="Path to briefing JSON")
    parser.add_argument("--output", type=Path, default=None, help="Output MP4 path")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="edge-tts voice name")
    parser.add_argument("--rate", default=DEFAULT_RATE, help="Speech rate, e.g. +8%%")
    parser.add_argument(
        "--tts-backend",
        choices=["auto", "gpt-sovits", "edge-tts"],
        default="auto",
        help="TTS backend ('auto' uses GPT-SoVITS if configured, else edge-tts)",
    )
    parser.add_argument("--gpt-sovits-url", default=None, help="GPT-SoVITS api_v2.py base URL")
    parser.add_argument(
        "--gpt-sovits-ref-audio", default=None, help="Reference audio path on GPT-SoVITS server"
    )
    parser.add_argument(
        "--gpt-sovits-prompt-text", default=None, help="Transcript of reference audio"
    )
    parser.add_argument("--post-to-x", action="store_true", help="Auto-post video to X (Twitter)")
    parser.add_argument("--dry-run-x", action="store_true", help="Preview X post without uploading")
    args = parser.parse_args()

    root = Path(__file__).parent
    today_tag = datetime.now().strftime("%Y-%m-%d")

    if args.input is None:
        briefing_json = root / "data" / f"{today_tag}.json"
        print("[0/4] Preparing today's briefing JSON...")
        build_daily_briefing(briefing_json, root / "data" / "sample_briefing.json")
    else:
        briefing_json = args.input

    output_mp4 = args.output or (root / "output" / f"{today_tag}_daily_briefing.mp4")
    final_path = build_video(
        briefing_json,
        output_mp4,
        voice=args.voice,
        rate=args.rate,
        tts_backend=args.tts_backend,
        gpt_sovits_url=args.gpt_sovits_url,
        gpt_sovits_ref_audio=args.gpt_sovits_ref_audio,
        gpt_sovits_prompt_text=args.gpt_sovits_prompt_text,
    )
    print(f"DONE! Video saved to: {final_path}")

    if args.post_to_x or args.dry_run_x:
        data = json.loads(briefing_json.read_text(encoding="utf-8"))
        tweet_text = format_briefing_tweet(data)
        post_video_to_x(final_path, tweet_text, dry_run=args.dry_run_x)


if __name__ == "__main__":
    main()
