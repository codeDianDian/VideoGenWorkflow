"""Stage 2: turn a raw script into a structured ScriptPlan via the LLM.

Mirrors the image's "Codex 拆解脚本 -> segments.json" step.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import settings
from .llm import ask_json
from .schemas import ScriptPlan

PLANNER_SYSTEM = (
    "You are a senior short-form-video director. You break a written script "
    "into 5-8 tightly-edited segments suitable for a 9:16 vertical video. "
    "Each segment must have its own visual identity and a precise time window. "
    "All times must be monotonically increasing and cover the full duration."
)


def _planner_user(script_text: str, total_duration: int, language_hint: str) -> str:
    return (
        f"Target total duration: {total_duration} seconds.\n"
        f"Language of subtitles & narration: {language_hint}.\n"
        f"Allowed scene_type values: hook | contrast | concept | steps | data | quote | cta.\n"
        f"The first segment must be scene_type=hook and last <=3s.\n"
        f"The final segment must be scene_type=cta.\n"
        f"Subtitles must be punchy (~10-22 chars per line for Chinese, ~6 words for English).\n\n"
        f"Script:\n----\n{script_text}\n----"
    )


def plan_script(
    script_text: str,
    total_duration: int | None = None,
    language_hint: str = "Chinese (zh-CN)",
) -> ScriptPlan:
    duration = total_duration or settings.default_duration
    plan = ask_json(PLANNER_SYSTEM, _planner_user(script_text, duration, language_hint), ScriptPlan)
    source_tail = max((seg.end for seg in plan.segments), default=float(plan.total_duration))
    source_duration = max(float(plan.total_duration), float(source_tail), 0.1)
    if abs(source_duration - float(duration)) > 0.01:
        scale = float(duration) / source_duration
        for seg in plan.segments:
            seg.start = round(seg.start * scale, 3)
            seg.end = round(seg.end * scale, 3)
    plan.total_duration = float(duration)
    for i, seg in enumerate(plan.segments):
        seg.index = i
    return ScriptPlan.model_validate(plan.model_dump())


def save_plan(plan: ScriptPlan, path: Path | None = None) -> Path:
    target = path or settings.build_dir / "segments.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return target


def load_plan(path: Path | None = None) -> ScriptPlan:
    target = path or settings.build_dir / "segments.json"
    return ScriptPlan.model_validate(json.loads(target.read_text(encoding="utf-8")))
