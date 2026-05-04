"""Streamlit workbench + run browser.

Run: `vidforge ui` (or `streamlit run vidforge/ui.py`).

Tabs:
  * **工作台** — paste a script, start the full pipeline, watch stage progress live.
  * **运行记录** — inspect past runs under `runs/`; **resume** partial/failed runs
    from the last successful stage (manifest history preserved).

The workbench runs `VideoRun.execute()` in a background thread so Streamlit stays
responsive; progress is read from `runs/<slug>/manifest.json` on poll.
"""
from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import streamlit as st

from vidforge.config import Settings, settings
from vidforge.runner import VideoRun, list_runs, load_manifest

PIPELINE_STEPS = [
    ("ingest", "导入"),
    ("plan", "分镜规划"),
    ("build", "动画工程"),
    ("render", "视频渲染"),
    ("narrate", "配音合成"),
    ("composite", "成片合成"),
    ("frames", "关键帧"),
]


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _inline_preview_html(project_dir: Path) -> str:
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
                "error": (st_.error or "")[:120],
            }
        )
    return rows


def _stage_row_for(step_en: str, manifest) -> dict | None:
    for s in reversed(manifest.stages):
        if s.name == step_en:
            return {"success": s.success, "error": s.error, "elapsed": s.elapsed_s}
    return None


def _default_duration_for_run(run_path: Path) -> int:
    seg = run_path / "segments.json"
    if seg.exists():
        try:
            data = json.loads(_read(seg))
            v = data.get("total_duration")
            if v is not None:
                return max(15, min(180, int(float(v))))
        except Exception:
            pass
    return 30


def _workbench_busy() -> bool:
    wr = st.session_state.get("web_poll_run_dir")
    if not wr or not Path(wr).exists():
        return False
    try:
        return load_manifest(Path(wr)).finished_at is None
    except Exception:
        return False


def _render_pipeline_visual(run_dir: Path | None) -> None:
    if not run_dir or not run_dir.exists():
        st.caption("提交脚本后，这里会显示流水线进度。")
        return
    mp = run_dir / "manifest.json"
    if not mp.exists():
        st.warning("manifest 尚未写入，任务可能刚启动…")
        return
    try:
        manifest = load_manifest(run_dir)
    except Exception as exc:
        st.error(f"无法读取 manifest: {exc}")
        return

    err_file = run_dir / "_web_ui_error.txt"
    if err_file.exists():
        st.error("后台线程异常:\n```\n" + _read(err_file)[:4000] + "\n```")

    n_ok = sum(1 for name, _ in PIPELINE_STEPS if (_stage_row_for(name, manifest) or {}).get("success"))
    st.progress(min(n_ok / len(PIPELINE_STEPS), 1.0), text=f"阶段进度 {n_ok}/{len(PIPELINE_STEPS)}")

    cols = st.columns(len(PIPELINE_STEPS))
    for i, (step_en, label) in enumerate(PIPELINE_STEPS):
        row = _stage_row_for(step_en, manifest)
        with cols[i]:
            if row and row.get("error"):
                st.metric(label, "失败", delta=(row["error"] or "")[:40], delta_color="inverse")
            elif row and row.get("success"):
                st.metric(label, "完成", delta=_format_seconds(row.get("elapsed")))
            elif manifest.finished_at is None:
                st.metric(label, "进行中…" if row else "排队")
            else:
                st.metric(label, "\u2014")

    log_path = run_dir / "_web_ui.log"
    if log_path.exists():
        with st.expander("子进程日志（如有）"):
            st.code(_read(log_path)[:8000], language="text")


def _start_background_run(
    run: VideoRun,
    duration: int | None,
    with_narration: bool,
    head: Path | None,
    *,
    resume: bool = False,
) -> None:
    def _go() -> None:
        try:
            run.execute(
                duration=duration,
                with_narration=with_narration,
                head=head,
                resume=resume,
            )
        except Exception as exc:  # noqa: BLE001
            err = run.run_dir / "_web_ui_error.txt"
            err.write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")

    threading.Thread(target=_go, daemon=True).start()


def _fragment_poll_decorator():
    try:
        return st.fragment(run_every=timedelta(seconds=2))  # type: ignore[attr-defined]
    except Exception:  # Streamlit too old
        def _noop(f):
            return f

        return _noop


def _render_workbench() -> None:
    st.subheader("工作台：贴脚本 → 全链路生成")

    if "web_poll_run_dir" not in st.session_state:
        st.session_state.web_poll_run_dir = None

    fragment = _fragment_poll_decorator()

    @fragment
    def _poll() -> None:
        rd = st.session_state.web_poll_run_dir
        if not rd:
            _render_pipeline_visual(None)
            return
        p = Path(rd)
        _render_pipeline_visual(p)

        if (p / "manifest.json").exists():
            try:
                m = load_manifest(p)
                if m.finished_at is not None:
                    toast_key = f"_wb_toast_{rd}"
                    if not st.session_state.get(toast_key):
                        st.session_state[toast_key] = True
                        if m.success:
                            st.toast(f"完成: {m.final_video or (p / 'final.mp4')}", icon="✅")
                        else:
                            st.toast("流水线失败，请查看上方错误", icon="❌")
            except Exception:
                pass

        final_mp4 = p / "final.mp4"
        if final_mp4.exists():
            st.video(str(final_mp4))

    _poll()

    st.divider()
    default_script = "# 30秒口播示例\n\n你好，这是一段测试脚本。\n"
    if "web_script_area" not in st.session_state:
        st.session_state.web_script_area = default_script

    script = st.text_area(
        "脚本内容（支持 Markdown）",
        height=280,
        help="会写入 inbox 并启动与 CLI `vidforge auto` 相同的流水线。",
        key="web_script_area",
    )

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        duration = st.number_input(
            "目标时长（秒）",
            min_value=15,
            max_value=180,
            value=30,
            step=5,
        )
    with c2:
        with_audio = st.toggle("自动配音（edge-tts）", value=True)
    with c3:
        head_up = st.file_uploader("口播视频 PIP（可选, mp4）", type=["mp4"])

    run_dir_str = st.session_state.web_poll_run_dir
    busy = _workbench_busy()

    b1, b2 = st.columns(2)
    with b1:
        start = st.button(
            "开始生成视频",
            type="primary",
            disabled=busy or not script.strip(),
            use_container_width=True,
        )
    with b2:
        if st.button("清除当前任务标记", disabled=not run_dir_str, use_container_width=True):
            st.session_state.web_poll_run_dir = None
            st.rerun()

    if start:
        settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        stem = f"webui_{uuid4().hex[:10]}"
        script_path = settings.inbox_dir / f"{stem}.md"
        script_path.write_text(script.strip() + "\n", encoding="utf-8")

        head_path: Path | None = None
        run = VideoRun(script_path)
        if head_up is not None:
            head_path = run.run_dir / "uploaded_talking_head.mp4"
            head_path.write_bytes(head_up.getvalue())

        st.session_state.web_poll_run_dir = str(run.run_dir)
        _start_background_run(run, int(duration), with_narration=with_audio, head=head_path, resume=False)
        st.success(f"任务已启动: `{run.run_dir.name}`")
        st.rerun()

    _builtin_provider = Settings.model_fields["llm_provider"].default
    _provider_overridden = settings.llm_provider != _builtin_provider
    _override_hint = (
        "（`VIDFORGE_LLM_PROVIDER` 等环境变量已覆盖代码默认的 DeepSeek。）"
        if _provider_overridden
        else ""
    )
    st.caption(
        f"请配置对应 API Key（见 `.env.example`）。"
        f"当前 LLM：**厂商** `{settings.llm_provider}`，**模型** `{settings.llm_model}`"
        f"（与后台 `settings` 一致）{_override_hint}"
        f" 成片目录：`{settings.runs_dir}`。"
    )


def _render_run_browser() -> None:
    runs = list_runs()
    sb = st.sidebar
    sb.markdown("### 运行记录")
    sb.caption(f"`{settings.runs_dir}`")

    if not runs:
        st.info(
            f"尚无历史 run。在「工作台」贴脚本生成，或将 `.md` 放进 `{settings.inbox_dir}` 并运行 watcher。"
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

    if not manifest.success:
        st.info(
            "该 run 未完成。可从已成功且磁盘产物齐全的阶段之后**续跑**；新阶段会追加写入 "
            "`manifest.json`（保留历史记录）。续跑开始后也可在「工作台」查看同一目录进度。"
        )
        busy_wb = _workbench_busy()
        rdur = _default_duration_for_run(selected)
        r1, r2 = st.columns(2)
        with r1:
            resume_dur = st.number_input(
                "续跑：规划时长（秒，仅当重跑「plan」时生效）",
                min_value=15,
                max_value=180,
                value=rdur,
                step=5,
                key=f"resume_dur_{selected.name}",
                disabled=busy_wb,
            )
        with r2:
            resume_audio = st.toggle(
                "续跑：自动配音（无 PIP 时）",
                value=True,
                key=f"resume_aud_{selected.name}",
                disabled=busy_wb,
            )
        resume_head = st.file_uploader(
            "续跑：口播 PIP（可选，覆盖/新上传）",
            type=["mp4"],
            key=f"resume_head_{selected.name}",
            disabled=busy_wb,
        )
        if st.button(
            "从断点继续（后台）",
            type="primary",
            key=f"resume_btn_{selected.name}",
            disabled=busy_wb,
            help="复用 manifest 中已成功的阶段；若磁盘上缺少对应产物则会自动重跑该阶段。",
        ):
            run = VideoRun.from_run_dir(selected)
            head_p: Path | None = None
            if resume_head is not None:
                head_p = run.run_dir / "uploaded_talking_head.mp4"
                head_p.write_bytes(resume_head.getvalue())
            st.session_state.web_poll_run_dir = str(selected)
            _start_background_run(
                run,
                int(resume_dur),
                with_narration=resume_audio,
                head=head_p,
                resume=True,
            )
            st.success(f"续跑已启动：`{selected.name}`。可在「工作台」查看进度。")
            st.rerun()

    n_ok = sum(1 for name, _ in PIPELINE_STEPS if (_stage_row_for(name, manifest) or {}).get("success"))
    st.progress(min(n_ok / len(PIPELINE_STEPS), 1.0), text=f"阶段 {n_ok}/{len(PIPELINE_STEPS)}")

    final_video = selected / "final.mp4"
    anim_video = selected / "animation.mp4"
    show_video = final_video if final_video.exists() else anim_video
    if show_video.exists():
        st.video(str(show_video))

    tabs = st.tabs(
        [
            "分镜 JSON",
            "场景预览",
            "项目文件",
            "关键帧",
            "素材",
            "运行日志",
        ]
    )

    with tabs[0]:
        seg_path = selected / "segments.json"
        if seg_path.exists():
            data = json.loads(_read(seg_path))
            cols = st.columns([1, 1])
            cols[0].metric("title", data.get("title", "-"))
            cols[1].metric("segments", len(data.get("segments", [])))
            seg_rows = []
            for seg in data.get("segments", []):
                nar = seg.get("narration", "") or ""
                seg_rows.append(
                    {
                        "idx": seg["index"],
                        "type": seg.get("scene_type"),
                        "start": seg.get("start"),
                        "end": seg.get("end"),
                        "subtitle": seg.get("subtitle"),
                        "narration": nar[:60] + ("..." if len(nar) > 60 else ""),
                    }
                )
            st.dataframe(seg_rows, hide_index=True, width="stretch")
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
                    "html"
                    if f.suffix == ".html"
                    else "css"
                    if f.suffix == ".css"
                    else "javascript"
                    if f.suffix == ".js"
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
            fc = st.columns(min(5, len(frames)))
            for i, f in enumerate(frames):
                fc[i % len(fc)].image(str(f), caption=f.stem, use_container_width=True)
        else:
            st.info("No frames extracted yet (frames stage not finished).")

    with tabs[4]:
        st.caption("All raw artifacts in this run folder:")
        rows = []
        for f in sorted(selected.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(selected)
            rows.append(
                {
                    "path": str(rel),
                    "bytes": f.stat().st_size,
                }
            )
        st.dataframe(rows, hide_index=True, width="stretch")
        narration = selected / "narration.mp3"
        if narration.exists():
            st.audio(str(narration))

    with tabs[5]:
        st.dataframe(_stage_table_rows(manifest.stages), hide_index=True, width="stretch")
        with st.expander("Manifest JSON"):
            st.json(json.loads(manifest.model_dump_json()))


def app() -> None:
    st.set_page_config(page_title="vidforge", layout="wide", page_icon="🎬")
    st.markdown("# 🎬 vidforge 工作台")

    tab_wb, tab_hist = st.tabs(["🧠 工作台（生产链路）", "📋 运行记录"])

    with tab_wb:
        _render_workbench()

    with tab_hist:
        _render_run_browser()


if __name__ == "__main__":
    app()
