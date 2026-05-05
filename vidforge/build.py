"""Stage 3: turn each Segment into per-scene HTML/CSS/JS, render base templates.

Equivalent to "Codex 生成动画工程" in the workflow image.

Optimisations vs v1:
  * Scenes are generated **in parallel** via `asyncio.to_thread` -> `asyncio.gather`.
  * Default concurrency is 4 (`VIDFORGE_BUILD_CONCURRENCY`) to reduce API burst / 429s;
    raise to 6–8 on dedicated high-rate accounts.
  * Each `ask_json` call is disk-cached (see `llm.py`); a re-run with the same
    plan is essentially free.
  * When `run_dir` is set (full `VideoRun` pipeline), `_build_progress.json` is
    updated for the UI to show LLM scene completion counts.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from rich.console import Console

from .config import PROJECT_ROOT, settings
from .llm import ask_json, report_cache_stats
from .schemas import SceneCode, ScriptPlan, Segment

# Default parallel LLM scene calls. Lower than 6 reduces burst 429s on shared API quotas;
# override with VIDFORGE_BUILD_CONCURRENCY.
_DEFAULT_BUILD_CONCURRENCY = 4

_BUILD_PROGRESS_NAME = "_build_progress.json"


def write_build_progress(run_dir: Path | None, payload: dict) -> None:
    """Best-effort atomic write for UI / CLI to poll during long LLM builds."""
    if run_dir is None:
        return
    path = run_dir / _BUILD_PROGRESS_NAME
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def clear_build_progress(run_dir: Path | None) -> None:
    if run_dir is None:
        return
    path = run_dir / _BUILD_PROGRESS_NAME
    try:
        path.unlink()
    except OSError:
        pass

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
    "Target look: premium warm editorial motion design for family-friendly explainers. Use a coherent palette of "
    "cream, amber, coral, cocoa, and charcoal; layered shapes or cards; rounded corners; soft shadows; crisp "
    "alignment; and typography that feels deliberate. The frame should feel composed like a finished poster or "
    "social explainer, not like a sticker collage or a school worksheet. Avoid huge empty backdrops, tiny isolated "
    "icons, Comic Sans, novelty fonts, and default-browser-looking layouts unless the script explicitly calls for them. "
    "Prefer one clear hero focal area plus one supporting accent cluster so the composition has depth and hierarchy "
    "instead of feeling sparse. "
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
        "Visual direction: premium warm editorial motion design, not clip-art and not a worksheet look.\n"
        "Prefer layered shapes, deliberate whitespace, crisp Chinese typography, rounded cards or ribbons, and "
        "subtle shadows so the frame feels finished.\n"
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
        "- The JS body runs inside an IIFE that receives `tl` (a paused gsap.timeline()) and `root` (the scene element) "
        "as arguments and keeps them in scope. "
        f"Add tweens that together last <= {seg.duration:.2f}s. Do NOT call tl.play() yourself.\n"
        "- NEVER reassign tl from globals (e.g. window.tl does not exist) — only use the injected `tl`.\n"
        "- Must include a real inline <svg> visual with multiple graphical primitives (path/circle/rect/line/etc.); "
        "subtitles or headings alone do not count as visuals.\n"
        "- Compose the scene like a finished editorial explainer: one strong hero composition plus one secondary accent "
        "zone, balanced negative space, and a clear visual hierarchy. Do not leave the frame looking accidentally empty.\n"
        "- If you use text, give it a designed container: ribbon, card, label, or chart caption. Avoid raw floating text "
        "when a panel or shape would make it feel more intentional.\n"
        "- Prefer clean, deliberate typography. Avoid Comic Sans, novelty handwriting fonts, and default-looking layouts.\n"
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
    if "comic sans" in html or "comic sans" in css:
        issues.append("avoid Comic Sans; use a more polished font pairing")
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


def _scene_fallback_enabled() -> bool:
    return os.environ.get("VIDFORGE_SCENE_FALLBACK", "1").lower() not in {"0", "false", "no"}


def _fallback_scene(seg: Segment, plan: ScriptPlan, reason: str) -> SceneCode:
    idx = seg.index
    title = escape(seg.subtitle)
    visual = escape(seg.visuals or seg.narration or seg.subtitle)
    label = escape(str(seg.scene_type).upper())
    keyword = escape((seg.keywords or [plan.title])[0] if (seg.keywords or [plan.title]) else "VIDFORGE")
    html = f"""
<div class="vf-scene-shell">
  <div class="vf-kicker">{label}</div>
  <div class="vf-title">{title}</div>
  <svg class="vf-visual" viewBox="0 0 760 560" aria-hidden="true">
    <defs>
      <linearGradient id="vf-grad-{idx}" x1="0" x2="1" y1="0" y2="1">
        <stop offset="0%" stop-color="#fff7e8"/>
        <stop offset="100%" stop-color="#ffd0a8"/>
      </linearGradient>
      <filter id="vf-shadow-{idx}" x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="18" stdDeviation="18" flood-color="#7c3f20" flood-opacity="0.18"/>
      </filter>
    </defs>
    <rect class="vf-mark vf-board" x="70" y="70" width="620" height="390" rx="42" fill="url(#vf-grad-{idx})" filter="url(#vf-shadow-{idx})"/>
    <circle class="vf-mark" cx="180" cy="170" r="58" fill="#ff7f64" opacity="0.9"/>
    <rect class="vf-mark" x="275" y="138" width="290" height="28" rx="14" fill="#5b3426" opacity="0.82"/>
    <rect class="vf-mark" x="275" y="198" width="220" height="24" rx="12" fill="#9a5a3b" opacity="0.5"/>
    <path class="vf-mark" d="M178 272 C248 220 316 356 394 288 C464 226 524 304 596 244" fill="none" stroke="#ff8f5f" stroke-width="18" stroke-linecap="round"/>
    <circle class="vf-mark" cx="600" cy="360" r="66" fill="#ffe2a6" stroke="#ffb15c" stroke-width="10"/>
    <path class="vf-mark" d="M570 358 L595 384 L638 322" fill="none" stroke="#5b3426" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>
  <div class="vf-note">{visual}</div>
  <div class="vf-chip">{keyword}</div>
</div>
"""
    css = f"""
.scene-{idx} {{
  background:
    radial-gradient(circle at 18% 12%, rgba(255,255,255,0.58), transparent 30%),
    linear-gradient(150deg, #fff2d7 0%, #ffd5ad 58%, #f07f61 100%);
}}
.scene-{idx} .vf-scene-shell {{
  position: relative;
  width: 840px;
  min-height: 1180px;
  padding: 92px 74px 82px;
  border-radius: 42px;
  background: rgba(255, 252, 244, 0.76);
  box-shadow: 0 36px 100px rgba(92, 48, 24, 0.22);
  color: #3c241b;
  overflow: hidden;
}}
.scene-{idx} .vf-kicker {{
  display: inline-block;
  padding: 16px 26px;
  border-radius: 999px;
  background: #3c241b;
  color: #fff7e8;
  font-size: 26px;
  font-weight: 800;
  letter-spacing: 0;
}}
.scene-{idx} .vf-title {{
  margin-top: 46px;
  font-size: 70px;
  line-height: 1.12;
  font-weight: 900;
  letter-spacing: 0;
}}
.scene-{idx} .vf-visual {{
  display: block;
  width: 100%;
  margin-top: 56px;
}}
.scene-{idx} .vf-note {{
  margin-top: 42px;
  font-size: 34px;
  line-height: 1.42;
  color: rgba(60, 36, 27, 0.72);
}}
.scene-{idx} .vf-chip {{
  position: absolute;
  right: 58px;
  bottom: 58px;
  padding: 18px 28px;
  border-radius: 24px;
  background: #ff7f64;
  color: white;
  font-size: 30px;
  font-weight: 800;
  box-shadow: 0 18px 42px rgba(240, 127, 97, 0.32);
}}
"""
    js = """
const shell = root.querySelector(".vf-scene-shell");
const visual = root.querySelector(".vf-visual");
const marks = root.querySelectorAll(".vf-mark");
const title = root.querySelector(".vf-title");
const note = root.querySelector(".vf-note");
const chip = root.querySelector(".vf-chip");
tl.fromTo(shell, { opacity: 0, y: 46, scale: 0.96 }, { opacity: 1, y: 0, scale: 1, duration: 0.7, ease: "power3.out" })
  .fromTo(title, { opacity: 0, y: 28 }, { opacity: 1, y: 0, duration: 0.55, ease: "power2.out" }, 0.18)
  .fromTo(visual, { opacity: 0, y: -18 }, { opacity: 1, y: 0, duration: 0.55, ease: "power2.out" }, 0.32)
  .fromTo(marks, { opacity: 0, scale: 0.9, transformOrigin: "center center" }, { opacity: 1, scale: 1, duration: 0.5, stagger: 0.055, ease: "back.out(1.6)" }, 0.48)
  .fromTo(note, { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.45, ease: "power2.out" }, 0.8)
  .fromTo(chip, { opacity: 0, y: 18, scale: 0.9 }, { opacity: 1, y: 0, scale: 1, duration: 0.45, ease: "back.out(1.8)" }, 0.95);
"""
    console.log(f"[yellow]scene {idx} using fallback visual: {reason[:180]}[/yellow]")
    return SceneCode(index=idx, html=html, css=css, js=js)


def generate_scene(seg: Segment, plan: ScriptPlan) -> SceneCode:
    # 6–8 parallel SceneCode calls can each return long CSS/JS; 2200 tokens
    # truncates JSON on DeepSeek/OpenAI — use a generous ceiling.
    cap = int(os.environ.get("VIDFORGE_SCENE_MAX_TOKENS", "8192"))
    user_prompt = _scene_user(seg, plan)
    last_issues: list[str] = []
    for attempt in range(2):
        try:
            code = ask_json(SCENE_SYSTEM, user_prompt, SceneCode, max_tokens=cap)
        except Exception as exc:
            last_issues = [f"{type(exc).__name__}: {exc}"]
            console.log(
                f"[yellow]scene {seg.index} generation failed {attempt + 1}/2: "
                f"{last_issues[0][:180]}[/yellow]"
            )
            continue
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
    if _scene_fallback_enabled():
        return _fallback_scene(seg, plan, "; ".join(last_issues) or "scene generation did not pass QA")
    raise RuntimeError(f"scene {seg.index} failed visual QA: {'; '.join(last_issues)}")



async def _generate_all_scenes(
    plan: ScriptPlan,
    max_concurrency: int,
    run_dir: Path | None,
) -> list[SceneCode]:
    """Run `generate_scene` for every segment concurrently with a soft cap."""
    sem = asyncio.Semaphore(max_concurrency)
    total_segs = len(plan.segments)
    lock = asyncio.Lock()
    completed = 0

    async def _one(seg: Segment) -> SceneCode:
        nonlocal completed
        async with sem:
            async with lock:
                done_snapshot = completed
            write_build_progress(
                run_dir,
                {
                    "phase": "llm_scenes",
                    "completed": done_snapshot,
                    "total": total_segs,
                    "working_on_index": seg.index,
                    "concurrency": max_concurrency,
                },
            )
            console.log(
                f"[cyan]\u25b6\ufe0f scene {seg.index} ({seg.scene_type})[/cyan] {seg.subtitle}"
            )
            code = await asyncio.to_thread(generate_scene, seg, plan)
            console.log(f"[green]\u2713 scene {seg.index} ready[/green]")
            async with lock:
                completed += 1
                write_build_progress(
                    run_dir,
                    {
                        "phase": "llm_scenes",
                        "completed": completed,
                        "total": total_segs,
                        "working_on_index": None,
                        "last_finished_index": seg.index,
                        "concurrency": max_concurrency,
                    },
                )
            return code

    return await asyncio.gather(*(_one(s) for s in plan.segments))


def build_project(
    plan: ScriptPlan,
    project_dir: Path | None = None,
    *,
    concurrency: int | None = None,
    run_dir: Path | None = None,
) -> Path:
    project_dir = project_dir or settings.build_dir / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    cap = concurrency or int(os.environ.get("VIDFORGE_BUILD_CONCURRENCY", _DEFAULT_BUILD_CONCURRENCY))
    cap = max(1, cap)

    ok = False
    try:
        write_build_progress(
            run_dir,
            {
                "phase": "llm_scenes",
                "completed": 0,
                "total": len(plan.segments),
                "concurrency": cap,
            },
        )
        scenes = asyncio.run(_generate_all_scenes(plan, cap, run_dir))
        scenes.sort(key=lambda s: s.index)

        write_build_progress(
            run_dir,
            {
                "phase": "templates",
                "completed": len(plan.segments),
                "total": len(plan.segments),
            },
        )

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
        ok = True
        return project_dir
    finally:
        if ok:
            clear_build_progress(run_dir)
