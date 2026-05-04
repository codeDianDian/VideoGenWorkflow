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


def assemble_track(plan: ScriptPlan, segment_files: list[Path], output_path: Path) -> Path:
    """Build a single audio track that places each segment's voice at its start time."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH.")

    inputs: list[str] = []
    filter_parts: list[str] = []
    for i, (seg, f) in enumerate(zip(plan.segments, segment_files)):
        inputs += ["-i", str(f)]
        delay_ms = int(seg.start * 1000)
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[a{i}]")
    mix_inputs = "".join(f"[a{i}]" for i in range(len(segment_files)))
    filter_complex = ";".join(filter_parts) + f";{mix_inputs}amix=inputs={len(segment_files)}:normalize=0[aout]"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        str(output_path),
    ]
    console.log(f"[cyan]$[/cyan] ffmpeg amix -> {output_path.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return output_path
