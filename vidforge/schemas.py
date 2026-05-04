"""Pydantic schemas shared across pipeline stages."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

SceneType = Literal[
    "hook",        # 0-3s opening
    "contrast",    # before/after, vs.
    "concept",     # whiteboard / explainer
    "steps",       # 1/2/3 step card
    "data",        # numbers / chart
    "quote",       # pull quote
    "cta",         # call to action / outro
]


class Segment(BaseModel):
    index: int
    start: float = Field(description="Start time in seconds")
    end: float = Field(description="End time in seconds")
    scene_type: SceneType
    subtitle: str = Field(description="On-screen subtitle text (\u2264 22 chars per line preferred)")
    narration: str = Field(description="Voiceover line for TTS (or matching the talking-head audio)")
    visuals: str = Field(description="Short prompt describing what should appear / animate")
    keywords: list[str] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.1, self.end - self.start)


class ScriptPlan(BaseModel):
    title: str
    style: str = Field(description="Visual style notes, e.g. 'minimal flat, blue accents'")
    total_duration: float
    aspect: str = "9:16"
    segments: list[Segment]


class SceneCode(BaseModel):
    """LLM output for one scene."""

    index: int
    html: str = Field(description="HTML fragment for the scene (no <html>/<body> wrappers)")
    css: str = Field(description="Scoped CSS for the scene; selectors should start with .scene-{index}")
    js: str = Field(
        description="JS body that builds a GSAP timeline for this scene. "
        "Receives `tl` (gsap.timeline) and `root` (HTMLElement) in scope."
    )


class RenderJob(BaseModel):
    project_dir: str
    output_path: str
    width: int
    height: int
    fps: int
    duration: float
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    success: bool = False
    log: str = ""
