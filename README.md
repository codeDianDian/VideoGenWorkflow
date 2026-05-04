# vidforge

A 6-stage **script → animated short video** pipeline modelled after the
*Codex × HyperFrames* workflow:

```
script.md  ─►  segments.json  ─►  HTML+GSAP project  ─►  animation.mp4  ─►  final.mp4
   1.ingest      2.plan (LLM)       3.build (LLM)         4.render          5.composite
```

| step | command | what happens |
|------|---------|--------------|
| 1 | `vidforge plan demo.md` | LLM splits the script into 5–8 timed segments and writes `build/segments.json`. |
| 2 | `vidforge build` | LLM generates per-scene HTML / scoped CSS / GSAP timeline JS. Templates wire them into `index.html`, `styles.css`, `main.js`. |
| 3 | `vidforge render` | Renders the HTML project to MP4 using HyperFrames (if `LIBTV_ACCESS_KEY` set) or Playwright + ffmpeg fallback. |
| 4 | `vidforge narrate` | (optional) Synth a voiceover with edge-tts when there is no human口播. |
| 5 | `vidforge composite --head head.mp4` | ffmpeg overlays the talking-head PIP into the bottom-right safe zone, mixes audio, and burns subtitles. |
| ✓ | `vidforge run demo.md` | Runs all of the above end-to-end. |

---

## Setup

Requires Python 3.11+ and `ffmpeg`. For the Playwright renderer you also need
Chromium (one-time install).

```bash
cd vidforge
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium

brew install ffmpeg                 # or your OS equivalent

cp .env.example .env                # set ANTHROPIC_API_KEY (or OPENAI_API_KEY)
```

Optional — for the **HyperFrames** renderer (the route shown in the original infographic):

```bash
npm i -g hyperframes                # provides the `npx hyperframes` CLI
echo "LIBTV_ACCESS_KEY=..." >> .env
echo "VIDFORGE_RENDERER=hyperframes" >> .env
```

---

## Quick start

```bash
# end-to-end with auto narration, no talking head
vidforge run examples/demo_script.md --auto-narration

# end-to-end with talking head PIP
vidforge run examples/demo_script.md --head ~/Movies/talking_head.mp4
```

Outputs land under `data/`:

```
data/
├── animation_full.mp4   # pure animation (no口播)
├── narration.mp3        # auto TTS (if --auto-narration)
├── final.mp4            # composited (with PIP if --head)
├── final.srt
└── final_subbed.mp4     # subtitle-burned, ready to upload
```

Intermediate artifacts under `build/`:

```
build/
├── segments.json        # plan output - safe to hand-edit between steps
└── project/
    ├── index.html
    ├── styles.css
    └── main.js
```

---

## Web 工作台

在浏览器里贴文案、看流水线各阶段进度、预览成片（与 CLI 同一套 `VideoRun.execute()`）：

```bash
vidforge ui
# 或
streamlit run vidforge/ui.py
```

- **工作台**：粘贴 Markdown 脚本、设置目标秒数、可选自动配音与口播 PIP（mp4）。任务在后台线程执行，页面每 2 秒轮询 `runs/<slug>/manifest.json` 更新阶段条与状态。
- **运行记录**：浏览历史 run，查看分镜 JSON、内嵌场景预览、关键帧与清单日志。

---

## Per-stage iteration

Every stage is idempotent and can be re-run without redoing the others — the
classic Codex iteration loop:

```bash
vidforge plan examples/demo_script.md -d 45        # tweak duration
$EDITOR build/segments.json                        # hand-fix wording / timing
vidforge build                                     # re-generate the HTML
vidforge render                                    # re-render
vidforge composite --head head.mp4                 # re-composite
```

---

## Architecture

```
vidforge/
├── cli.py                  # 6 typer subcommands matching the workflow image
├── config.py               # env-driven settings (size, fps, voice, renderer)
├── schemas.py              # ScriptPlan / Segment / SceneCode / RenderJob
├── llm.py                  # Anthropic / OpenAI -> validated Pydantic JSON
├── ingest.py               # .md / .txt / .pdf -> string
├── plan.py                 # script -> segments.json (LLM)
├── build.py                # segments -> per-scene HTML/CSS/JS (LLM + Jinja)
├── render.py               # HyperFrames CLI OR Playwright + ffmpeg
├── tts.py                  # edge-tts segment-aligned narration
├── composite.py            # ffmpeg PIP overlay, subtitle burn, audio mix
└── ui.py                   # Streamlit 工作台 + 运行记录
templates/
├── base.html.j2            # GSAP-loaded stage with PIP safe zone
├── styles.css.j2           # base styles + scoped scene styles
└── main.js.j2              # master timeline driver
```

---

## How the LLM gets reliable HTML

`build.py` does **not** ask the LLM to produce a whole web page from scratch.
Instead:

1. The base templates own the structure (stage size, scene containers, subtitle
   bar, PIP safe zone, master timeline).
2. The LLM is asked, *per segment*, to fill three fields:
   * `html`  – the markup that goes inside that scene's `<section>`,
   * `css`   – styles **scoped** under `.scene-{index}`,
   * `js`    – tweens added to a paused `tl = gsap.timeline()` already in scope.
3. Jinja2 stitches the per-scene fragments into a single deterministic project
   that can be replayed frame-for-frame by either renderer.

The renderers wait for `document.body.dataset.ready === '1'` before invoking
`window.__startVideo()`, then wait for `dataset.done === '1'` — so timing is
exact regardless of machine speed.

---

## Where to extend

- **More scene types** — add to `SceneType` in `schemas.py` and bias the planner
  prompt in `plan.py`.
- **Brand kits** — drop logo / colour vars into `templates/styles.css.j2`.
- **Auto B-roll** — extend `build.py` to also call an image / Lottie generator
  per scene and inline the SVG.
- **Hook A/B testing** — re-run only stage 2 with different planner prompts and
  composite multiple variants for analytics.
- **Publish step** — add a `publish` command that uploads `final_subbed.mp4` to
  Xiaohongshu / 抖音 via their official open APIs (with proper auth).
