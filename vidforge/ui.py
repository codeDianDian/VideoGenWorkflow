"""Streamlit workbench + run browser.

Run: `vidforge ui` (or `streamlit run vidforge/ui.py`).

Tabs:
  * **工作台** — paste a script, start the full pipeline, watch stage progress live.
  * **运行记录** — inspect past runs under `runs/`; **resume** partial/failed runs
    from the last successful stage (manifest history preserved); **full redo** from
    the same `script.md` into a new timestamped run folder.

The workbench runs `VideoRun.execute()` in a background thread so Streamlit stays
responsive; progress is read from `runs/<slug>/manifest.json` on poll.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import streamlit as st

from vidforge import optimize as optimize_stage
from vidforge.config import Settings, settings
from vidforge.runner import VideoRun, list_runs, load_manifest
from vidforge.schemas import ScriptPlan

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
    preview_boot = """
<script>
(function () {
  function showPreviewError(message) {
    var box = document.createElement("div");
    box.style.cssText = "position:fixed;left:24px;right:24px;top:24px;z-index:9999;padding:18px 22px;background:#7f1d1d;color:white;font:28px sans-serif;border-radius:8px;";
    box.textContent = message;
    document.body.appendChild(box);
  }
  function startPreview() {
    if (!window.gsap) {
      showPreviewError("GSAP 加载失败，场景预览无法播放。请检查网络或改成本地 GSAP。");
      return;
    }
    if (window.__startVideo) window.__startVideo();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { requestAnimationFrame(startPreview); });
  } else {
    requestAnimationFrame(startPreview);
  }
})();
</script>
"""
    html = html.replace(
        '<script src="./main.js"></script>',
        f"<script>{js}</script>{preview_boot}",
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


def _read_build_progress_caption(run_dir: Path) -> str | None:
    p = run_dir / "_build_progress.json"
    if not p.exists():
        return None
    try:
        bp = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    phase = bp.get("phase")
    updated = bp.get("updated_at") or ""
    if phase == "llm_scenes":
        c = bp.get("completed", 0)
        t = bp.get("total", "?")
        cap = bp.get("concurrency")
        inflight = bp.get("in_flight")
        parts = [f"动画工程（LLM）：已完成 {c}/{t} 个分镜"]
        if isinstance(inflight, list) and inflight:
            nums = sorted(int(x) for x in inflight)
            if len(nums) <= 4:
                parts.append("进行中: " + ", ".join(f"scene_{n:02d}" for n in nums))
            else:
                parts.append(f"进行中 {len(nums)} 路并发（含 scene_{nums[0]:02d}…）")
        else:
            wi = bp.get("working_on_index")
            if wi is not None:
                parts.append(f"当前生成 scene_{int(wi):02d}")
        if cap:
            parts.append(f"并发上限 {cap}")
        if updated:
            parts.append(f"快照 {updated}")
        return " · ".join(parts)
    if phase == "templates":
        msg = "动画工程：正在组装 HTML / CSS / JS …"
        return f"{msg} · {updated}" if updated else msg
    return None


def _render_diagnostic_logs(run_dir: Path) -> None:
    err_file = run_dir / "_web_ui_error.txt"
    if err_file.exists():
        st.error("流水线异常（完整 traceback）:\n```\n" + _read(err_file)[:12000] + "\n```")

    for log_name, log_title in (
        ("pipeline_error.log", "pipeline_error.log（历次崩溃追加）"),
        ("render_error.log", "render_error.log（Playwright：dataset / 浏览器控制台）"),
        ("render_ffmpeg_error.log", "render_ffmpeg_error.log（ffmpeg 转码）"),
    ):
        lp = run_dir / log_name
        if lp.exists():
            with st.expander(log_title):
                st.code(_read(lp)[-24000:], language="text")


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

    _render_diagnostic_logs(run_dir)

    n_ok = sum(1 for name, _ in PIPELINE_STEPS if (_stage_row_for(name, manifest) or {}).get("success"))
    st.progress(min(n_ok / len(PIPELINE_STEPS), 1.0), text=f"阶段进度 {n_ok}/{len(PIPELINE_STEPS)}")

    if manifest.finished_at is None:
        bpc = _read_build_progress_caption(run_dir)
        if bpc:
            st.caption(bpc)

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


def _load_plan_for_ui(run_dir: Path) -> ScriptPlan | None:
    plan_path = run_dir / "segments.json"
    if not plan_path.exists():
        return None
    try:
        return ScriptPlan.model_validate(json.loads(_read(plan_path)))
    except Exception as exc:
        st.error(f"无法读取 segments.json: {exc}")
        return None


def _plan_from_tune_rows(plan: ScriptPlan, rows: list[dict]) -> ScriptPlan:
    data = plan.model_dump(mode="json")
    rows_by_idx = {int(r["idx"]): r for r in rows}
    for seg in data["segments"]:
        row = rows_by_idx[int(seg["index"])]
        seg["start"] = float(row["start"])
        seg["end"] = float(row["end"])
        seg["subtitle"] = str(row["subtitle"])
        seg["narration"] = str(row["narration"])
    data["segments"].sort(key=lambda s: (float(s["start"]), int(s["index"])))
    data["total_duration"] = max(float(s["end"]) for s in data["segments"])
    return ScriptPlan.model_validate(data)


def _tuning_from_tune_rows(rows: list[dict]) -> dict[int, dict[str, float]]:
    return {
        int(row["idx"]): {
            "audio_shift_ms": float(row.get("audio_shift_ms", 0.0)),
            "guard_gap_s": float(row.get("guard_gap_s", 0.08)),
            "fade_out_ms": float(row.get("fade_out_ms", 120.0)),
            "volume_pct": float(row.get("volume_pct", 100.0)),
        }
        for row in rows
    }


def _tune_widget_gen_storage_key(run_dir_name: str) -> str:
    return f"tune_widget_gen_{run_dir_name}"


def _tune_widget_key_suffix(run_dir_name: str) -> str:
    gen = int(st.session_state.get(_tune_widget_gen_storage_key(run_dir_name), 0))
    return f"_g{gen}"


def _bump_tune_widget_generation(run_dir_name: str) -> None:
    k = _tune_widget_gen_storage_key(run_dir_name)
    st.session_state[k] = int(st.session_state.get(k, 0)) + 1


def _raw_timeline_issues(rows: list[dict]) -> list[str]:
    issues: list[str] = []
    ordered = sorted(rows, key=lambda r: (float(r["start"]), int(r["idx"])))
    for i, row in enumerate(ordered):
        idx = int(row["idx"])
        start = float(row["start"])
        end = float(row["end"])
        if end <= start:
            issues.append(f"scene_{idx:02d}: 结束时间必须大于开始时间")
        if i > 0:
            prev = ordered[i - 1]
            gap = start - float(prev["end"])
            if gap < -0.05:
                issues.append(f"scene_{idx:02d}: 与 scene_{int(prev['idx']):02d} 重叠 {abs(gap):.2f}s")
            elif gap > 0.35:
                issues.append(f"scene_{idx:02d}: 与上一段有 {gap:.2f}s 空隙")
    return issues


def _render_post_tune(selected: Path) -> None:
    plan = _load_plan_for_ui(selected)
    if plan is None:
        st.warning("需要先完成分镜规划，才能微调。")
        return

    st.subheader("后期微调")
    tuned_final = selected / "final_tuned.mp4"
    tuned_narration = selected / "narration_tuned.mp3"
    if tuned_final.exists():
        st.video(str(tuned_final))
    if tuned_narration.exists():
        st.audio(str(tuned_narration))

    with st.expander("参数说明", expanded=True):
        st.markdown(
            """
- **画面开始 / 画面结束**：改变分镜在总时间轴里的位置。只保存或只重合成时，主要影响旁白放置；要让动画画面也跟着改，需要点 **重渲染动画+成片**。
- **旁白偏移**：只移动该段旁白。正数让声音更晚出现，负数让声音更早出现，适合修音画不同步。
- **段尾留白**：合成时从分镜末尾预留的安全间隔。值越大，旁白越早被裁切，越不容易压到下一段。
- **裁切淡出**：旁白被裁切时尾部淡出的时间，避免硬切产生突兀断音。
- **旁白音量**：只调该段 TTS 音量，不改变背景动画。
"""
        )

    saved_tuning = optimize_stage.load_tuning(selected, plan)
    baseline_diag = optimize_stage.tts_diagnostics(selected, plan, saved_tuning)
    problematic = {int(r["idx"]) for r in baseline_diag if int(r.get("issue_count") or 0) > 0}

    wk_suffix = _tune_widget_key_suffix(selected.name)

    rows: list[dict] = []
    for seg in plan.segments:
        opts = saved_tuning.get(int(seg.index), {})
        expanded = int(seg.index) in problematic
        with st.expander(f"scene_{seg.index:02d} · {seg.subtitle}", expanded=expanded):
            c_time1, c_time2, c_audio1, c_audio2 = st.columns(4)
            with c_time1:
                start = st.number_input(
                    "画面开始 (s)",
                    min_value=0.0,
                    step=0.1,
                    format="%.2f",
                    value=float(seg.start),
                    key=f"tune_start_{selected.name}_{seg.index}{wk_suffix}",
                    help="该分镜在总时间轴中开始显示的秒数。改它会影响字幕和旁白位置；要同步改变动画，需要重渲染。",
                )
            with c_time2:
                end = st.number_input(
                    "画面结束 (s)",
                    min_value=0.1,
                    step=0.1,
                    format="%.2f",
                    value=float(seg.end),
                    key=f"tune_end_{selected.name}_{seg.index}{wk_suffix}",
                    help="该分镜结束显示的秒数。缩短会让该段可容纳的旁白时间变短。",
                )
            with c_audio1:
                audio_shift_ms = st.number_input(
                    "旁白偏移 (ms)",
                    min_value=-5000.0,
                    max_value=5000.0,
                    step=20.0,
                    format="%.0f",
                    value=float(opts.get("audio_shift_ms", 0.0)),
                    key=f"tune_shift_{selected.name}_{seg.index}{wk_suffix}",
                    help="只移动该段旁白：正数=延后，负数=提前。常用范围是 -300 到 +300ms。",
                )
            with c_audio2:
                volume_pct = st.number_input(
                    "旁白音量 (%)",
                    min_value=0.0,
                    max_value=200.0,
                    step=5.0,
                    format="%.0f",
                    value=float(opts.get("volume_pct", 100.0)),
                    key=f"tune_volume_{selected.name}_{seg.index}{wk_suffix}",
                    help="该段旁白的相对音量。100 为原始音量，80 表示略降，120 表示略提高。",
                )

            c_audio3, c_audio4 = st.columns(2)
            with c_audio3:
                guard_gap_s = st.number_input(
                    "段尾留白 (s)",
                    min_value=0.0,
                    max_value=2.0,
                    step=0.02,
                    format="%.2f",
                    value=float(opts.get("guard_gap_s", 0.08)),
                    key=f"tune_guard_{selected.name}_{seg.index}{wk_suffix}",
                    help="从该分镜尾部预留的静音安全区。提高它会更早裁切该段旁白，减少压到下一段的风险。",
                )
            with c_audio4:
                fade_out_ms = st.number_input(
                    "裁切淡出 (ms)",
                    min_value=0.0,
                    max_value=1000.0,
                    step=20.0,
                    format="%.0f",
                    value=float(opts.get("fade_out_ms", 120.0)),
                    key=f"tune_fade_{selected.name}_{seg.index}{wk_suffix}",
                    help="如果旁白被裁切，尾部用多长时间淡出。0 表示硬切，80-180ms 通常比较自然。",
                )

            subtitle = st.text_input(
                "字幕",
                value=seg.subtitle,
                key=f"tune_subtitle_{selected.name}_{seg.index}{wk_suffix}",
                help="屏幕底部字幕文本。保存后会同步写入 segments.json 和项目配置。",
            )
            narration = st.text_area(
                "旁白文案",
                value=seg.narration,
                height=72,
                key=f"tune_narration_{selected.name}_{seg.index}{wk_suffix}",
                help="TTS 朗读文本。修改后需要点击“重新生成TTS+成片”才会重新合成语音。",
            )
            rows.append(
                {
                    "idx": seg.index,
                    "type": seg.scene_type,
                    "start": float(start),
                    "end": float(end),
                    "subtitle": subtitle,
                    "narration": narration,
                    "audio_shift_ms": float(audio_shift_ms),
                    "guard_gap_s": float(guard_gap_s),
                    "fade_out_ms": float(fade_out_ms),
                    "volume_pct": float(volume_pct),
                }
            )

    raw_issues = _raw_timeline_issues(rows)
    current_tuning = _tuning_from_tune_rows(rows)
    try:
        new_plan = _plan_from_tune_rows(plan, rows)
    except Exception as exc:
        new_plan = None
        raw_issues.append(f"segments.json 校验失败：{exc}")

    visual_timing_changed = any(
        abs(float(row["start"]) - float(seg.start)) > 0.001
        or abs(float(row["end"]) - float(seg.end)) > 0.001
        for row, seg in zip(rows, plan.segments)
    )
    narration_changed = any(
        str(row["narration"]) != str(seg.narration)
        for row, seg in zip(rows, plan.segments)
    )
    if visual_timing_changed:
        st.info("你改了画面开始/结束。只重合成会调整旁白时间；如果要画面转场也按新时间走，请使用“重渲染动画+成片”。")
    if narration_changed:
        st.info("你改了旁白文案。普通重合成会继续使用旧 TTS 文件；需要新声音请使用“重新生成TTS+成片”。")
    if raw_issues:
        st.error("时间轴基础校验：\n\n" + "\n".join(f"- {i}" for i in raw_issues))

    diag_plan = new_plan or plan
    diag = optimize_stage.tts_diagnostics(selected, diag_plan, current_tuning)
    issue_rows = [r for r in diag if int(r.get("issue_count") or 0) > 0]
    rep_key = f"_auto_tune_report_{selected.name}"
    if rep_key in st.session_state:
        with st.expander("上次「根据 issues 自动优化」的说明", expanded=False):
            st.code(st.session_state[rep_key], language="text")

    auto_clicked = False
    if issue_rows:
        cwarn, cauto = st.columns([3, 1])
        with cwarn:
            st.warning(
                f"检测到 {len(issue_rows)} 段潜在时间轴不适配；查看「时间轴标注」列，或使用右侧一键优化。"
            )
        with cauto:
            auto_clicked = st.button(
                "根据 issues 自动优化",
                key=f"auto_issue_tune_{selected.name}",
                use_container_width=True,
                disabled=new_plan is None,
                help="依据诊断 issue 类型拉长/对齐分镜窗口，并微调旁白偏移等参数。不会修改旁白文案；缺 TTS 时请随后点击「重新生成 TTS+成片」。",
            )
    else:
        st.success("当前分镜和旁白时间轴没有明显冲突。")

    if auto_clicked:
        try:
            assert new_plan is not None
            opt_plan, opt_tuning, rep_lines = optimize_stage.auto_optimize_from_issues(
                selected, new_plan, current_tuning
            )
            optimize_stage.write_plan_with_backup(opt_plan, selected / "segments.json")
            optimize_stage.save_tuning(selected, opt_tuning)
            optimize_stage.patch_project_plan(selected / "project", opt_plan)
            st.session_state[rep_key] = "\n".join(rep_lines)
            st.success("已写入优化后的 segments.json、post_tune.json，并同步 project/index.html。")
            _bump_tune_widget_generation(selected.name)
            st.rerun()
        except Exception as exc:
            st.error(f"自动优化失败: {exc}")
    display_diag = [
        {k: v for k, v in r.items() if k != "previous_audio_end"} for r in diag
    ]
    st.dataframe(
        display_diag,
        hide_index=True,
        width="stretch",
        column_config={
            "idx": st.column_config.NumberColumn("分镜"),
            "start": st.column_config.NumberColumn("画面开始", format="%.2f"),
            "end": st.column_config.NumberColumn("画面结束", format="%.2f"),
            "window_s": st.column_config.NumberColumn("画面窗口", format="%.2f"),
            "tts_s": st.column_config.NumberColumn("原TTS时长", format="%.2f"),
            "shift_ms": st.column_config.NumberColumn("旁白偏移ms", format="%.0f"),
            "clip_s": st.column_config.NumberColumn("合成保留", format="%.2f"),
            "audio_start": st.column_config.NumberColumn("旁白开始", format="%.2f"),
            "audio_end": st.column_config.NumberColumn("旁白结束", format="%.2f"),
            "overflow_s": st.column_config.NumberColumn("会裁切", format="%.2f"),
            "issue_count": st.column_config.NumberColumn("问题数"),
            "issues": st.column_config.TextColumn("时间轴标注"),
            "issue_codes": st.column_config.TextColumn("issue代码", width="small"),
            "subtitle": st.column_config.TextColumn("字幕"),
            "narration": st.column_config.TextColumn("旁白"),
        },
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button(
            "保存参数/分镜",
            key=f"save_tune_{selected.name}",
            use_container_width=True,
            disabled=new_plan is None,
        ):
            try:
                assert new_plan is not None
                optimize_stage.write_plan_with_backup(new_plan, selected / "segments.json")
                optimize_stage.save_tuning(selected, current_tuning)
                optimize_stage.patch_project_plan(selected / "project", new_plan)
                st.success("已保存 segments.json、post_tune.json，并同步更新 project/index.html。")
                _bump_tune_widget_generation(selected.name)
                st.rerun()
            except Exception as exc:
                st.error(f"保存失败: {exc}")
    with c2:
        if st.button(
            "只重合成",
            key=f"mix_tune_{selected.name}",
            use_container_width=True,
            disabled=new_plan is None,
            help="使用现有 animation.mp4 和现有 TTS 文件，只应用旁白偏移、裁切、淡出、音量参数。",
        ):
            try:
                assert new_plan is not None
                optimize_stage.write_plan_with_backup(new_plan, selected / "segments.json")
                optimize_stage.save_tuning(selected, current_tuning)
                with st.spinner("正在重合成 narration_tuned.mp3 / final_tuned.mp4..."):
                    out = optimize_stage.rebuild_tuned_outputs(
                        selected,
                        new_plan,
                        render_animation=False,
                        synth_tts=False,
                        apply_overrides=True,
                        segment_options=current_tuning,
                    )
                st.success(f"已生成优化版: {out['final'].name}")
                _bump_tune_widget_generation(selected.name)
                st.rerun()
            except Exception as exc:
                st.error(f"重合成失败: {exc}")
    with c3:
        if st.button(
            "重新生成TTS+成片",
            key=f"resynth_tune_{selected.name}",
            use_container_width=True,
            disabled=new_plan is None,
            help="旁白文案变更后使用。会重新调用 TTS，再按当前偏移/裁切参数合成成片。",
        ):
            try:
                assert new_plan is not None
                optimize_stage.write_plan_with_backup(new_plan, selected / "segments.json")
                optimize_stage.save_tuning(selected, current_tuning)
                with st.spinner("正在重新生成 TTS 并合成优化版..."):
                    out = optimize_stage.rebuild_tuned_outputs(
                        selected,
                        new_plan,
                        render_animation=False,
                        synth_tts=True,
                        apply_overrides=True,
                        segment_options=current_tuning,
                    )
                st.success(f"已生成优化版: {out['final'].name}")
                _bump_tune_widget_generation(selected.name)
                st.rerun()
            except Exception as exc:
                st.error(f"重新生成失败: {exc}")
    with c4:
        if st.button(
            "重渲染动画+成片",
            key=f"rerender_tune_{selected.name}",
            use_container_width=True,
            disabled=new_plan is None,
            help="画面开始/结束变更后使用。会重新渲染动画，并按当前旁白参数合成。",
        ):
            try:
                assert new_plan is not None
                optimize_stage.write_plan_with_backup(new_plan, selected / "segments.json")
                optimize_stage.save_tuning(selected, current_tuning)
                with st.spinner("正在重渲染动画并生成优化版..."):
                    out = optimize_stage.rebuild_tuned_outputs(
                        selected,
                        new_plan,
                        render_animation=True,
                        synth_tts=narration_changed,
                        apply_overrides=True,
                        segment_options=current_tuning,
                    )
                st.success(f"已生成优化版: {out['final'].name}")
                _bump_tune_widget_generation(selected.name)
                st.rerun()
            except Exception as exc:
                st.error(f"重渲染失败: {exc}")

    st.divider()
    st.subheader("关键帧生成 / 替换")
    seg_labels = {
        f"scene_{seg.index:02d}  {seg.subtitle}": seg
        for seg in plan.segments
    }
    picked_label = st.selectbox(
        "选择分镜",
        list(seg_labels.keys()),
        key=f"frame_scene_{selected.name}",
    )
    picked = seg_labels[picked_label]
    existing = optimize_stage.frame_override_files(selected).get(picked.index)
    if existing:
        st.image(str(existing), caption=f"当前覆盖图: {existing.name}", use_container_width=True)

    upload = st.file_uploader(
        "上传替换图（png/jpg）",
        type=["png", "jpg", "jpeg"],
        key=f"frame_upload_{selected.name}_{picked.index}",
    )
    if upload is not None and st.button(
        "保存上传图为覆盖关键帧",
        key=f"save_upload_{selected.name}_{picked.index}",
    ):
        suffix = Path(upload.name).suffix or ".png"
        out = optimize_stage.save_frame_override(selected, picked.index, upload.getvalue(), suffix)
        st.success(f"已保存覆盖图: {out.name}")
        st.rerun()

    provider = st.selectbox(
        "文生图 provider",
        ["openai", "http-json"],
        key=f"image_provider_{selected.name}",
        help="openai 使用 OPENAI_API_KEY/VIDFORGE_IMAGE_API_KEY；http-json 使用 VIDFORGE_IMAGE_API_URL。",
    )
    model = st.text_input(
        "文生图模型",
        value=settings.image_model,
        key=f"image_model_{selected.name}",
        help="用于生成覆盖关键帧的模型名，只影响关键帧生成，不影响时间轴或声音。",
    )
    prompt = st.text_area(
        "关键帧提示词",
        value=(
            f"Create a polished 9:16 keyframe for this video segment. "
            f"Subtitle: {picked.subtitle}. Visual brief: {picked.visuals}. "
            "Style: premium warm editorial motion design, crisp typography, layered cards, no watermark."
        ),
        height=140,
        key=f"image_prompt_{selected.name}_{picked.index}",
        help="描述要替换到该分镜时间段的静态画面。保存后需要点击“应用覆盖图到视频片段”才会进入最终视频。",
    )
    if st.button("调用文生图并替换关键帧", key=f"gen_frame_{selected.name}_{picked.index}"):
        try:
            out = selected / "frame_overrides" / f"scene_{picked.index:02d}.png"
            with st.spinner("正在生成关键帧..."):
                generated = optimize_stage.generate_keyframe_image(
                    provider=provider,
                    prompt=prompt,
                    output_path=out,
                    model=model,
                )
                optimize_stage.save_frame_override(selected, picked.index, generated.read_bytes(), ".png")
            st.success(f"已生成并保存: scene_{picked.index:02d}.png")
            st.rerun()
        except Exception as exc:
            st.error(f"文生图失败: {exc}")

    if st.button("应用覆盖图到视频片段", key=f"apply_override_{selected.name}", type="primary"):
        try:
            with st.spinner("正在把覆盖关键帧应用到对应分镜时间段..."):
                out = optimize_stage.rebuild_tuned_outputs(
                    selected,
                    new_plan or plan,
                    render_animation=False,
                    synth_tts=False,
                    apply_overrides=True,
                    segment_options=current_tuning,
                )
            st.success(f"已生成带覆盖图的优化版: {out['final'].name}")
            st.rerun()
        except Exception as exc:
            st.error(f"应用失败: {exc}")


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
        except Exception:
            # runner.execute 已在 pipeline_error.log / _web_ui_error.txt 写入完整 traceback
            pass

    threading.Thread(target=_go, daemon=True).start()


def _fragment_poll_decorator():
    try:
        poll_s = float(os.environ.get("VIDFORGE_UI_POLL_S", "1"))
        poll_s = max(0.4, min(poll_s, 15.0))
        return st.fragment(run_every=timedelta(seconds=poll_s))  # type: ignore[attr-defined]
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

    busy_wb = _workbench_busy()
    with st.expander("重新跑完整流水线（新 run）", expanded=False):
        st.markdown(
            "使用本条目录中的 **`script.md`** 从头跑：导入 → 分镜规划 → 动画工程 → 渲染 → 配音 → 成片。"
            "会**新建**一条带时间戳的 run 目录，不会改写当前这条记录。"
        )
        script_md = selected / "script.md"
        if not script_md.exists():
            st.warning("该目录下没有 `script.md`，无法从此记录发起全新流水线。")
        else:
            fr1, fr2 = st.columns(2)
            with fr1:
                fullredo_dur = st.number_input(
                    "目标时长（秒）",
                    min_value=15,
                    max_value=180,
                    value=_default_duration_for_run(selected),
                    step=5,
                    key=f"fullredo_dur_{selected.name}",
                    disabled=busy_wb,
                )
            with fr2:
                fullredo_audio = st.toggle(
                    "自动配音（无口播 PIP 时）",
                    value=True,
                    key=f"fullredo_aud_{selected.name}",
                    disabled=busy_wb,
                )
            prev_pip = selected / "uploaded_talking_head.mp4"
            fullredo_reuse_pip = st.checkbox(
                "沿用本条已保存的口播 PIP",
                value=prev_pip.exists(),
                key=f"fullredo_reuse_pip_{selected.name}",
                disabled=busy_wb or not prev_pip.exists(),
                help="复制当前 run 下的 uploaded_talking_head.mp4 到新任务（若存在）。",
            )
            fullredo_head = st.file_uploader(
                "或上传新的口播 PIP（mp4，优先于沿用）",
                type=["mp4"],
                key=f"fullredo_head_{selected.name}",
                disabled=busy_wb,
            )
            if st.button(
                "启动全新流水线（后台）",
                type="primary",
                key=f"fullredo_go_{selected.name}",
                disabled=busy_wb or not script_md.exists(),
                help="script 会先写入 inbox 再启动，与「工作台」逻辑一致。",
            ):
                settings.inbox_dir.mkdir(parents=True, exist_ok=True)
                stem = f"rerun_{uuid4().hex[:12]}"
                new_script = settings.inbox_dir / f"{stem}.md"
                shutil.copyfile(script_md, new_script)
                run = VideoRun(new_script)
                head_p: Path | None = None
                if fullredo_head is not None:
                    head_p = run.run_dir / "uploaded_talking_head.mp4"
                    head_p.write_bytes(fullredo_head.getvalue())
                elif fullredo_reuse_pip and prev_pip.exists():
                    head_p = run.run_dir / "uploaded_talking_head.mp4"
                    shutil.copyfile(prev_pip, head_p)
                st.session_state.web_poll_run_dir = str(run.run_dir)
                _start_background_run(
                    run,
                    int(fullredo_dur),
                    with_narration=fullredo_audio,
                    head=head_p,
                    resume=False,
                )
                st.success(f"新任务已启动：`{run.run_dir.name}`。在「工作台」可查看进度。")
                st.rerun()

    if not manifest.success:
        st.info(
            "该 run 未完成。可从已成功且磁盘产物齐全的阶段之后**续跑**；新阶段会追加写入 "
            "`manifest.json`（保留历史记录）。续跑开始后也可在「工作台」查看同一目录进度。"
        )
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

    _render_diagnostic_logs(selected)

    n_ok = sum(1 for name, _ in PIPELINE_STEPS if (_stage_row_for(name, manifest) or {}).get("success"))
    st.progress(min(n_ok / len(PIPELINE_STEPS), 1.0), text=f"阶段 {n_ok}/{len(PIPELINE_STEPS)}")
    if manifest.finished_at is None:
        rbpc = _read_build_progress_caption(selected)
        if rbpc:
            st.caption(rbpc)

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
            "后期微调",
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
        frames = []
        if frames_dir.exists():
            frames = sorted(
                list(frames_dir.glob("*.jpg"))
                + list(frames_dir.glob("*.jpeg"))
                + list(frames_dir.glob("*.png"))
            )
        if frames:
            fc = st.columns(min(5, len(frames)))
            for i, f in enumerate(frames):
                fc[i % len(fc)].image(str(f), caption=f.stem, use_container_width=True)
        else:
            st.info("No frames extracted yet (frames stage not finished).")

    with tabs[4]:
        _render_post_tune(selected)

    with tabs[5]:
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

    with tabs[6]:
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
