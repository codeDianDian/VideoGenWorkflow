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
    "Every scene must include at least one substantial inline SVG illustration or diagram; text-only scenes are invalid. "
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
        "- Must include a real inline <svg> visual with multiple graphical primitives (path/circle/rect/line/etc.); "
        "subtitles or headings alone do not count as visuals.\n"
        "- Do not create any element with id=\"subtitle\"; the base template owns the global subtitle layer. "
        "Use class names such as `.scene-caption` for scene-local text.\n"
        "- If CSS sets any visual/text element to opacity: 0, the JS must animate that same element to opacity: 1 "
        "with tl.to(...) or tl.fromTo(...). Do NOT use tl.from(... { opacity: 0 }) against CSS that already has opacity: 0.\n"
        "- Avoid single-line `//` comments inside dense JS chains; use block comments or separate lines so animation code is never commented out.\n"
        "- Prefer 1-3 large visual elements with staggered entrances. Strong typography wins.\n"
        f"{_libtv_skill_scene_note()}"
    )


def _scene_visual_issues(code: SceneCode) -> list[str]:
    html = code.html.lower()
    css = code.css.lower()
    js = code.js.lower()
    issues: list[str] = []
    if "<svg" not in html:
        issues.append("html has no inline <svg> visual")
    if re.search(r"\bid\s*=\s*['\"]subtitle['\"]", code.html, flags=re.I):
        issues.append("html uses reserved id='subtitle'; use a class name instead")
    primitive_count = len(
        re.findall(r"<(path|circle|rect|polygon|polyline|line|ellipse|g)\b", html)
    )
    if primitive_count < 3:
        issues.append("inline visual has too few graphical primitives")
    if re.search(r"opacity\s*:\s*0\b", css) and not re.search(r"opacity\s*:\s*1\b", js):
        issues.append("css hides elements with opacity:0 but js never animates opacity to 1")
    for line in code.js.splitlines():
        comment_tail = line.split("//", 1)[1] if "//" in line else ""
        if re.search(r"\b(?:tl|gsap)\.", comment_tail):
            issues.append("single-line // comment appears to comment out animation code")
            break
    return issues


def generate_scene(seg: Segment, plan: ScriptPlan) -> SceneCode:
    # 6–8 parallel SceneCode calls can each return long CSS/JS; 2200 tokens
    # truncates JSON on DeepSeek/OpenAI — use a generous ceiling.
    cap = int(os.environ.get("VIDFORGE_SCENE_MAX_TOKENS", "8192"))
    user_prompt = _scene_user(seg, plan)
    last_issues: list[str] = []
    for attempt in range(2):
        code = ask_json(SCENE_SYSTEM, user_prompt, SceneCode, max_tokens=cap)
        code.index = seg.index
        # LLMs sometimes emit infinite GSAP tweens (repeat: -1) which prevents the
        # master timeline from ever calling onComplete — clamp defensively.
        code.js = re.sub(r"repeat\s*:\s*-1\b", "repeat: 1", code.js)
        code.js = re.sub(r"repeat\s*:\s*Infinity\b", "repeat: 1", code.js, flags=re.I)
        code.js = _sanitize_scene_js(code.js)
        last_issues = _scene_visual_issues(code)
        if not last_issues:
            return code
        console.log(
            f"[yellow]scene {seg.index} visual QA retry {attempt + 1}/2: "
            f"{'; '.join(last_issues)}[/yellow]"
        )
        user_prompt = (
            f"{user_prompt}\n\n"
            "VISUAL QA FAILED for your previous scene output:\n"
            f"- {'; '.join(last_issues)}\n"
            "Regenerate the full SceneCode JSON. Add a substantial inline SVG illustration/diagram "
            "with multiple shapes and animate its SVG elements via `tl` and `root.querySelector(...)`."
        )
    raise RuntimeError(f"scene {seg.index} failed visual QA: {'; '.join(last_issues)}")



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
