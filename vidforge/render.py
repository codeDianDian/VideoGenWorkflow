"""Stage 4: render the HTML project to MP4.

Two backends:
  * hyperframes - if `npx hyperframes` is on PATH and LIBTV_ACCESS_KEY is set.
  * playwright  - opens a Chromium at the target viewport, records video, hands
                  the webm to ffmpeg.

Both backends honour the project's stage size and total duration.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import settings
from .schemas import RenderJob, ScriptPlan

console = Console()


def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. `brew install ffmpeg` or your OS equivalent.")


def render_with_hyperframes(project_dir: Path, output_path: Path, plan: ScriptPlan) -> RenderJob:
    job = RenderJob(
        project_dir=str(project_dir),
        output_path=str(output_path),
        width=settings.width,
        height=settings.height,
        fps=settings.fps,
        duration=plan.total_duration,
    )
    if shutil.which("npx") is None:
        raise RuntimeError("npx not found. Install Node.js to use the hyperframes backend.")
    if not settings.libtv_access_key:
        raise RuntimeError("LIBTV_ACCESS_KEY missing. Set it in .env or use renderer=playwright.")

    cmd_lint = ["npx", "hyperframes", "lint", str(project_dir)]
    cmd_render = [
        "npx", "hyperframes", "render",
        "--input", str(project_dir),
        "--output", str(output_path),
        "--width", str(settings.width),
        "--height", str(settings.height),
        "--fps", str(settings.fps),
        "--duration", f"{plan.total_duration}",
        "--quality", "high",
    ]
    env = {"LIBTV_ACCESS_KEY": settings.libtv_access_key}
    log_lines: list[str] = []
    for cmd in (cmd_lint, cmd_render):
        console.log(f"[cyan]$[/cyan] {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True, env={**env})
        log_lines.append(proc.stdout)
        log_lines.append(proc.stderr)
        if proc.returncode != 0:
            job.log = "\n".join(log_lines)
            return job

    job.success = output_path.exists()
    job.log = "\n".join(log_lines)
    job.finished_at = datetime.utcnow()
    return job


async def _record_with_playwright(project_dir: Path, output_path: Path, plan: ScriptPlan) -> str:
    from playwright.async_api import async_playwright

    workdir = output_path.parent / "_pw"
    workdir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await browser.new_context(
            viewport={"width": settings.width, "height": settings.height},
            device_scale_factor=1,
            record_video_dir=str(workdir),
            record_video_size={"width": settings.width, "height": settings.height},
        )
        page = await ctx.new_page()
        url = f"file://{(project_dir / 'index.html').resolve()}?autoplay=1"
        await page.goto(url, wait_until="load")
        await page.wait_for_function("document.body.dataset.ready === '1'", timeout=30_000)
        await page.evaluate("window.__startVideo && window.__startVideo()")
        await page.wait_for_function(
            "document.body.dataset.done === '1'",
            timeout=max(int((plan.total_duration + 45) * 1000), 120_000),
        )
        await page.close()
        await ctx.close()
        await browser.close()

    webm_files = list(workdir.glob("*.webm"))
    if not webm_files:
        raise RuntimeError("Playwright did not produce a video file.")
    return str(webm_files[-1])


def render_with_playwright(project_dir: Path, output_path: Path, plan: ScriptPlan) -> RenderJob:
    _ensure_ffmpeg()
    job = RenderJob(
        project_dir=str(project_dir),
        output_path=str(output_path),
        width=settings.width,
        height=settings.height,
        fps=settings.fps,
        duration=plan.total_duration,
    )
    webm = asyncio.run(_record_with_playwright(project_dir, output_path, plan))
    cmd = [
        "ffmpeg", "-y",
        "-i", webm,
        "-r", str(settings.fps),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    console.log(f"[cyan]$[/cyan] {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    job.log = proc.stdout + proc.stderr
    job.success = output_path.exists()
    job.finished_at = datetime.utcnow()
    return job


def render(project_dir: Path, output_path: Path, plan: ScriptPlan) -> RenderJob:
    if settings.renderer == "hyperframes":
        return render_with_hyperframes(project_dir, output_path, plan)
    return render_with_playwright(project_dir, output_path, plan)
