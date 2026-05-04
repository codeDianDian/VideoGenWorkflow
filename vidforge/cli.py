"""Typer CLI mirroring the 6-step workflow from the infographic."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import build as build_stage
from . import composite as composite_stage
from . import ingest as ingest_stage
from . import plan as plan_stage
from . import render as render_stage
from . import tts as tts_stage
from .config import settings

app = typer.Typer(
    add_completion=False,
    help="Script -> structured shots -> HTML/GSAP -> rendered MP4 -> final composite.",
)
console = Console()


@app.command()
def plan(
    script: Path,
    duration: int = typer.Option(None, "--duration", "-d", help="Target total duration in seconds"),
    language: str = typer.Option("Chinese (zh-CN)", "--language", "-l"),
) -> None:
    """Stage 2 - LLM turns a .md/.pdf script into segments.json."""
    text = ingest_stage.load_script(script)
    p = plan_stage.plan_script(text, total_duration=duration, language_hint=language)
    out = plan_stage.save_plan(p)
    console.print(f"[green]\u2713 segments.json -> {out}[/green]")
    table = Table(title=p.title, show_lines=False)
    for col in ("idx", "start", "end", "type", "subtitle"):
        table.add_column(col)
    for seg in p.segments:
        table.add_row(str(seg.index), f"{seg.start:.1f}", f"{seg.end:.1f}", seg.scene_type, seg.subtitle[:36])
    console.print(table)


@app.command()
def build(plan_path: Optional[Path] = typer.Option(None, "--plan")) -> None:
    """Stage 3 - LLM writes index.html / styles.css / main.js with GSAP."""
    p = plan_stage.load_plan(plan_path)
    project_dir = build_stage.build_project(p)
    console.print(f"[green]\u2713 project at {project_dir}[/green]")


@app.command()
def render(
    output: Path = typer.Option(Path("data/animation_full.mp4")),
    plan_path: Optional[Path] = typer.Option(None, "--plan"),
    project: Optional[Path] = typer.Option(None, "--project"),
) -> None:
    """Stage 4 - render the HTML project to MP4."""
    p = plan_stage.load_plan(plan_path)
    project_dir = project or settings.build_dir / "project"
    output.parent.mkdir(parents=True, exist_ok=True)
    job = render_stage.render(project_dir, output, p)
    if not job.success:
        console.print(f"[red]Render failed.[/red]\n{job.log[-2000:]}")
        raise typer.Exit(code=1)
    console.print(f"[green]\u2713 {output} ({p.total_duration:.1f}s)[/green]")


@app.command()
def narrate(
    plan_path: Optional[Path] = typer.Option(None, "--plan"),
    output: Path = typer.Option(Path("data/narration.mp3")),
) -> None:
    """Stage 4b - synth a voiceover track via edge-tts (no human口播 case)."""
    p = plan_stage.load_plan(plan_path)
    files = tts_stage.synth_segments(p)
    out = tts_stage.assemble_track(p, files, output)
    console.print(f"[green]\u2713 {out}[/green]")


@app.command()
def composite(
    animation: Path = typer.Option(Path("data/animation_full.mp4")),
    head: Optional[Path] = typer.Option(None, "--head", help="talking head MP4 to use as PIP + audio"),
    narration: Optional[Path] = typer.Option(None, "--narration"),
    subtitles: bool = typer.Option(True, "--subtitles/--no-subtitles"),
    output: Path = typer.Option(Path("data/final.mp4")),
    plan_path: Optional[Path] = typer.Option(None, "--plan"),
) -> None:
    """Stage 5 - compose the final video (with or without PIP)."""
    p = plan_stage.load_plan(plan_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if head:
        composite_stage.composite_with_pip(animation, head, output)
    else:
        composite_stage.composite_no_pip(animation, output, narration_mp3=narration)
    if subtitles:
        srt_path = output.with_suffix(".srt")
        composite_stage.write_srt(p, srt_path)
        burned = output.with_name(output.stem + "_subbed" + output.suffix)
        composite_stage.burn_subtitles(output, srt_path, burned)
        console.print(f"[green]\u2713 {burned}[/green]")
    else:
        console.print(f"[green]\u2713 {output}[/green]")


@app.command()
def auto(
    script: Path,
    duration: int = typer.Option(None, "--duration", "-d"),
    head: Optional[Path] = typer.Option(None, "--head"),
    no_narration: bool = typer.Option(False, "--no-narration"),
) -> None:
    """One-shot: full pipeline -> isolated runs/<slug>/ folder + manifest."""
    from .runner import VideoRun

    run_obj = VideoRun(script)
    try:
        manifest = run_obj.execute(
            duration=duration,
            with_narration=not no_narration,
            head=head,
        )
    except Exception:
        rdir = run_obj.run_dir
        console.print(
            f"[yellow]Diagnostics: {rdir / 'pipeline_error.log'}, "
            f"{rdir / 'render_error.log'}, {rdir / '_web_ui_error.txt'}[/yellow]"
        )
        raise
    if manifest.success:
        console.print(f"[bold green]\u2713 {manifest.final_video}[/bold green]")
    else:
        console.print(f"[red]pipeline failed - see {run_obj.run_dir / 'manifest.json'}[/red]")
        raise typer.Exit(code=1)


@app.command()
def resume(
    run_dir: Path,
    duration: int = typer.Option(None, "--duration", "-d", help="Target duration if re-running the plan stage"),
    head: Optional[Path] = typer.Option(None, "--head"),
    no_narration: bool = typer.Option(False, "--no-narration"),
) -> None:
    """Continue a failed or partial run: skip stages that already succeeded (see manifest + on-disk artifacts)."""
    from .runner import VideoRun

    run = VideoRun.from_run_dir(run_dir)
    try:
        manifest = run.execute(
            duration=duration,
            with_narration=not no_narration,
            head=head,
            resume=True,
        )
    except Exception:
        rdir = run.run_dir
        console.print(
            f"[yellow]Diagnostics: {rdir / 'pipeline_error.log'}, "
            f"{rdir / 'render_error.log'}, {rdir / '_web_ui_error.txt'}[/yellow]"
        )
        raise
    if manifest.success:
        console.print(f"[bold green]✓ resumed -> {manifest.final_video}[/bold green]")
    else:
        console.print(f"[red]resume failed — see {run.run_dir / 'manifest.json'}[/red]")
        raise typer.Exit(code=1)


@app.command()
def watch() -> None:
    """Daemon: watch inbox/ and process every .md/.txt/.pdf dropped in."""
    from .watcher import watch as _watch

    _watch()


@app.command()
def ui(port: int = typer.Option(8501, "--port", "-p")) -> None:
    """Launch the local Streamlit dashboard at http://localhost:<port>."""
    import subprocess
    import sys

    ui_path = Path(__file__).parent / "ui.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(ui_path),
        "--server.headless=true",
        "--server.port", str(port),
        "--browser.gatherUsageStats=false",
    ]
    console.print(f"[cyan]\u2192 http://localhost:{port}[/cyan]")
    subprocess.run(cmd, check=False)


@app.command()
def run(
    script: Path,
    duration: int = typer.Option(None, "--duration", "-d"),
    head: Optional[Path] = typer.Option(None, "--head"),
    auto_narration: bool = typer.Option(False, "--auto-narration"),
    output: Path = typer.Option(Path("data/final.mp4")),
) -> None:
    """End-to-end: ingest -> plan -> build -> render -> (narrate) -> composite."""
    text = ingest_stage.load_script(script)
    p = plan_stage.plan_script(text, total_duration=duration)
    plan_stage.save_plan(p)
    project_dir = build_stage.build_project(p)
    anim_path = settings.data_dir / "animation_full.mp4"
    job = render_stage.render(project_dir, anim_path, p)
    if not job.success:
        console.print(f"[red]Render failed.[/red]\n{job.log[-2000:]}")
        raise typer.Exit(code=1)

    narration_path = None
    if auto_narration and not head:
        files = tts_stage.synth_segments(p)
        narration_path = tts_stage.assemble_track(p, files, settings.data_dir / "narration.mp3")

    if head:
        composite_stage.composite_with_pip(anim_path, head, output)
    else:
        composite_stage.composite_no_pip(anim_path, output, narration_mp3=narration_path)

    srt_path = output.with_suffix(".srt")
    composite_stage.write_srt(p, srt_path)
    burned = output.with_name(output.stem + "_subbed" + output.suffix)
    composite_stage.burn_subtitles(output, srt_path, burned)
    console.print(f"[bold green]\u2713 final video: {burned}[/bold green]")


if __name__ == "__main__":
    app()
