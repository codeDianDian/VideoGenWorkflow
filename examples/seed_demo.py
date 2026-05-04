"""Hand-crafted plan + scenes so we can demo the renderer without an LLM key.

Creates the same artifacts that `vidforge plan` + `vidforge build` would create:
  build/segments.json
  build/project/index.html
  build/project/styles.css
  build/project/main.js

Then `vidforge render` (or `python examples/seed_demo.py --render`) turns it
into data/animation_full.mp4.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from vidforge.config import settings
from vidforge.plan import save_plan
from vidforge.render import render_with_playwright
from vidforge.schemas import SceneCode, ScriptPlan, Segment


PLAN = ScriptPlan(
    title="30 \u79d2\u8bb2\u6e05\u695a\uff1aComputer Use",
    style="minimal flat, deep navy bg, electric blue + warm yellow accents, large 700-weight Chinese type",
    total_duration=30.0,
    aspect="9:16",
    segments=[
        Segment(
            index=0, start=0.0, end=3.0, scene_type="hook",
            subtitle="AI \u4e0d\u53ea\u4f1a\u804a\u5929",
            narration="\u5f88\u591a\u4eba\u4ee5\u4e3a AI \u53ea\u80fd\u5728\u804a\u5929\u6846\u91cc\u7ed9\u4f60\u5efa\u8bae\u3002",
            visuals="huge headline drops in, electric blue underline sweeps across",
            keywords=["hook", "headline"],
        ),
        Segment(
            index=1, start=3.0, end=9.0, scene_type="contrast",
            subtitle="\u5b83\u80fd\u771f\u7684\u5e2e\u4f60\u64cd\u4f5c\u7535\u8111",
            narration="Computer Use \u8ba9\u6a21\u578b\u50cf\u4eba\u4e00\u6837\u53bb\u64cd\u4f5c\u7535\u8111\u3002",
            visuals="left card 'CHAT' fades, right card 'ACTION' scales up with a cursor icon",
            keywords=["contrast"],
        ),
        Segment(
            index=2, start=9.0, end=18.0, scene_type="steps",
            subtitle="\u770b \u5c4f\u5e55  \u52a8 \u9f20\u6807  \u9a8c \u8bc1",
            narration="\u5b83\u505a\u4e09\u4ef6\u4e8b\uff1a\u770b\u5c4f\u5e55\u3001\u52a8\u9f20\u6807\u952e\u76d8\u3001\u95ed\u73af\u9a8c\u8bc1\u3002",
            visuals="three step cards (1/2/3) slide in from below in sequence",
            keywords=["steps"],
        ),
        Segment(
            index=3, start=18.0, end=24.0, scene_type="concept",
            subtitle="\u4f8b\u5b50\uff1a\u81ea\u52a8\u586b OKR \u6a21\u677f",
            narration="\u6bd4\u5982\uff0c\u8ba9\u5b83\u5728 Notion \u91cc\u65b0\u5efa\u4e00\u4e2a\u672c\u5468 OKR \u6a21\u677f\u3002",
            visuals="browser frame with Notion-like UI; cursor clicks fields and types",
            keywords=["example", "notion"],
        ),
        Segment(
            index=4, start=24.0, end=30.0, scene_type="cta",
            subtitle="\u628a\u91cd\u590d\u52a8\u4f5c\u4ea4\u7ed9\u4ed6",
            narration="\u4e0b\u4e00\u6b65\uff0c\u628a\u4f60\u91cd\u590d\u7684\u64cd\u4f5c\u4ea4\u7ed9 Computer Use \u8bd5\u4e00\u6b21\u3002",
            visuals="big call-to-action button pulses; arrow points down",
            keywords=["cta"],
        ),
    ],
)


SCENES: dict[int, SceneCode] = {
    0: SceneCode(
        index=0,
        html=(
            '<div class="hook-wrap">'
            '<div class="kicker">EP.01 \u00b7 AI \u667a\u80fd\u4f53</div>'
            '<h1 class="hook-title"><span>AI</span> \u4e0d\u53ea\u4f1a<br/><em>\u804a\u5929</em></h1>'
            '<div class="hook-bar"></div>'
            "</div>"
        ),
        css=(
            ".scene-0 .hook-wrap{display:flex;flex-direction:column;gap:36px;align-items:flex-start;padding-top:160px}"
            ".scene-0 .kicker{font-size:36px;letter-spacing:.3em;color:#9aa3b2;text-transform:uppercase}"
            ".scene-0 .hook-title{font-size:200px;line-height:1.05;margin:0;font-weight:800;letter-spacing:.02em}"
            ".scene-0 .hook-title span{color:#4f8cff}"
            ".scene-0 .hook-title em{color:#ffd166;font-style:normal}"
            ".scene-0 .hook-bar{width:0;height:14px;background:#4f8cff;border-radius:7px}"
        ),
        js=(
            "tl.from(root.querySelector('.kicker'), {y:30, opacity:0, duration:0.5, ease:'power2.out'})"
            ".from(root.querySelectorAll('.hook-title span, .hook-title em, .hook-title br'), "
            "{y:120, opacity:0, duration:0.7, stagger:0.15, ease:'expo.out'}, '<+0.1')"
            ".to(root.querySelector('.hook-bar'), {width:520, duration:0.6, ease:'power3.out'}, '-=0.2');"
        ),
    ),
    1: SceneCode(
        index=1,
        html=(
            '<div class="vs-wrap">'
            '<div class="vs-card vs-left"><div class="vs-tag">\u4ee5\u524d</div><div class="vs-text">\u53ea\u80fd\u804a\u5929</div></div>'
            '<div class="vs-mid">VS</div>'
            '<div class="vs-card vs-right"><div class="vs-tag">\u73b0\u5728</div><div class="vs-text">\u4f1a\u52a8\u624b</div><div class="vs-cursor">\u25b6</div></div>'
            "</div>"
        ),
        css=(
            ".scene-1 .vs-wrap{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:32px;width:100%}"
            ".scene-1 .vs-card{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);"
            "border-radius:32px;padding:48px 40px;min-height:520px;display:flex;flex-direction:column;"
            "justify-content:center;gap:24px;position:relative;backdrop-filter:blur(10px)}"
            ".scene-1 .vs-tag{font-size:32px;color:#9aa3b2;letter-spacing:.2em}"
            ".scene-1 .vs-text{font-size:96px;font-weight:800;line-height:1.1}"
            ".scene-1 .vs-left .vs-text{color:#9aa3b2}"
            ".scene-1 .vs-right{border-color:rgba(79,140,255,0.4);box-shadow:0 0 40px rgba(79,140,255,0.25)}"
            ".scene-1 .vs-right .vs-text{color:#4f8cff}"
            ".scene-1 .vs-cursor{position:absolute;bottom:40px;right:48px;font-size:96px;color:#ffd166}"
            ".scene-1 .vs-mid{font-size:64px;font-weight:900;color:#ffd166;letter-spacing:.05em}"
        ),
        js=(
            "tl.from(root.querySelector('.vs-left'), {x:-120, opacity:0, duration:0.6, ease:'power3.out'})"
            ".from(root.querySelector('.vs-mid'), {scale:0.4, opacity:0, duration:0.5, ease:'back.out(2)'}, '-=0.2')"
            ".from(root.querySelector('.vs-right'), {x:120, opacity:0, duration:0.6, ease:'power3.out'}, '-=0.4')"
            ".from(root.querySelector('.vs-cursor'), {scale:0, rotation:-30, duration:0.5, ease:'back.out(3)'}, '+=0.1')"
            ".to(root.querySelector('.vs-cursor'), {x:-30, y:-30, duration:0.4, yoyo:true, repeat:3, ease:'power1.inOut'});"
        ),
    ),
    2: SceneCode(
        index=2,
        html=(
            '<div class="steps-wrap">'
            '<h2 class="steps-title">\u5b83\u505a\u4e09\u4ef6\u4e8b</h2>'
            '<div class="step-card"><div class="step-num">1</div><div class="step-body">'
            '<div class="step-h">\u770b\u5c4f\u5e55</div><div class="step-d">\u622a\u56fe \u2192 \u8bc6\u522b UI \u5143\u7d20</div></div></div>'
            '<div class="step-card"><div class="step-num">2</div><div class="step-body">'
            '<div class="step-h">\u52a8\u9f20\u6807\u952e\u76d8</div><div class="step-d">\u70b9\u51fb \u00b7 \u8f93\u5165 \u00b7 \u6eda\u52a8</div></div></div>'
            '<div class="step-card"><div class="step-num">3</div><div class="step-body">'
            '<div class="step-h">\u95ed\u73af\u9a8c\u8bc1</div><div class="step-d">\u518d\u622a\u56fe \u2192 \u786e\u8ba4\u7ed3\u679c</div></div></div>'
            "</div>"
        ),
        css=(
            ".scene-2 .steps-wrap{display:flex;flex-direction:column;gap:32px;width:100%;align-items:stretch}"
            ".scene-2 .steps-title{font-size:84px;font-weight:800;margin:0 0 24px 0;color:#fff}"
            ".scene-2 .step-card{display:flex;align-items:center;gap:36px;background:rgba(255,255,255,0.05);"
            "border:1px solid rgba(255,255,255,0.08);border-radius:28px;padding:36px 40px}"
            ".scene-2 .step-num{font-size:120px;font-weight:900;color:#4f8cff;line-height:1;width:120px;text-align:center}"
            ".scene-2 .step-h{font-size:68px;font-weight:800;color:#fff}"
            ".scene-2 .step-d{font-size:38px;color:#9aa3b2;margin-top:8px}"
        ),
        js=(
            "tl.from(root.querySelector('.steps-title'), {y:60, opacity:0, duration:0.5, ease:'power2.out'})"
            ".from(root.querySelectorAll('.step-card'), "
            "{x:160, opacity:0, duration:0.6, stagger:0.5, ease:'power3.out'}, '-=0.2')"
            ".from(root.querySelectorAll('.step-num'), "
            "{scale:0, opacity:0, duration:0.4, stagger:0.5, ease:'back.out(2.5)'}, 0.5);"
        ),
    ),
    3: SceneCode(
        index=3,
        html=(
            '<div class="ex-wrap">'
            '<div class="ex-label">\u4f8b\u5b50</div>'
            '<div class="ex-window">'
            '<div class="ex-bar"><span></span><span></span><span></span></div>'
            '<div class="ex-page">'
            '<div class="ex-h">\u672c\u5468 OKR</div>'
            '<div class="ex-row"><span class="ex-key">\u76ee\u6807</span><span class="ex-val">\u53d1\u5e03 4 \u6761\u7206\u6b3e\u89c6\u9891</span></div>'
            '<div class="ex-row"><span class="ex-key">KR1</span><span class="ex-val">\u51c6\u5907 8 \u4e2a\u811a\u672c</span></div>'
            '<div class="ex-row"><span class="ex-key">KR2</span><span class="ex-val">\u8bd5\u62cd 4 \u7248\u5f00\u573a</span></div>'
            '<div class="ex-cursor">\u25b8</div>'
            "</div></div></div>"
        ),
        css=(
            ".scene-3 .ex-wrap{display:flex;flex-direction:column;gap:32px;width:100%;align-items:flex-start}"
            ".scene-3 .ex-label{font-size:36px;letter-spacing:.3em;color:#ffd166}"
            ".scene-3 .ex-window{width:100%;background:#161c26;border-radius:32px;border:1px solid rgba(255,255,255,0.08);overflow:hidden}"
            ".scene-3 .ex-bar{display:flex;gap:12px;padding:20px 24px;background:#0c1118}"
            ".scene-3 .ex-bar span{width:18px;height:18px;border-radius:50%;background:#3a4150}"
            ".scene-3 .ex-bar span:nth-child(1){background:#ff5f57}"
            ".scene-3 .ex-bar span:nth-child(2){background:#febb2e}"
            ".scene-3 .ex-bar span:nth-child(3){background:#28c840}"
            ".scene-3 .ex-page{padding:48px 44px;display:flex;flex-direction:column;gap:24px;position:relative;min-height:560px}"
            ".scene-3 .ex-h{font-size:84px;font-weight:800;margin-bottom:16px}"
            ".scene-3 .ex-row{display:flex;gap:24px;align-items:baseline;border-bottom:1px solid rgba(255,255,255,0.06);padding-bottom:18px}"
            ".scene-3 .ex-key{font-size:36px;color:#9aa3b2;width:160px;font-weight:700}"
            ".scene-3 .ex-val{font-size:48px;font-weight:600;color:#fff}"
            ".scene-3 .ex-cursor{position:absolute;font-size:64px;color:#ffd166;left:240px;top:220px;filter:drop-shadow(0 0 12px rgba(255,209,102,0.5))}"
        ),
        js=(
            "tl.from(root.querySelector('.ex-label'), {y:20, opacity:0, duration:0.4, ease:'power2.out'})"
            ".from(root.querySelector('.ex-window'), {y:80, opacity:0, scale:0.95, duration:0.6, ease:'power3.out'}, '-=0.1')"
            ".from(root.querySelector('.ex-h'), {y:30, opacity:0, duration:0.4}, '-=0.2')"
            ".from(root.querySelectorAll('.ex-row'), {x:-60, opacity:0, duration:0.4, stagger:0.5, ease:'power2.out'})"
            ".fromTo(root.querySelector('.ex-cursor'), {x:0, y:0}, "
            "{x:300, y:160, duration:1.2, ease:'power1.inOut'}, '-=0.6');"
        ),
    ),
    4: SceneCode(
        index=4,
        html=(
            '<div class="cta-wrap">'
            '<div class="cta-eyebrow">\u4eca\u5929\u5c31\u8bd5\u8bd5</div>'
            '<h2 class="cta-line">\u628a\u91cd\u590d\u52a8\u4f5c<br/>\u4ea4\u7ed9 <em>Computer Use</em></h2>'
            '<div class="cta-button">\u5199\u4e00\u6bb5 prompt \u8bd5\u8dd1 \u2192</div>'
            '<div class="cta-arrow">\u2193</div>'
            "</div>"
        ),
        css=(
            ".scene-4 .cta-wrap{display:flex;flex-direction:column;gap:40px;align-items:flex-start;width:100%;padding-top:120px}"
            ".scene-4 .cta-eyebrow{font-size:40px;color:#ffd166;letter-spacing:.2em;font-weight:700}"
            ".scene-4 .cta-line{font-size:160px;line-height:1.05;margin:0;font-weight:800}"
            ".scene-4 .cta-line em{font-style:normal;color:#4f8cff}"
            ".scene-4 .cta-button{font-size:56px;font-weight:800;background:#4f8cff;color:#fff;padding:32px 56px;"
            "border-radius:24px;box-shadow:0 16px 60px rgba(79,140,255,0.5)}"
            ".scene-4 .cta-arrow{font-size:120px;color:#ffd166;align-self:center;margin-top:8px}"
        ),
        js=(
            "tl.from(root.querySelector('.cta-eyebrow'), {y:20, opacity:0, duration:0.4})"
            ".from(root.querySelector('.cta-line'), {y:60, opacity:0, duration:0.6, ease:'power3.out'}, '-=0.1')"
            ".from(root.querySelector('.cta-button'), {scale:0.6, opacity:0, duration:0.5, ease:'back.out(2)'})"
            ".to(root.querySelector('.cta-button'), {scale:1.06, duration:0.5, yoyo:true, repeat:3, ease:'power1.inOut'})"
            ".from(root.querySelector('.cta-arrow'), {y:-40, opacity:0, duration:0.4, ease:'power2.out'}, '-=1.5')"
            ".to(root.querySelector('.cta-arrow'), {y:30, duration:0.6, yoyo:true, repeat:2, ease:'power1.inOut'}, '-=0.3');"
        ),
    ),
}


def _stitch_project() -> Path:
    project_dir = settings.build_dir / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(settings.templates_dir)),
        autoescape=select_autoescape(disabled_extensions=("j2",)),
    )
    ctx = {
        "plan": PLAN,
        "scenes": SCENES,
        "width": settings.width,
        "height": settings.height,
        "fps": settings.fps,
        "scene_styles": "\n\n".join(s.css for s in SCENES.values()),
    }
    (project_dir / "index.html").write_text(env.get_template("base.html.j2").render(**ctx), encoding="utf-8")
    (project_dir / "styles.css").write_text(env.get_template("styles.css.j2").render(**ctx), encoding="utf-8")
    (project_dir / "main.js").write_text(env.get_template("main.js.j2").render(**ctx), encoding="utf-8")
    return project_dir


def main(do_render: bool) -> None:
    save_plan(PLAN)
    project_dir = _stitch_project()
    print(f"\u2713 segments.json + project at {project_dir}")
    if not do_render:
        return
    output = settings.data_dir / "animation_full.mp4"
    job = render_with_playwright(project_dir, output, PLAN)
    if job.success:
        print(f"\u2713 rendered: {output}")
    else:
        print("render FAILED:", job.log[-2000:])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    main(args.render)
