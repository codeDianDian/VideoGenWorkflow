"""Streamlit dashboard for vidforge runs.

Run with: `vidforge ui` (or `streamlit run vidforge/ui.py`).
Lists every run under `runs/`, lets you inspect:
  * the segments.json (\u5206\u955c JSON)
  * the rendered HTML/CSS/JS project
  * a live HTML preview of the project
  * the auto-extracted preview frames
  * the final MP4 (with audio)
  * per-stage timing & error log
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from vidforge.config import settings
from vidforge.runner import list_runs, load_manifest


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _inline_preview_html(project_dir: Path) -> str:
    """Return a single self-contained HTML doc by inlining styles.css and main.js."""
    index_html = _read(project_dir / "index.html")
    css = _read(project_dir / "styles.css")
    js = _read(project_dir / "main.js")
    if not index_html:
        return ""
    html = index_html.replace(
        '<link rel="stylesheet" href="./styles.css" />',
        f"<style>{css}</style>",
    )
    html = html.replace(
        '<script src="./main.js"></script>',
        f"<script>{js}</script>",
    )
    return html


def _format_seconds(s: float | None) -> str:
    if s is None:
        return "-"
    return f"{s:.1f}s"


def _stage_table_rows(stages):
    rows = []
    for st_ in stages:
        elapsed = st_.elapsed_s
        rows.append(
            {
                "stage": st_.name,
                "ok": "\u2713" if st_.success else ("\u2717" if st_.error else "..."),
                "elapsed": _format_seconds(elapsed),
                "started": st_.started_at.strftime("%H:%M:%S"),
                "error": (st_.error or "")[:80],
            }
        )
    return rows


def app() -> None:
    st.set_page_config(page_title="vidforge", layout="wide", page_icon="\U0001F3AC")
    st.markdown("## \U0001F3AC vidforge dashboard")

    runs = list_runs()
    sb = st.sidebar
    sb.markdown("### Runs")
    sb.caption(f"`{settings.runs_dir}`")

    if not runs:
        st.info(
            f"No runs yet. Drop a `.md`/`.txt`/`.pdf` into `{settings.inbox_dir}` and start the watcher "
            f"(`make watch`), or run `vidforge auto path/to/script.md`."
        )
        return

    labels = []
    for r in runs:
        try:
            m = load_manifest(r)
            mark = "\u2713" if m.success else ("\u2717" if m.finished_at else "\u25cb")
            labels.append(f"{mark} {r.name}")
        except Exception:
            labels.append(f"? {r.name}")

    pick = sb.radio("Select", labels, index=0, label_visibility="collapsed")
    selected = runs[labels.index(pick)]
    manifest = load_manifest(selected)

    head_cols = st.columns([3, 1, 1, 1])
    head_cols[0].markdown(f"### {manifest.title or manifest.slug}")
    head_cols[1].metric("status", "\u2713 done" if manifest.success else "running/failed")
    head_cols[2].metric("duration", _format_seconds(manifest.duration_s))
    head_cols[3].metric("stages", len(manifest.stages))

    final_video = selected / "final.mp4"
    anim_video = selected / "animation.mp4"
    show_video = final_video if final_video.exists() else anim_video
    if show_video.exists():
        st.video(str(show_video))

    tabs = st.tabs([
        "\u5206\u955c JSON",
        "\u573a\u666f\u9884\u89c8",
        "\u9879\u76ee\u6587\u4ef6",
        "\u5173\u952e\u5e27",
        "\u7d20\u6750",
        "\u8fd0\u884c\u65e5\u5fd7",
    ])

    with tabs[0]:
        seg_path = selected / "segments.json"
        if seg_path.exists():
            data = json.loads(_read(seg_path))
            cols = st.columns([1, 1])
            cols[0].metric("title", data.get("title", "-"))
            cols[1].metric("segments", len(data.get("segments", [])))
            seg_rows = []
            for seg in data.get("segments", []):
                seg_rows.append(
                    {
                        "idx": seg["index"],
                        "type": seg.get("scene_type"),
                        "start": seg.get("start"),
                        "end": seg.get("end"),
                        "subtitle": seg.get("subtitle"),
                        "narration": seg.get("narration", "")[:60] + ("..." if len(seg.get("narration", "")) > 60 else ""),
                    }
                )
            st.dataframe(seg_rows, use_container_width=True, hide_index=True)
            with st.expander("Raw JSON"):
                st.json(data)
        else:
            st.warning("No segments.json yet.")

    with tabs[1]:
        proj = selected / "project"
        if (proj / "index.html").exists():
            html = _inline_preview_html(proj)
            scale = st.slider("preview scale", 0.2, 0.6, 0.32, 0.02)
            wrap = (
                f"<div style='transform:scale({scale});transform-origin:top left;"
                f"width:{settings.width}px;height:{settings.height}px'>"
                f"{html}</div>"
            )
            st.components.v1.html(
                wrap,
                height=int(settings.height * scale + 80),
                scrolling=True,
            )
        else:
            st.warning("Project HTML not built yet.")

    with tabs[2]:
        proj = selected / "project"
        if proj.exists():
            for f in sorted(proj.glob("*")):
                if not f.is_file():
                    continue
                lang = (
                    "html" if f.suffix == ".html"
                    else "css" if f.suffix == ".css"
                    else "javascript" if f.suffix == ".js"
                    else "text"
                )
                with st.expander(f"{f.name} ({f.stat().st_size:,} bytes)"):
                    st.code(_read(f), language=lang, line_numbers=True)
        else:
            st.warning("No project files yet.")

    with tabs[3]:
        frames_dir = selected / "frames"
        frames = sorted(frames_dir.glob("*.jpg")) if frames_dir.exists() else []
        if frames:
            cols = st.columns(min(5, len(frames)))
            for i, f in enumerate(frames):
                cols[i % len(cols)].image(str(f), caption=f.stem, use_container_width=True)
        else:
            st.info("No frames extracted yet (frames stage not finished).")

    with tabs[4]:
        st.caption("All raw artifacts in this run folder:")
        rows = []
        for f in sorted(selected.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(selected)
            rows.append({
                "path": str(rel),
                "bytes": f.stat().st_size,
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
        narration = selected / "narration.mp3"
        if narration.exists():
            st.audio(str(narration))

    with tabs[5]:
        st.dataframe(_stage_table_rows(manifest.stages), use_container_width=True, hide_index=True)
        with st.expander("Manifest JSON"):
            st.json(json.loads(manifest.model_dump_json()))


if __name__ == "__main__":
    app()
