"""Stage 5: composite the rendered animation with optional talking-head PIP and audio.

Two modes (matching the workflow image):
  A. with_pip - overlay talking_head.mp4 in the bottom-right safe zone, use its
                audio as the main soundtrack.
  B. no_pip   - just normalise the animation (optional auto-narration mp3 mix-in).

Outputs an MP4 ready to upload.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from .config import settings

console = Console()


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found on PATH.")
    return path


def composite_with_pip(
    animation_mp4: Path,
    talking_head_mp4: Path,
    output_path: Path,
    pip_size: int = 360,
    pip_margin: int = 48,
    use_head_audio: bool = True,
) -> Path:
    """Overlay talking head at the bottom-right safe zone."""
    x = settings.width - pip_size - pip_margin
    y = settings.height - pip_size - pip_margin

    filter_complex = (
        f"[1:v]scale={pip_size}:{pip_size}:force_original_aspect_ratio=increase,"
        f"crop={pip_size}:{pip_size},"
        f"format=yuva420p,"
        f"geq=lum='p(X,Y)':a='if(lt(pow(X-W/2,2)+pow(Y-H/2,2),pow(W/2,2)),255,0)'[pip];"
        f"[0:v][pip]overlay={x}:{y}:format=auto[outv]"
    )

    cmd = [
        _ffmpeg(), "-y",
        "-i", str(animation_mp4),
        "-i", str(talking_head_mp4),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", ("1:a" if use_head_audio else "0:a?"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(output_path),
    ]
    console.log(f"[cyan]$[/cyan] ffmpeg PIP overlay -> {output_path.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return output_path


def composite_no_pip(
    animation_mp4: Path,
    output_path: Path,
    narration_mp3: Path | None = None,
) -> Path:
    cmd: list[str] = [_ffmpeg(), "-y", "-i", str(animation_mp4)]
    if narration_mp3:
        cmd += ["-i", str(narration_mp3)]
        cmd += [
            "-filter_complex",
            "[1:a]apad[aout]",
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
        ]
    else:
        cmd += ["-c:v", "copy", "-an"]
    cmd += [str(output_path)]
    console.log(f"[cyan]$[/cyan] ffmpeg passthrough -> {output_path.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return output_path


def burn_subtitles(input_mp4: Path, srt_path: Path, output_path: Path) -> Path:
    cmd = [
        _ffmpeg(), "-y",
        "-i", str(input_mp4),
        "-vf", f"subtitles='{srt_path}':force_style='FontName=PingFang SC,FontSize=24,Outline=2,BorderStyle=1'",
        "-c:a", "copy",
        str(output_path),
    ]
    console.log(f"[cyan]$[/cyan] ffmpeg burn subs -> {output_path.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return output_path


def write_srt(plan, srt_path: Path) -> Path:
    def _ts(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: list[str] = []
    for i, seg in enumerate(plan.segments, start=1):
        lines.append(str(i))
        lines.append(f"{_ts(seg.start)} --> {_ts(seg.end)}")
        lines.append(seg.subtitle)
        lines.append("")
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    return srt_path
