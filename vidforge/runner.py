"""Pipeline orchestrator. One VideoRun = one self-contained folder under runs/.

Each run produces this on-disk layout (all visible from the UI):

    runs/20260504-153012__demo_script/
    ├── script.md              # original input, copied
    ├── segments.json          # plan output (\u5206\u955c)
    ├── project/               # build output - the HTML/GSAP project
    │   ├── index.html
    │   ├── styles.css
    │   └── main.js
    ├── tts_segments/          # one mp3 per segment
    ├── narration.mp3          # mixed audio track
    ├── animation.mp4          # render output
    ├── final.mp4              # composite output
    ├── frames/                # auto-extracted previews
    └── manifest.json          # run metadata + per-stage status
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field
from rich.console import Console

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import build as build_stage
from . import composite as composite_stage
from . import ingest as ingest_stage
from . import plan as plan_stage
from . import render as render_stage
from . import tts as tts_stage
from .config import settings
from .schemas import SceneCode, ScriptPlan

console = Console()


class StageResult(BaseModel):
    name: str
    started_at: datetime
    finished_at: datetime | None = None
    success: bool = False
    error: str | None = None
    output: str | None = None

    @property
    def elapsed_s(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class RunManifest(BaseModel):
    slug: str
    script_path: str
    started_at: datetime
    finished_at: datetime | None = None
    success: bool = False
    final_video: str | None = None
    duration_s: float | None = None
    title: str | None = None
    stages: list[StageResult] = Field(default_factory=list)


def _slug_for(script_path: Path) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = script_path.stem.replace(" ", "_")[:40] or "untitled"
    return f"{ts}__{base}"


class VideoRun:
    """One pipeline invocation, isolated in its own folder."""

    def __init__(
        self,
        script_path: Path,
        run_dir: Path | None = None,
    ) -> None:
        self.script_path = Path(script_path).expanduser().resolve()
        if not self.script_path.exists():
            raise FileNotFoundError(self.script_path)
        self.slug = _slug_for(self.script_path)
        self.run_dir = run_dir or settings.runs_dir / self.slug
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = RunManifest(
            slug=self.slug,
            script_path=str(self.script_path),
            started_at=datetime.utcnow(),
        )
        self._save()

    @classmethod
    def from_title(cls, title: str) -> "VideoRun":
        """Bootstrap a run without a real script file (for hand-crafted seeds)."""
        inst = object.__new__(cls)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = title.replace(" ", "_")[:40] or "untitled"
        inst.slug = f"{ts}__{base}"
        inst.run_dir = settings.runs_dir / inst.slug
        inst.run_dir.mkdir(parents=True, exist_ok=True)
        inst.script_path = inst.run_dir / "script.md"
        inst.manifest = RunManifest(
            slug=inst.slug,
            script_path=str(inst.script_path),
            started_at=datetime.utcnow(),
        )
        inst._save()
        return inst

    def _save(self) -> None:
        (self.run_dir / "manifest.json").write_text(
            self.manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _stage(self, name: str, fn: Callable[[], Any]) -> Any:
        stage = StageResult(name=name, started_at=datetime.utcnow())
        self.manifest.stages.append(stage)
        self._save()
        console.log(f"[cyan]\u25b6\ufe0f  {name}[/cyan]  ({self.slug})")
        try:
            result = fn()
            stage.success = True
            stage.output = str(result) if result is not None else None
        except Exception as exc:  # noqa: BLE001
            stage.error = f"{type(exc).__name__}: {exc}"
            stage.finished_at = datetime.utcnow()
            self._save()
            raise
        finally:
            stage.finished_at = datetime.utcnow()
            self._save()
        return result

    def execute(
        self,
        *,
        duration: int | None = None,
        with_narration: bool = True,
        head: Path | None = None,
    ) -> RunManifest:
        try:
            shutil.copy(self.script_path, self.run_dir / "script.md")

            text: str = self._stage("ingest", lambda: ingest_stage.load_script(self.script_path))

            plan_path = self.run_dir / "segments.json"

            def _plan_fn():
                p = plan_stage.plan_script(text, total_duration=duration)
                plan_stage.save_plan(p, plan_path)
                return plan_path

            self._stage("plan", _plan_fn)
            plan = plan_stage.load_plan(plan_path)
            self.manifest.title = plan.title

            project_dir = self.run_dir / "project"
            self._stage("build", lambda: build_stage.build_project(plan, project_dir))

            anim_path = self.run_dir / "animation.mp4"

            def _render_fn():
                job = render_stage.render(project_dir, anim_path, plan)
                if not job.success:
                    raise RuntimeError(f"render: {job.log[-500:]}")
                return anim_path

            self._stage("render", _render_fn)

            narration_path: Path | None = None
            if with_narration and head is None:
                narration_path = self.run_dir / "narration.mp3"

                def _narrate_fn():
                    files = tts_stage.synth_segments(plan, out_dir=self.run_dir / "tts_segments")
                    tts_stage.assemble_track(plan, files, narration_path)  # type: ignore[arg-type]
                    return narration_path

                self._stage("narrate", _narrate_fn)

            final_path = self.run_dir / "final.mp4"

            def _composite_fn():
                if head is not None:
                    composite_stage.composite_with_pip(anim_path, head, final_path)
                else:
                    composite_stage.composite_no_pip(anim_path, final_path, narration_mp3=narration_path)
                return final_path

            self._stage("composite", _composite_fn)

            def _frames_fn():
                frames_dir = self.run_dir / "frames"
                frames_dir.mkdir(exist_ok=True)
                for seg in plan.segments:
                    t = (seg.start + seg.end) / 2
                    out = frames_dir / f"scene_{seg.index:02d}.jpg"
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-ss", str(t), "-i", str(final_path),
                            "-frames:v", "1", "-q:v", "2", str(out),
                        ],
                        check=True,
                    )
                return frames_dir

            self._stage("frames", _frames_fn)

            self.manifest.success = True
            self.manifest.final_video = str(final_path)
            self.manifest.duration_s = plan.total_duration
        finally:
            self.manifest.finished_at = datetime.utcnow()
            self._save()
        return self.manifest

    def execute_seeded(
        self,
        plan: ScriptPlan,
        scenes: dict[int, SceneCode],
        *,
        theme_css: str = "",
        with_narration: bool = True,
        head: Path | None = None,
    ) -> RunManifest:
        """Same pipeline as `execute()` but skips the LLM stages, using a
        hand-crafted plan + scenes (handy when you have no LLM credit, or want
        deterministic deterministic regression videos)."""
        try:
            self.manifest.title = plan.title
            (self.run_dir / "script.md").write_text(
                f"# {plan.title}\n\n(seeded run, no external script)\n", encoding="utf-8"
            )

            self._stage("ingest", lambda: "(seeded)")

            plan_path = self.run_dir / "segments.json"
            self._stage("plan", lambda: plan_stage.save_plan(plan, plan_path))

            project_dir = self.run_dir / "project"

            def _build():
                project_dir.mkdir(exist_ok=True)
                env = Environment(
                    loader=FileSystemLoader(str(settings.templates_dir)),
                    autoescape=select_autoescape(disabled_extensions=("j2",)),
                )
                ctx = {
                    "plan": plan,
                    "scenes": scenes,
                    "width": settings.width,
                    "height": settings.height,
                    "fps": settings.fps,
                    "scene_styles": theme_css + "\n\n" + "\n\n".join(s.css for s in scenes.values()),
                }
                (project_dir / "index.html").write_text(
                    env.get_template("base.html.j2").render(**ctx), encoding="utf-8"
                )
                (project_dir / "styles.css").write_text(
                    env.get_template("styles.css.j2").render(**ctx), encoding="utf-8"
                )
                (project_dir / "main.js").write_text(
                    env.get_template("main.js.j2").render(**ctx), encoding="utf-8"
                )
                return project_dir

            self._stage("build", _build)

            anim_path = self.run_dir / "animation.mp4"

            def _render_fn():
                job = render_stage.render(project_dir, anim_path, plan)
                if not job.success:
                    raise RuntimeError(f"render: {job.log[-500:]}")
                return anim_path

            self._stage("render", _render_fn)

            narration_path: Path | None = None
            if with_narration and head is None:
                narration_path = self.run_dir / "narration.mp3"

                def _narrate_fn():
                    files = tts_stage.synth_segments(plan, out_dir=self.run_dir / "tts_segments")
                    tts_stage.assemble_track(plan, files, narration_path)  # type: ignore[arg-type]
                    return narration_path

                self._stage("narrate", _narrate_fn)

            final_path = self.run_dir / "final.mp4"

            def _composite_fn():
                if head is not None:
                    composite_stage.composite_with_pip(anim_path, head, final_path)
                else:
                    composite_stage.composite_no_pip(anim_path, final_path, narration_mp3=narration_path)
                return final_path

            self._stage("composite", _composite_fn)

            def _frames_fn():
                frames_dir = self.run_dir / "frames"
                frames_dir.mkdir(exist_ok=True)
                for seg in plan.segments:
                    t = (seg.start + seg.end) / 2
                    out = frames_dir / f"scene_{seg.index:02d}.jpg"
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-ss", str(t), "-i", str(final_path),
                            "-frames:v", "1", "-q:v", "2", str(out),
                        ],
                        check=True,
                    )
                return frames_dir

            self._stage("frames", _frames_fn)

            self.manifest.success = True
            self.manifest.final_video = str(final_path)
            self.manifest.duration_s = plan.total_duration
        finally:
            self.manifest.finished_at = datetime.utcnow()
            self._save()
        return self.manifest


def list_runs() -> list[Path]:
    if not settings.runs_dir.exists():
        return []
    return sorted(
        [p for p in settings.runs_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists()],
        key=lambda p: p.name,
        reverse=True,
    )


def load_manifest(run_dir: Path) -> RunManifest:
    raw = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    return RunManifest.model_validate(raw)
