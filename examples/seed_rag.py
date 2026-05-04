"""Hand-crafted plan + scenes for a second demo: '30 seconds explaining RAG'.

Distinct visual identity from seed_demo.py: deep-purple stage, neon cyan + hot
pink accents, condensed type. Output: data/rag_animation.mp4.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from vidforge.config import settings
from vidforge.plan import save_plan
from vidforge.render import render_with_playwright
from vidforge.schemas import SceneCode, ScriptPlan, Segment


PLAN = ScriptPlan(
    title="30 \u79d2\u8bb2\u6e05\u695a\uff1a\u4ec0\u4e48\u662f RAG",
    style="deep purple stage, neon cyan + hot pink accents, condensed bold type",
    total_duration=30.0,
    aspect="9:16",
    segments=[
        Segment(
            index=0, start=0.0, end=3.5, scene_type="hook",
            subtitle="\u4e3a\u4ec0\u4e48 AI \u8001\u80e1\u8bf4\uff1f",
            narration="\u4e3a\u4ec0\u4e48\u5927\u6a21\u578b\u603b\u662f\u4e00\u672c\u6b63\u7ecf\u5730\u80e1\u8bf4\u516b\u9053\uff1f",
            visuals="enormous question mark with glitch shadow, headline below",
            keywords=["hook", "hallucination"],
        ),
        Segment(
            index=1, start=3.5, end=10.0, scene_type="contrast",
            subtitle="\u5149\u9760\u8bb0  vs  \u5148\u67e5\u518d\u7b54",
            narration="\u666e\u901a\u5927\u6a21\u578b\u53ea\u80fd\u9760\u8bb0\u4f4f\u7684\u4e1c\u897f\u5bf9\u4ed8\uff0c\u800c RAG \u5148\u67e5\u8d44\u6599\u518d\u56de\u7b54\u3002",
            visuals="left card: brain only, right card: brain + stacked books",
            keywords=["contrast", "memory", "retrieval"],
        ),
        Segment(
            index=2, start=10.0, end=20.0, scene_type="steps",
            subtitle="\u5207\u7247 \u00b7 \u68c0\u7d22 \u00b7 \u751f\u6210",
            narration="\u4e09\u6b65\uff1a\u6587\u6863\u5207\u7247\u5b58\u5165\u5411\u91cf\u5e93\uff0c\u68c0\u7d22\u76f8\u5173\u7247\u6bb5\uff0c\u518d\u8ba9\u6a21\u578b\u751f\u6210\u56de\u7b54\u3002",
            visuals="three vertical step cards numbered 01/02/03 with arrows between",
            keywords=["chunk", "retrieve", "generate"],
        ),
        Segment(
            index=3, start=20.0, end=25.0, scene_type="concept",
            subtitle="\u4f60\u7684\u6587\u6863 \u2192 \u77e5\u8bc6\u5e93",
            narration="\u4f60\u53ea\u9700\u8981\u628a\u516c\u53f8\u6587\u6863\u3001PDF\u3001\u6570\u636e\u5e93\u4e22\u8fdb\u53bb\uff0c\u5c31\u80fd\u53d8\u6210\u4e13\u5c5e\u77e5\u8bc6\u5e93\u3002",
            visuals="document icon split into chunks flying into vector cube",
            keywords=["pipeline", "embedding"],
        ),
        Segment(
            index=4, start=25.0, end=30.0, scene_type="cta",
            subtitle="\u7ed9 AI \u63a5\u4e2a\u77e5\u8bc6\u5e93",
            narration="\u60f3\u8ba9 AI \u4e0d\u80e1\u8bf4\uff1f\u7ed9\u5b83\u63a5\u4e2a\u77e5\u8bc6\u5e93\uff0c\u56de\u7b54\u7acb\u523b\u6709\u636e\u53ef\u67e5\u3002",
            visuals="huge plug-in symbol joining brain and database, pulsing glow",
            keywords=["cta"],
        ),
    ],
)


SCENES: dict[int, SceneCode] = {
    0: SceneCode(
        index=0,
        html=(
            '<div class="rag-hook">'
            '<div class="rag-kicker">EP.02 \u00b7 RAG \u5165\u95e8</div>'
            '<div class="rag-q">?</div>'
            '<h1 class="rag-hook-h">\u4e3a\u4ec0\u4e48 AI<br/><em>\u8001\u80e1\u8bf4\uff1f</em></h1>'
            "</div>"
        ),
        css=(
            ".scene-0 .rag-hook{position:relative;display:flex;flex-direction:column;gap:24px;align-items:flex-start;padding-top:140px;width:100%}"
            ".scene-0 .rag-kicker{font-size:34px;letter-spacing:.35em;color:#9b8fbf;text-transform:uppercase}"
            ".scene-0 .rag-q{position:absolute;right:40px;top:60px;font-size:520px;font-weight:900;line-height:1;color:#ff4ecd;"
            "text-shadow:8px 8px 0 #00d4ff, -4px -4px 0 rgba(255,78,205,0.3);font-family:'Helvetica Neue',sans-serif}"
            ".scene-0 .rag-hook-h{font-size:160px;line-height:1.05;margin:0;font-weight:900;color:#ffffff;letter-spacing:.02em;position:relative;z-index:2}"
            ".scene-0 .rag-hook-h em{color:#00d4ff;font-style:normal}"
        ),
        js=(
            "tl.from(root.querySelector('.rag-kicker'), {y:30, opacity:0, duration:0.5})"
            ".from(root.querySelector('.rag-q'), {scale:0, rotation:-30, opacity:0, duration:0.7, ease:'back.out(2)'}, '-=0.2')"
            ".to(root.querySelector('.rag-q'), {x:'+=8', y:'-=4', duration:0.06, repeat:6, yoyo:true, ease:'none'}, '-=0.3')"
            ".from(root.querySelectorAll('.rag-hook-h, .rag-hook-h em'), {y:80, opacity:0, duration:0.6, stagger:0.1, ease:'expo.out'}, '-=0.6');"
        ),
    ),
    1: SceneCode(
        index=1,
        html=(
            '<div class="rag-vs">'
            '<div class="rag-vs-card rag-vs-left">'
            '<div class="rag-vs-icon">\U0001F9E0</div>'
            '<div class="rag-vs-tag">\u666e\u901a LLM</div>'
            '<div class="rag-vs-text">\u53ea\u9760\u8bb0</div>'
            '<div class="rag-vs-sub">\u8bb0\u4e0d\u4f4f \u2192 \u80e1\u7f16</div>'
            "</div>"
            '<div class="rag-vs-card rag-vs-right">'
            '<div class="rag-vs-icon">\U0001F4DA</div>'
            '<div class="rag-vs-tag">RAG</div>'
            '<div class="rag-vs-text">\u5148\u67e5\u518d\u7b54</div>'
            '<div class="rag-vs-sub">\u6709\u636e\u53ef\u67e5</div>'
            "</div>"
            "</div>"
        ),
        css=(
            ".scene-1 .rag-vs{display:grid;grid-template-columns:1fr 1fr;gap:28px;width:100%;align-items:stretch}"
            ".scene-1 .rag-vs-card{display:flex;flex-direction:column;gap:18px;padding:48px 36px;border-radius:32px;min-height:560px}"
            ".scene-1 .rag-vs-icon{font-size:120px;line-height:1}"
            ".scene-1 .rag-vs-tag{font-size:30px;letter-spacing:.3em;font-weight:700}"
            ".scene-1 .rag-vs-text{font-size:88px;font-weight:900;line-height:1.05}"
            ".scene-1 .rag-vs-sub{font-size:34px;color:#c9bfe0;margin-top:auto}"
            ".scene-1 .rag-vs-left{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08)}"
            ".scene-1 .rag-vs-left .rag-vs-tag{color:#9b8fbf}"
            ".scene-1 .rag-vs-left .rag-vs-text{color:#9b8fbf}"
            ".scene-1 .rag-vs-right{background:linear-gradient(160deg,rgba(0,212,255,0.18),rgba(255,78,205,0.18));"
            "border:1px solid rgba(0,212,255,0.5);box-shadow:0 0 60px rgba(0,212,255,0.25)}"
            ".scene-1 .rag-vs-right .rag-vs-tag{color:#00d4ff}"
            ".scene-1 .rag-vs-right .rag-vs-text{color:#ffffff}"
        ),
        js=(
            "tl.from(root.querySelector('.rag-vs-left'), {x:-160, opacity:0, duration:0.6, ease:'power3.out'})"
            ".from(root.querySelector('.rag-vs-right'), {x:160, opacity:0, duration:0.6, ease:'power3.out'}, '-=0.5')"
            ".from(root.querySelectorAll('.rag-vs-icon'), {scale:0, rotation:-20, duration:0.5, stagger:0.15, ease:'back.out(2)'}, '-=0.3')"
            ".from(root.querySelectorAll('.rag-vs-text'), {y:30, opacity:0, duration:0.5, stagger:0.15}, '-=0.3')"
            ".to(root.querySelector('.rag-vs-right'), {boxShadow:'0 0 100px rgba(0,212,255,0.45)', duration:1.2, yoyo:true, repeat:1, ease:'sine.inOut'}, '+=0.2');"
        ),
    ),
    2: SceneCode(
        index=2,
        html=(
            '<div class="rag-steps">'
            '<h2 class="rag-steps-h">RAG \u4e09\u6b65\u8d70</h2>'
            '<div class="rag-step rag-step-1"><div class="rag-step-num">01</div>'
            '<div class="rag-step-h2">\u5207\u7247\u5165\u5e93</div>'
            '<div class="rag-step-d">\u6587\u6863 \u2192 \u5206\u5757 \u2192 \u8f6c\u5411\u91cf</div></div>'
            '<div class="rag-arrow">\u2193</div>'
            '<div class="rag-step rag-step-2"><div class="rag-step-num">02</div>'
            '<div class="rag-step-h2">\u68c0\u7d22\u76f8\u5173</div>'
            '<div class="rag-step-d">\u6309\u8bed\u4e49\u627e\u51faTop-K\u7247\u6bb5</div></div>'
            '<div class="rag-arrow">\u2193</div>'
            '<div class="rag-step rag-step-3"><div class="rag-step-num">03</div>'
            '<div class="rag-step-h2">\u751f\u6210\u56de\u7b54</div>'
            '<div class="rag-step-d">\u7247\u6bb5+\u95ee\u9898 \u2192 \u6a21\u578b\u51fa\u7b54</div></div>'
            "</div>"
        ),
        css=(
            ".scene-2 .rag-steps{display:flex;flex-direction:column;gap:18px;width:100%}"
            ".scene-2 .rag-steps-h{font-size:84px;font-weight:900;margin:0 0 12px 0;color:#ffffff}"
            ".scene-2 .rag-step{display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto auto;column-gap:32px;"
            "padding:30px 36px;border-radius:28px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08)}"
            ".scene-2 .rag-step-num{grid-row:1/3;font-size:96px;font-weight:900;line-height:1;color:#00d4ff;align-self:center}"
            ".scene-2 .rag-step-h2{font-size:56px;font-weight:800;color:#ffffff}"
            ".scene-2 .rag-step-d{font-size:30px;color:#c9bfe0;margin-top:6px}"
            ".scene-2 .rag-step-2 .rag-step-num{color:#ff4ecd}"
            ".scene-2 .rag-step-3 .rag-step-num{color:#ffd166}"
            ".scene-2 .rag-arrow{font-size:48px;color:#9b8fbf;text-align:center;margin:-4px 0}"
        ),
        js=(
            "tl.from(root.querySelector('.rag-steps-h'), {y:60, opacity:0, duration:0.5, ease:'power2.out'})"
            ".from(root.querySelectorAll('.rag-step'), {x:140, opacity:0, duration:0.55, stagger:0.6, ease:'power3.out'}, '-=0.2')"
            ".from(root.querySelectorAll('.rag-step-num'), {scale:0.3, opacity:0, duration:0.4, stagger:0.6, ease:'back.out(2.5)'}, 0.5)"
            ".from(root.querySelectorAll('.rag-arrow'), {y:-30, opacity:0, duration:0.4, stagger:0.6, ease:'power2.out'}, 0.6);"
        ),
    ),
    3: SceneCode(
        index=3,
        html=(
            '<div class="rag-flow">'
            '<div class="rag-flow-label">\u4f60\u7684\u6587\u6863</div>'
            '<div class="rag-flow-row">'
            '<div class="rag-doc">\U0001F4C4<div class="rag-doc-l">PDF</div></div>'
            '<div class="rag-flow-arrow">\u2192</div>'
            '<div class="rag-chunks">'
            '<div class="rag-chunk"></div><div class="rag-chunk"></div><div class="rag-chunk"></div><div class="rag-chunk"></div>'
            "</div>"
            '<div class="rag-flow-arrow">\u2192</div>'
            '<div class="rag-cube"><div class="rag-cube-l">VECTOR<br/>DB</div></div>'
            "</div>"
            '<div class="rag-flow-foot">\u4e00\u952e\u53d8\u6210\u4e13\u5c5e\u77e5\u8bc6\u5e93</div>'
            "</div>"
        ),
        css=(
            ".scene-3 .rag-flow{display:flex;flex-direction:column;gap:60px;width:100%;align-items:center;padding-top:80px}"
            ".scene-3 .rag-flow-label{font-size:36px;letter-spacing:.3em;color:#ff4ecd;font-weight:700}"
            ".scene-3 .rag-flow-row{display:flex;align-items:center;gap:18px;justify-content:center;width:100%}"
            ".scene-3 .rag-doc{display:flex;flex-direction:column;align-items:center;gap:8px;font-size:120px;line-height:1}"
            ".scene-3 .rag-doc-l{font-size:24px;color:#9b8fbf;letter-spacing:.2em;font-weight:700}"
            ".scene-3 .rag-flow-arrow{font-size:64px;color:#00d4ff}"
            ".scene-3 .rag-chunks{display:grid;grid-template-columns:repeat(2, 56px);grid-template-rows:repeat(2, 56px);gap:8px}"
            ".scene-3 .rag-chunk{width:56px;height:56px;border-radius:10px;background:linear-gradient(135deg,#00d4ff,#ff4ecd);box-shadow:0 0 24px rgba(0,212,255,0.5)}"
            ".scene-3 .rag-cube{width:160px;height:160px;border-radius:24px;background:linear-gradient(160deg,rgba(0,212,255,0.25),rgba(255,78,205,0.25));"
            "border:2px solid rgba(0,212,255,0.7);display:flex;align-items:center;justify-content:center;box-shadow:0 0 40px rgba(0,212,255,0.4)}"
            ".scene-3 .rag-cube-l{font-size:22px;font-weight:900;color:#ffffff;letter-spacing:.15em;text-align:center;line-height:1.2}"
            ".scene-3 .rag-flow-foot{font-size:46px;font-weight:800;color:#ffffff}"
        ),
        js=(
            "tl.from(root.querySelector('.rag-flow-label'), {y:20, opacity:0, duration:0.4})"
            ".from(root.querySelector('.rag-doc'), {scale:0.4, opacity:0, duration:0.5, ease:'back.out(2)'})"
            ".from(root.querySelectorAll('.rag-flow-arrow')[0], {x:-30, opacity:0, duration:0.3}, '-=0.2')"
            ".from(root.querySelectorAll('.rag-chunk'), {scale:0, opacity:0, duration:0.4, stagger:0.08, ease:'back.out(2.5)'}, '-=0.2')"
            ".from(root.querySelectorAll('.rag-flow-arrow')[1], {x:-30, opacity:0, duration:0.3})"
            ".from(root.querySelector('.rag-cube'), {scale:0, rotation:-30, opacity:0, duration:0.5, ease:'back.out(2)'}, '-=0.1')"
            ".to(root.querySelector('.rag-cube'), {boxShadow:'0 0 80px rgba(0,212,255,0.8)', duration:0.8, yoyo:true, repeat:1, ease:'sine.inOut'}, '+=0.1')"
            ".from(root.querySelector('.rag-flow-foot'), {y:30, opacity:0, duration:0.5}, '-=0.6');"
        ),
    ),
    4: SceneCode(
        index=4,
        html=(
            '<div class="rag-cta">'
            '<div class="rag-cta-eye">\u73b0\u5728\u5c31\u52a8\u624b</div>'
            '<h2 class="rag-cta-h">\u7ed9 AI<br/>\u63a5\u4e2a<em>\u77e5\u8bc6\u5e93</em></h2>'
            '<div class="rag-cta-plug">'
            '<span class="rag-plug-l">\U0001F9E0</span>'
            '<span class="rag-plug-link"></span>'
            '<span class="rag-plug-r">\U0001F5C4\uFE0F</span>'
            "</div>"
            '<div class="rag-cta-tip">\u56de\u7b54\u7acb\u523b\u6709\u636e\u53ef\u67e5</div>'
            "</div>"
        ),
        css=(
            ".scene-4 .rag-cta{display:flex;flex-direction:column;gap:32px;align-items:flex-start;width:100%;padding-top:120px}"
            ".scene-4 .rag-cta-eye{font-size:36px;color:#ff4ecd;letter-spacing:.3em;font-weight:700}"
            ".scene-4 .rag-cta-h{font-size:170px;line-height:1.05;margin:0;font-weight:900;color:#ffffff}"
            ".scene-4 .rag-cta-h em{font-style:normal;color:#00d4ff}"
            ".scene-4 .rag-cta-plug{display:flex;align-items:center;gap:16px;font-size:96px;align-self:center;margin-top:16px}"
            ".scene-4 .rag-plug-link{display:inline-block;width:120px;height:14px;border-radius:7px;background:linear-gradient(90deg,#00d4ff,#ff4ecd);box-shadow:0 0 24px rgba(0,212,255,0.7)}"
            ".scene-4 .rag-cta-tip{font-size:38px;color:#c9bfe0;align-self:center;margin-top:8px}"
        ),
        js=(
            "tl.from(root.querySelector('.rag-cta-eye'), {y:20, opacity:0, duration:0.4})"
            ".from(root.querySelector('.rag-cta-h'), {y:60, opacity:0, duration:0.6, ease:'power3.out'}, '-=0.1')"
            ".from(root.querySelectorAll('.rag-cta-plug span'), {scale:0, opacity:0, duration:0.4, stagger:0.15, ease:'back.out(2)'})"
            ".to(root.querySelector('.rag-plug-link'), {scaleX:1.3, duration:0.4, yoyo:true, repeat:3, ease:'sine.inOut', transformOrigin:'left'})"
            ".from(root.querySelector('.rag-cta-tip'), {y:20, opacity:0, duration:0.4}, '-=0.6');"
        ),
    ),
}


THEME_CSS = """
#stage { background: radial-gradient(ellipse at top right, #2d1547 0%, #1a0d2e 45%, #0f0820 100%); }
#subtitle { color: #ffffff; }
#pip-safe-zone { border-color: rgba(0,212,255,0.18); }
"""


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
        "scene_styles": THEME_CSS + "\n\n" + "\n\n".join(s.css for s in SCENES.values()),
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
    output = settings.data_dir / "rag_animation.mp4"
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
