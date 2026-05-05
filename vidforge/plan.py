"""Stage 2: turn a raw script into a structured ScriptPlan via the LLM.

Mirrors the image's "Codex 拆解脚本 -> segments.json" step.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import settings
from .llm import ask_json
from .schemas import ScriptPlan, Segment

PLANNER_SYSTEM = (
    "You are a senior short-form-video director. You break a written script "
    "into 5-8 tightly-edited segments suitable for a 9:16 vertical video. "
    "Each segment must have its own visual identity and a precise time window. "
    "All times must be monotonically increasing and cover the full duration. "
    "When you describe the visual style, prefer a polished editorial motion-design language over a simple, cute, "
    "or worksheet-like look. "
    "TTS discipline: each segment's `narration` must be short enough to be spoken comfortably inside "
    "(segment.end - segment.start) with ~0.15s headroom — run-on lines get clipped in post. "
    "Rule of thumb: Chinese narration ≈ at most 3 spoken characters per second of that window; "
    "English ≈ at most 2 words per second. Prefer one crisp sentence per segment."
)


def _is_chinese_primary(language_hint: str) -> bool:
    h = (language_hint or "").lower()
    return "chinese" in h or "zh" in h or "中文" in language_hint or "简体" in language_hint or "繁体" in language_hint


def _estimate_narration_seconds(text: str, *, chinese: bool) -> float:
    """Conservative TTS duration heuristic (no audio synth)."""
    t = (text or "").strip()
    if not t:
        return 0.12
    cps_zh = float(os.environ.get("VIDFORGE_PLAN_TTS_CHARS_PER_SEC_ZH", "3.6"))
    wps_en = float(os.environ.get("VIDFORGE_PLAN_TTS_WORDS_PER_SEC_EN", "2.3"))
    if chinese:
        n = sum(1 for c in t if not c.isspace())
        return max(0.15, n / cps_zh)
    words = [w for w in t.replace("/", " ").split() if w]
    return max(0.15, len(words) / wps_en)


def expand_plan_timeline_for_tts(
    plan: ScriptPlan,
    *,
    language_hint: str,
) -> ScriptPlan:
    """Widen segment windows (and shift the tail timeline) so heuristic TTS fits before edge-tts runs.

    Disabled when ``VIDFORGE_PLAN_EXPAND_TTS=0``. Slightly lengthens total_duration vs the target scale
    when narration is dense — better than silent clipping in composite.
    """
    if os.environ.get("VIDFORGE_PLAN_EXPAND_TTS", "1").lower() in {"0", "false", "no"}:
        return plan

    guard = float(os.environ.get("VIDFORGE_PLAN_TTS_GUARD_S", "0.14"))
    gap = float(os.environ.get("VIDFORGE_PLAN_SEGMENT_GAP_S", "0.06"))
    chinese = _is_chinese_primary(language_hint)

    segs = sorted(plan.segments, key=lambda s: (float(s.start), int(s.index)))
    mutable: list[Segment] = [s.model_copy(deep=True) for s in segs]

    for pos in range(len(mutable)):
        seg = mutable[pos]
        win = float(seg.end) - float(seg.start)
        need = _estimate_narration_seconds(seg.narration, chinese=chinese) + guard
        if need <= win + 1e-3:
            continue
        extra = need - win
        new_end = float(seg.end) + extra
        mutable[pos] = seg.model_copy(update={"end": round(new_end, 3)})
        if pos + 1 < len(mutable):
            nxt = mutable[pos + 1]
            min_start = new_end + gap
            if float(nxt.start) < min_start - 1e-6:
                delta = min_start - float(nxt.start)
                for j in range(pos + 1, len(mutable)):
                    s2 = mutable[j]
                    mutable[j] = s2.model_copy(
                        update={
                            "start": round(float(s2.start) + delta, 3),
                            "end": round(float(s2.end) + delta, 3),
                        }
                    )

    last_end = max(float(s.end) for s in mutable)
    for i, s in enumerate(mutable):
        mutable[i] = s.model_copy(update={"index": i})

    return ScriptPlan.model_validate(
        {
            **plan.model_dump(mode="json"),
            "segments": [s.model_dump(mode="json") for s in mutable],
            "total_duration": round(last_end, 3),
        }
    )


def _planner_user(script_text: str, total_duration: int, language_hint: str) -> str:
    return (
        f"Target total duration: {total_duration} seconds.\n"
        f"Language of subtitles & narration: {language_hint}.\n"
        f"Allowed scene_type values: hook | contrast | concept | steps | data | quote | cta.\n"
        f"The first segment must be scene_type=hook and last <=3s.\n"
        f"The final segment must be scene_type=cta.\n"
        f"Subtitles must be punchy (~10-22 chars per line for Chinese, ~6 words for English).\n\n"
        "When you write the `style` field, make it concrete and production-ready, for example "
        "\"premium warm editorial motion design with cream/amber/coral accents, layered cards, soft shadows, "
        "and crisp typography\". Avoid vague labels like simple, cute, or generic.\n\n"
        "TTS: keep each `narration` comfortably within that segment's time window (see system rules). "
        "Do not pack a paragraph into a 2-second hook.\n\n"
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
    plan = ScriptPlan.model_validate(plan.model_dump())
    plan = expand_plan_timeline_for_tts(plan, language_hint=language_hint)
    return plan


def save_plan(plan: ScriptPlan, path: Path | None = None) -> Path:
    target = path or settings.build_dir / "segments.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return target


def load_plan(path: Path | None = None) -> ScriptPlan:
    target = path or settings.build_dir / "segments.json"
    return ScriptPlan.model_validate(json.loads(target.read_text(encoding="utf-8")))
