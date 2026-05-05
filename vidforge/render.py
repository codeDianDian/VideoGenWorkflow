"""Stage 4: render the HTML project to MP4.

Two backends:
  * hyperframes - if `npx hyperframes` is on PATH and LIBTV_ACCESS_KEY is set.
  * playwright  - opens a Chromium at the target viewport, records video, hands
                  the webm to ffmpeg.

Both backends honour the project's stage size and total duration.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import settings
from .schemas import RenderJob, ScriptPlan

console = Console()


def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. `brew install ffmpeg` or your OS equivalent.")


def _playwright_done_timeout_ms(plan: ScriptPlan) -> int:
    """Wait for ``dataset.done`` until timeline tail + buffer (GSAP often exceeds nominal plan)."""
    tail = max((float(s.end) for s in plan.segments), default=0.0)
    nominal = max(float(plan.total_duration), tail)
    buf = float(settings.playwright_done_buffer_s)
    ms = int((nominal + buf) * 1000)
    floor = int(settings.playwright_done_timeout_floor_ms)
    return max(ms, floor, 60_000)


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
    env = {**os.environ, "LIBTV_ACCESS_KEY": settings.libtv_access_key or ""}
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

    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    workdir = output_path.parent / "_pw" / f"{output_path.stem}_{stamp}"
    workdir.mkdir(parents=True, exist_ok=True)

    err_log = output_path.parent / "render_error.log"
    browser_lines: list[str] = []
    url = f"file://{(project_dir / 'index.html').resolve()}?autoplay=1"
    ready_timeout = max(int(settings.playwright_ready_timeout_ms), 30_000)
    done_timeout_ms = _playwright_done_timeout_ms(plan)

    def _on_console(msg) -> None:
        browser_lines.append(f"{msg.type}: {msg.text}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await browser.new_context(
            viewport={"width": settings.width, "height": settings.height},
            device_scale_factor=1,
            record_video_dir=str(workdir),
            record_video_size={"width": settings.width, "height": settings.height},
        )
        page = await ctx.new_page()
        page.on("console", _on_console)
        page_error = asyncio.get_running_loop().create_future()

        def _pageerror(err) -> None:
            msg = f"{err!r}"
            browser_lines.append(f"pageerror: {msg}")
            if not page_error.done():
                page_error.set_result(msg)

        page.on("pageerror", _pageerror)

        async def _wait_for_page_state(expr: str, *, timeout: int, label: str) -> None:
            wait_task = asyncio.create_task(page.wait_for_function(expr, timeout=timeout))
            done, _ = await asyncio.wait(
                {wait_task, page_error},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if page_error in done:
                wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wait_task
                raise RuntimeError(f"Browser pageerror while waiting for {label}: {page_error.result()}")
            await wait_task

        ready_val = ""
        done_val = ""
        try:
            await page.goto(url, wait_until="load")
            await _wait_for_page_state(
                "document.body.dataset.ready === '1'",
                timeout=ready_timeout,
                label="dataset.ready",
            )
            await page.evaluate("window.__startVideo && window.__startVideo()")
            await _wait_for_page_state(
                "document.body.dataset.done === '1'",
                timeout=done_timeout_ms,
                label="dataset.done",
            )
        except Exception:
            try:
                ready_val = await page.evaluate("() => document.body?.dataset?.ready ?? ''")
                done_val = await page.evaluate("() => document.body?.dataset?.done ?? ''")
            except Exception as snap_exc:
                ready_val = f"(read failed: {snap_exc})"
                done_val = ""

            diag = "\n".join(
                [
                    f"{datetime.utcnow().isoformat()}Z",
                    f"project_dir: {project_dir}",
                    f"url: {url}",
                    f"viewport: {settings.width}x{settings.height}",
                    f"plan.total_duration: {plan.total_duration}",
                    f"segment ends (max): {max((s.end for s in plan.segments), default=0)}",
                    f"ready_timeout_ms: {ready_timeout}",
                    f"done_timeout_ms: {done_timeout_ms}",
                    f"dataset.ready at failure: {ready_val!r}",
                    f"dataset.done at failure: {done_val!r}",
                    "",
                    "---- browser console (last 120) ----",
                    *browser_lines[-120:],
                    "",
                    traceback.format_exc(),
                ]
            )
            err_log.write_text(diag, encoding="utf-8")
            console.print(f"[red]Playwright render failed — wrote {err_log.name}[/red]")
            await page.close()
            await ctx.close()
            await browser.close()
            raise

        await page.close()
        await ctx.close()
        await browser.close()

    webm_files = list(workdir.glob("*.webm"))
    if not webm_files:
        msg = "Playwright did not produce a video file."
        err_log.write_text(
            f"{datetime.utcnow().isoformat()}Z\n{msg}\n\n"
            + "\n".join(browser_lines[-120:]),
            encoding="utf-8",
        )
        raise RuntimeError(msg)
    return str(webm_files[-1])


def render_with_playwright(project_dir: Path, output_path: Path, plan: ScriptPlan) -> RenderJob:
    _ensure_ffmpeg()
    for stale_log in ("render_error.log", "render_ffmpeg_error.log"):
        stale_path = output_path.parent / stale_log
        if stale_path.exists():
            stale_path.unlink()
    job = RenderJob(
        project_dir=str(project_dir),
        output_path=str(output_path),
        width=settings.width,
        height=settings.height,
        fps=settings.fps,
        duration=plan.total_duration,
    )
    try:
        webm = asyncio.run(_record_with_playwright(project_dir, output_path, plan))
    except Exception as exc:
        hint = f"{type(exc).__name__}: {exc}\nSee render_error.log in the run directory for browser console + dataset state."
        job.log = hint
        job.finished_at = datetime.utcnow()
        console.print(f"[red]{hint}[/red]")
        return job

    cmd = [
        "ffmpeg", "-y",
        "-i", webm,
        "-r", str(settings.fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        os.environ.get("VIDFORGE_FFMPEG_PRESET", "fast"),
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    console.log(f"[cyan]$[/cyan] {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    job.log = proc.stdout + proc.stderr
    if proc.returncode != 0:
        fe = output_path.parent / "render_ffmpeg_error.log"
        fe.write_text(job.log + "\n", encoding="utf-8")
        console.print(f"[red]ffmpeg failed — wrote {fe.name}[/red]")
    job.success = output_path.exists() and proc.returncode == 0
    job.finished_at = datetime.utcnow()
    return job


def render(project_dir: Path, output_path: Path, plan: ScriptPlan) -> RenderJob:
    if settings.renderer == "hyperframes":
        return render_with_hyperframes(project_dir, output_path, plan)
    return render_with_playwright(project_dir, output_path, plan)
