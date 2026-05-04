"""Pydantic schemas shared across pipeline stages."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

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

    @model_validator(mode="after")
    def _validate_time_window(self) -> "Segment":
        if self.start < 0:
            raise ValueError("segment start must be >= 0")
        if self.end <= self.start:
            raise ValueError("segment end must be greater than start")
        return self


class ScriptPlan(BaseModel):
    title: str
    style: str = Field(description="Visual style notes, e.g. 'minimal flat, blue accents'")
    total_duration: float
    aspect: str = "9:16"
    segments: list[Segment]

    @model_validator(mode="after")
    def _validate_timeline(self) -> "ScriptPlan":
        if self.total_duration <= 0:
            raise ValueError("total_duration must be > 0")
        if not self.segments:
            raise ValueError("segments must not be empty")

        prev_start = -1.0
        prev_end = 0.0
        for seg in self.segments:
            if seg.start < prev_start:
                raise ValueError("segment starts must be monotonically increasing")
            if seg.start < prev_end - 0.05:
                raise ValueError("segments must not overlap")
            prev_start = seg.start
            prev_end = seg.end

        first = self.segments[0]
        last = self.segments[-1]
        tolerance = max(1.0, self.total_duration * 0.05)
        if first.start > 0.5:
            raise ValueError("timeline should start near 0s")
        if abs(last.end - self.total_duration) > tolerance:
            raise ValueError("last segment end should be close to total_duration")
        return self


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
