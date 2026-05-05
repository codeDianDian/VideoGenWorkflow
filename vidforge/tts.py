"""Optional Stage 4b: synth narration with edge-tts when there is no human口播.

Produces one MP3 per segment (so it can be cut to the segment's window) and a
concatenated narration.mp3 aligned with segment.start times.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from .config import settings
from .schemas import ScriptPlan

console = Console()


async def _synth_one(text: str, voice: str, out_path: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(str(out_path))


def synth_segments(plan: ScriptPlan, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or settings.build_dir / "narration"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for seg in plan.segments:
        target = out_dir / f"seg_{seg.index:02d}.mp3"
        console.log(f"[cyan]TTS[/cyan] seg {seg.index}: {seg.narration[:40]}...")
        asyncio.run(_synth_one(seg.narration, settings.tts_voice, target))
        paths.append(target)
    return paths


def assemble_track(
    plan: ScriptPlan,
    segment_files: list[Path],
    output_path: Path,
    *,
    guard_gap_s: float = 0.08,
    segment_options: dict[int, dict[str, float]] | None = None,
) -> Path:
    """Build one narration track without allowing adjacent segment audio to overlap."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH.")

    segment_options = segment_options or {}
    inputs: list[str] = []
    filter_parts: list[str] = []
    for i, (seg, f) in enumerate(zip(plan.segments, segment_files)):
        inputs += ["-i", str(f)]
        opts = segment_options.get(int(seg.index), {}) or segment_options.get(str(seg.index), {})  # type: ignore[arg-type]
        shift_s = float(opts.get("audio_shift_ms", 0.0)) / 1000.0
        local_guard_gap_s = float(opts.get("guard_gap_s", guard_gap_s))
        fade_out_s = max(0.0, float(opts.get("fade_out_ms", 120.0)) / 1000.0)
        volume = max(0.0, float(opts.get("volume_pct", 100.0)) / 100.0)
        delay_ms = max(0, int((float(seg.start) + shift_s) * 1000))
        trim_s = max(0.12, float(seg.end - seg.start) - local_guard_gap_s)
        chain = f"[{i}:a]atrim=0:{trim_s:.3f},asetpts=PTS-STARTPTS"
        if volume != 1.0:
            chain += f",volume={volume:.4f}"
        if trim_s > 0.28 and fade_out_s > 0:
            fade_s = min(fade_out_s, trim_s / 3)
            chain += f",afade=t=out:st={trim_s - fade_s:.3f}:d={fade_s:.3f}"
        chain += f",adelay={delay_ms}|{delay_ms}[a{i}]"
        filter_parts.append(chain)
    mix_inputs = "".join(f"[a{i}]" for i in range(len(segment_files)))
    total_s = max(float(plan.total_duration), max((float(s.end) for s in plan.segments), default=0.0))
    filter_complex = (
        ";".join(filter_parts)
        + f";{mix_inputs}amix=inputs={len(segment_files)}:normalize=0:duration=longest,"
        + f"atrim=0:{total_s:.3f},asetpts=PTS-STARTPTS[aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-t", f"{total_s:.3f}",
        str(output_path),
    ]
    console.log(f"[cyan]$[/cyan] ffmpeg amix -> {output_path.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return output_path
