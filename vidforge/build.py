"""Stage 3: turn each Segment into per-scene HTML/CSS/JS, render base templates.

Equivalent to "Codex 生成动画工程" in the workflow image.

Optimisations vs v1:
  * Scenes are generated **in parallel** via `asyncio.to_thread` -> `asyncio.gather`.
    Wall-clock for a 6-segment plan drops from ~30-60 s to ~6-12 s.
  * Each `ask_json` call is disk-cached (see `llm.py`); a re-run with the same
    plan is essentially free.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console

from .config import PROJECT_ROOT, settings
from .llm import ask_json, report_cache_stats
from .schemas import SceneCode, ScriptPlan, Segment

_DEFAULT_BUILD_CONCURRENCY = 6

console = Console()


def _sanitize_scene_js(js: str) -> str:
    """Fix common LLM mistakes that break the page before dataset.ready is set."""
    # Model sometimes emits `var tl = window.tl` but only closure `tl` exists → tl is undefined, fromTo crashes.
    js = re.sub(
        r"\b(?:var|let|const)\s+tl\s*=\s*window\.tl\s*;?",
        "",
        js,
    )
    js = js.replace("window.tl", "tl")
    js = re.sub(r"\bwindow\.timeline\b", "tl", js)
    return js


SCENE_SYSTEM = (
    "You are a senior front-end engineer who specialises in GSAP-driven motion graphics for short videos. "
    "You write production-quality HTML/CSS/JS that renders deterministically (no random, no setTimeout). "
    "All animation must be expressed via the supplied `tl` (gsap.timeline) so a downstream renderer can capture it. "
    "All assets must be inline SVG / pure CSS - never reference external images, fonts or APIs. "
    "NEVER use gsap repeat: -1 / Infinity or any infinite tween — every tween must finish within the scene window "
    "or the master timeline never completes."
)


_LIBTV_SKILL_MD = (
    PROJECT_ROOT / "third_party" / "libtv-skills" / "skills" / "libtv-skill" / "SKILL.md"
)


def _libtv_skill_scene_note() -> str:
    """Optional context for the scene LLM when the LibTV skill is vendored in-repo."""
    if not settings.libtv_skill_in_scene_prompt:
        return ""
    if not _LIBTV_SKILL_MD.is_file():
        return ""
    return (
        "\n"
        "LibTV workflow (optional): The repo includes `third_party/libtv-skills` — see `skills/libtv-skill/SKILL.md` "
        "and its `scripts/` for LibLib.tv Agent-IM (same `LIBTV_ACCESS_KEY` as HyperFrames). "
        "Use that path in a separate step for reference stills or video; for **this** scene output keep "
        "**inline SVG / pure CSS only** (no external image or video URLs in the fragment).\n"
    )


def _scene_user(seg: Segment, plan: ScriptPlan) -> str:
    return (
        f"Project style: {plan.style}\n"
        f"Stage size: {settings.width}x{settings.height} (9:16)\n"
        f"Scene index: {seg.index}\n"
        f"Scene type: {seg.scene_type}\n"
        f"Time window: {seg.start:.2f}s -> {seg.end:.2f}s (duration {seg.duration:.2f}s)\n"
        f"Subtitle: {seg.subtitle}\n"
        f"Narration: {seg.narration}\n"
        f"Visuals brief: {seg.visuals}\n"
        f"Keywords: {', '.join(seg.keywords) or '(none)'}\n\n"
        "Constraints:\n"
        f"- Wrap all CSS selectors with .scene-{seg.index} so styles do not leak.\n"
        "- The HTML fragment is injected INSIDE a <section class=\"scene\"> so do NOT add <html>/<body>/<section>.\n"
        "- Never write to the bottom 380px (PIP safe zone) or the bottom 60px (subtitle bar).\n"
        "- The JS body runs inside an IIFE with `tl` (a paused gsap.timeline()) and `root` (the scene element) in scope. "
        f"Add tweens that together last <= {seg.duration:.2f}s. Do NOT call tl.play() yourself.\n"
        "- NEVER reassign tl from globals (e.g. window.tl does not exist) — only use the injected `tl`.\n"
        "- Prefer 1-3 large visual elements with staggered entrances. Strong typography wins.\n"
        f"{_libtv_skill_scene_note()}"
    )


def generate_scene(seg: Segment, plan: ScriptPlan) -> SceneCode:
    # 6–8 parallel SceneCode calls can each return long CSS/JS; 2200 tokens
    # truncates JSON on DeepSeek/OpenAI — use a generous ceiling.
    cap = int(os.environ.get("VIDFORGE_SCENE_MAX_TOKENS", "8192"))
    code = ask_json(SCENE_SYSTEM, _scene_user(seg, plan), SceneCode, max_tokens=cap)
    code.index = seg.index
    # LLMs sometimes emit infinite GSAP tweens (repeat: -1) which prevents the
    # master timeline from ever calling onComplete — clamp defensively.
    code.js = re.sub(r"repeat\s*:\s*-1\b", "repeat: 1", code.js)
    code.js = re.sub(r"repeat\s*:\s*Infinity\b", "repeat: 1", code.js, flags=re.I)
    code.js = _sanitize_scene_js(code.js)
    return code


async def _generate_all_scenes(plan: ScriptPlan, max_concurrency: int) -> list[SceneCode]:
    """Run `generate_scene` for every segment concurrently with a soft cap."""
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(seg: Segment) -> SceneCode:
        async with sem:
            console.log(
                f"[cyan]\u25b6\ufe0f scene {seg.index} ({seg.scene_type})[/cyan] {seg.subtitle}"
            )
            code = await asyncio.to_thread(generate_scene, seg, plan)
            console.log(f"[green]\u2713 scene {seg.index} ready[/green]")
            return code

    return await asyncio.gather(*(_one(s) for s in plan.segments))


def build_project(
    plan: ScriptPlan,
    project_dir: Path | None = None,
    *,
    concurrency: int | None = None,
) -> Path:
    project_dir = project_dir or settings.build_dir / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    cap = concurrency or int(os.environ.get("VIDFORGE_BUILD_CONCURRENCY", _DEFAULT_BUILD_CONCURRENCY))
    scenes = asyncio.run(_generate_all_scenes(plan, cap))
    scenes.sort(key=lambda s: s.index)

    env = Environment(
        loader=FileSystemLoader(str(settings.templates_dir)),
        autoescape=select_autoescape(disabled_extensions=("j2",)),
    )
    ctx = {
        "plan": plan,
        "scenes": {s.index: s for s in scenes},
        "width": settings.width,
        "height": settings.height,
        "fps": settings.fps,
        "scene_styles": "\n\n".join(s.css for s in scenes),
    }

    (project_dir / "index.html").write_text(env.get_template("base.html.j2").render(**ctx), encoding="utf-8")
    (project_dir / "styles.css").write_text(env.get_template("styles.css.j2").render(**ctx), encoding="utf-8")
    (project_dir / "main.js").write_text(env.get_template("main.js.j2").render(**ctx), encoding="utf-8")

    console.log(f"[green]\u2713 Project built at {project_dir}[/green]")
    report_cache_stats()
    return project_dir
