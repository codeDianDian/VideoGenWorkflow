"""File-watcher daemon. Polls inbox/ and processes any new .md / .txt / .pdf.

Drop a script in `inbox/`, watcher creates a fresh run under `runs/<ts>__<slug>/`,
runs the full pipeline, and renames the input to `*.processed` so it is not
picked up twice.
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path

from rich.console import Console

from .config import settings
from .runner import VideoRun

console = Console()

ALLOWED = {".md", ".txt", ".pdf"}
PROCESSED_SUFFIX = ".processed"
FAILED_SUFFIX = ".failed"
IGNORE_NAMES = {"README.md", "readme.md", ".gitkeep"}


def _candidates() -> list[Path]:
    if not settings.inbox_dir.exists():
        return []
    out: list[Path] = []
    for p in settings.inbox_dir.iterdir():
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.name in IGNORE_NAMES:
            continue
        if p.name.endswith(PROCESSED_SUFFIX) or p.name.endswith(FAILED_SUFFIX):
            continue
        if p.suffix.lower() not in ALLOWED:
            continue
        out.append(p)
    return out


def _is_settled(p: Path, threshold_ms: int = 1500) -> bool:
    """Heuristic: file size hasn't changed in the last `threshold_ms` ms."""
    s1 = p.stat().st_size
    time.sleep(threshold_ms / 1000)
    if not p.exists():
        return False
    s2 = p.stat().st_size
    return s1 == s2


def _process(path: Path) -> None:
    console.log(f"\n[bold cyan]\u25b6  {path.name}[/bold cyan]")
    try:
        run = VideoRun(path)
        manifest = run.execute()
    except Exception as exc:  # noqa: BLE001
        console.log(f"[red]\u2717 failed: {exc}[/red]")
        console.print_exception()
        path.rename(path.with_name(path.name + FAILED_SUFFIX))
        return
    if manifest.success:
        console.log(f"[green]\u2713 done: {manifest.final_video}[/green]")
    else:
        console.log(f"[red]\u2717 pipeline reported failure[/red]")
    path.rename(path.with_name(path.name + PROCESSED_SUFFIX))


def watch(poll_interval: float | None = None) -> None:
    poll = poll_interval or settings.watch_interval_s
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    console.log(f"[bold]Watching {settings.inbox_dir}[/bold] (every {poll:.1f}s)")
    console.log(f"  artifacts \u2192 {settings.runs_dir}")

    seen: set[Path] = set()
    try:
        while True:
            for cand in _candidates():
                if cand in seen:
                    continue
                if not _is_settled(cand):
                    continue
                seen.add(cand)
                _process(cand)
            time.sleep(poll)
    except KeyboardInterrupt:
        console.log("[yellow]watcher stopped[/yellow]")
