# vidforge optimization playbook

What's already in place (after this commit):

- **Daemonized watcher** — `inbox/` → `runs/<ts>__<slug>/` end-to-end, no chat needed
- **Visibility** — Streamlit dashboard renders segments.json, scene preview iframe, project source, key frames, audio, manifest
- **Per-run isolation** — every run is reproducible from its folder
- **Idempotent stages** — each stage's output lives in its own file, easy to selectively re-run
- **Auto-start** — `make install-launchd` registers a per-user macOS service

This doc lists the next-tier optimizations, ordered by **value / effort**. Pick
top-down.

---

## 1. Cost & latency (biggest wins for LLM cost)

### 1.1 Cache LLM responses on disk by `hash(prompt + model)`

The `plan` and `build` stages send the same prompts whenever inputs are unchanged.
Currently nothing memoizes them.

```python
# vidforge/llm.py — add a thin disk cache
import hashlib, json
from pathlib import Path
CACHE_DIR = settings.build_dir / "llm_cache"

def _key(system, user, model_cls, model):
    h = hashlib.sha256(f"{model}|{system}|{user}|{model_cls.__name__}".encode()).hexdigest()
    return CACHE_DIR / f"{h}.json"

def ask_json(system, user, model_cls, max_tokens=4000):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _key(system, user, model_cls, settings.llm_model)
    if key.exists():
        return model_cls.model_validate(json.loads(key.read_text()))
    raw = ...                       # existing call
    obj = model_cls.model_validate(json.loads(raw))
    key.write_text(obj.model_dump_json())
    return obj
```

**Impact:** typical iteration ~30 LLM calls × 3 retries → 90 calls/run. With cache,
re-rendering with only a font change is ~0 LLM calls. Saves $0.05–$0.30/run on
gpt-4o, more on Claude Sonnet.

### 1.2 Parallel scene generation

`build_project()` already runs one LLM call per scene **in parallel** (`asyncio.gather` +
`asyncio.to_thread`), with a semaphore default **concurrent cap of 4** (`VIDFORGE_BUILD_CONCURRENCY`;
lower reduces burst 429s on shared API quotas). Full runs also write `runs/<slug>/_build_progress.json`
so the Streamlit workbench can show **scene N/M** while the build stage runs.

```python
# Optional: raise cap on a dedicated high-rate key
# VIDFORGE_BUILD_CONCURRENCY=6
```

**Impact:** wall-clock is driven by the slowest few scenes plus rate limits, not the sum of all segments.

### 1.3 Token budgets per stage

Currently `max_tokens=4000` is shared. Set explicit caps:
- `plan`: 2500 (segments are small)
- `scene`: 1500 each (CSS + JS)
- `narration`: not LLM-driven

Track tokens via `usage` from each provider, attach to `manifest.json`. Pin
runaway scenes early.

---

## 2. Reliability & quality gates

### 2.1 Lint generated CSS/JS before render

The LLM occasionally produces:
- elements wider than the stage (cropped at render),
- writes inside the bottom 380 px PIP safe zone,
- forgets to scope selectors and bleeds across scenes,
- references missing classes.

Add a fast pre-flight check:

```python
# vidforge/lint.py
def lint_scene(seg, code):
    issues = []
    # 1. selectors must be scoped
    if not all(rule.startswith(f".scene-{seg.index}") for rule in extract_top_level_selectors(code.css)):
        issues.append("css selector leaks scope")
    # 2. JS must reference root
    if "root.querySelector" not in code.js and "root.querySelectorAll" not in code.js:
        issues.append("scene JS does not reference `root`")
    return issues
```

If any issue → re-prompt the LLM with the issue list as feedback. A single
re-prompt usually fixes >90 % of failures and costs ~1k tokens.

### 2.2 Visual regression guard

Render the first 1.0 s of each scene as a screenshot, hash the image, and store
on the manifest. The next run with the same plan should produce the same hash;
if not, the LLM drifted. Surface the diff in the UI.

### 2.3 Subtitle overflow check

After build, run a headless Playwright pass that measures
`document.getElementById('subtitle').scrollHeight` for each segment. Anything
over 2 lines triggers a re-prompt with stricter length budget. Currently you
discover overflowing subtitles only by eyeballing the final video.

---

## 3. Render quality & determinism

### 3.1 Replace `record_video` with frame-by-frame capture

Playwright's `record_video_dir` is opportunistic — it captures whatever the OS
actually paints, so frame timing drifts on a hot machine. For deterministic
output, drive the timeline by `progress` and screenshot each frame:

```python
fps = 30; total = plan.total_duration
for i in range(int(total * fps)):
    t = i / fps
    await page.evaluate(f"window.master.progress({t/total})")  # expose master
    await page.screenshot(path=f"frame_{i:06d}.png", omit_background=False)
# then: ffmpeg -framerate 30 -i frame_%06d.png -pix_fmt yuv420p out.mp4
```

**Impact:** identical bytes across machines; far cleaner antialiasing; trivially
supports any FPS (60, 24).

### 3.2 Hardware-accelerated H.264 on Apple Silicon

Switch the final encode to VideoToolbox:

```bash
ffmpeg -hwaccel videotoolbox -i in.mov -c:v h264_videotoolbox -b:v 10M out.mp4
```

5–8× faster encode at the same quality on M-series Macs.

### 3.3 Local GSAP + font subset

Currently `index.html` loads GSAP from a CDN. For airgap reliability and to
bake in the run as a true artifact, vendor `gsap.min.js` once and reference it
via `file://`. Also subset the Chinese display font (PingFang fallback works
but is OS-dependent) with `pyftsubset` so the same font ships with every run.

---

## 4. Authoring loop

### 4.1 Hand-edit segments.json mid-pipeline

The runner re-uses `segments.json` if it exists. Add a `--from-stage build`
flag so you can:
1. Run `plan` only, inspect, edit `segments.json` in the UI;
2. Click a `Re-build` button → only re-runs build → render → composite.

The dashboard can already show the JSON; what's missing is an in-place editor
(`st_ace` or just `st.text_area` + save button).

### 4.2 A/B hooks

`vidforge auto demo.md --variants 4` should:
1. Run `plan` once to get the segments,
2. For segment 0 (hook) only, ask the LLM for **N alternative drafts**,
3. Build N variants of just that scene,
4. Render N short stand-alone clips of the hook,
5. Show them side by side in the UI for picking.

This is the highest-leverage edit you can make for short videos — a hook A/B
beats every other content tweak.

### 4.3 Cursor skill / Linear webhook

Wrap the runner in a small webhook:

```python
# vidforge/api.py (FastAPI)
@app.post("/runs")
async def create_run(file: UploadFile):
    p = settings.inbox_dir / file.filename
    p.write_bytes(await file.read())
    return {"slug": p.stem}
```

Then any tool — Cursor cloud agent, a Linear "Done" automation, a Slack slash
command — can drop scripts in remotely. Same `inbox/`-based contract.

---

## 5. Observability

### 5.1 Live tail in the dashboard

Each run's `manifest.json` is updated after every stage; a small auto-refresh
in the UI (`st_autorefresh`) lets you watch a run progress without manually
reloading.

### 5.2 Structured logs to JSONL

Replace the rich `console.log` calls in stages with a `logger.info({...})` that
writes one JSON object per stage to `runs/<slug>/log.jsonl`. Trivial to
ship to Datadog/Loki later, no rewrite.

### 5.3 Cost per run

Anthropic and OpenAI both return `usage.input_tokens` / `usage.output_tokens`.
Multiply by current pricing and write to `manifest.cost_usd`. UI shows total
spend per run + month-to-date in the sidebar.

---

## 6. TTS quality

### 6.1 SSML pacing per segment

`edge_tts.Communicate` accepts SSML. Wrap narration with explicit pause tags so
each segment fits its window:

```xml
<speak>
  <prosody rate="-8%">{narration}</prosody>
  <break time="200ms"/>
</speak>
```

If a segment's TTS audio is longer than its time window, slow the animation
down rather than truncating speech (`gsap.timeline().timeScale(window/speech)`).

### 6.2 Multi-voice & ElevenLabs fallback

Configure two voices and alternate by `scene_type` (`hook` → energetic male,
`concept` → calm female). When edge-tts is rate-limited or offline, fall back
to ElevenLabs / Bytedance TTS by abstracting the voice provider behind a
`Synth` interface with same `synth_segments()` shape.

### 6.3 Auto subtitle alignment

Run Whisper on the produced narration to get word-level timestamps, then write
SRT with **per-word** highlights instead of per-segment. Burned subtitles get
the karaoke-style "current word in cyan" effect that performs well on social.

---

## 7. Security & ops

### 7.1 Sandbox the LLM-generated JS

Today the agent's JS runs in a regular Chromium tab on your machine. For runs
triggered by webhooks / external sources, render in a one-shot Docker
container with no host mounts:

```bash
docker run --rm -v "$PWD/runs/<slug>:/work" -w /work mcr.microsoft.com/playwright:v1.46.0-jammy \
  python -m vidforge.render
```

### 7.2 Secret rotation reminder

`.env` is gitignored, but a leaked key is a leaked key. Add a CI/cron task that
checks `OPENAI_API_KEY` last-rotated date via the dashboard API and emails when
it's >90 days old.

### 7.3 Disk hygiene

Each run is ~10 MB. Add a `vidforge gc --keep 30` command that retains the 30
most recent successful runs and deletes the rest after exporting metadata to
SQLite for long-tail analytics.

---

## 8. Roadmap (one-line each, by month)

- **M1** — 1.1 cache, 1.2 parallel build, 4.1 from-stage flag → halves cost & wall time.
- **M2** — 2.1 lint, 2.3 subtitle overflow, 5.1 live tail → fewer regenerations.
- **M3** — 3.1 frame-by-frame, 3.2 hwaccel, 6.1 SSML → broadcast-grade output.
- **M4** — 4.2 A/B hooks, 5.3 cost tracking, 6.3 word subs → social-ready.
- **M5+** — 4.3 webhook, 7.1 docker sandbox, 7.3 GC → multi-tenant ready.
