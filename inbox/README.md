# inbox/

Drop a `.md`, `.txt`, or `.pdf` script here.

Example script (checked into the repo): `examples/xiaohongshu_scifi_kids_30s.md` — copy or symlink it here to try the watcher.

When the watcher is running (`make watch` or the launchd service), every new
file is picked up automatically and processed end-to-end. Each input creates a
self-contained folder under `../runs/<timestamp>__<slug>/` containing:

- `script.md` – your original input
- `segments.json` – LLM-derived shot list
- `project/` – generated `index.html` / `styles.css` / `main.js`
- `narration.mp3` – auto voiceover (edge-tts)
- `animation.mp4` – pure animation render
- `final.mp4` – composite (with PIP if you supplied one)
- `frames/` – auto-extracted scene previews
- `manifest.json` – per-stage status & errors

After processing, the input file is renamed to `*.processed` (or `*.failed`).

Open the dashboard to inspect everything visually:

```bash
make ui      # http://localhost:8501
```
