"""Post-production helpers for run-level tuning.

This module intentionally works on an existing run directory. It lets the UI
adjust timing/narration details, rebuild the narration/final video without a
full LLM run, and optionally replace a segment with a generated or uploaded
keyframe image.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from . import composite as composite_stage
from . import render as render_stage
from . import tts as tts_stage
from .config import settings
from .schemas import ScriptPlan, Segment

console = Console()

DEFAULT_SEGMENT_TUNING: dict[str, float] = {
    "audio_shift_ms": 0.0,
    "guard_gap_s": 0.08,
    "fade_out_ms": 120.0,
    "volume_pct": 100.0,
}

# Heuristics for issue-driven auto tuning (post-production timeline + TTS window).
AUTO_SEGMENT_GAP_S = 0.06
AUTO_MIN_SEGMENT_WINDOW_S = 0.35
AUTO_TTS_END_MARGIN_S = 0.12
AUTO_MAX_SHIFT_STEP_MS = 480.0


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg not found on PATH.")
    return path


def _ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise RuntimeError("ffprobe not found on PATH.")
    return path


def media_duration(path: Path) -> float | None:
    if not path.exists():
        return None
    proc = subprocess.run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def _coerce_segment_tuning(raw: dict[str, Any] | None) -> dict[str, float]:
    raw = raw or {}
    out = dict(DEFAULT_SEGMENT_TUNING)
    for key in out:
        try:
            out[key] = float(raw.get(key, out[key]))
        except (TypeError, ValueError):
            pass
    out["guard_gap_s"] = max(0.0, out["guard_gap_s"])
    out["fade_out_ms"] = max(0.0, out["fade_out_ms"])
    out["volume_pct"] = max(0.0, out["volume_pct"])
    return out


def load_tuning(run_dir: Path, plan: ScriptPlan) -> dict[int, dict[str, float]]:
    path = run_dir / "post_tune.json"
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
    raw_segments = raw.get("segments", raw)
    if not isinstance(raw_segments, dict):
        raw_segments = {}
    return {
        int(seg.index): _coerce_segment_tuning(raw_segments.get(str(seg.index)))
        for seg in plan.segments
    }


def save_tuning(run_dir: Path, tuning: dict[int, dict[str, float]]) -> Path:
    path = run_dir / "post_tune.json"
    payload = {
        "segments": {
            str(idx): _coerce_segment_tuning(opts)
            for idx, opts in sorted(tuning.items())
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def tts_diagnostics(
    run_dir: Path,
    plan: ScriptPlan,
    segment_options: dict[int, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    segment_options = segment_options or {}
    animation_s = media_duration(run_dir / "animation.mp4")
    sorted_segments = sorted(plan.segments, key=lambda s: (float(s.start), int(s.index)))
    previous_seg = None
    previous_audio_end: float | None = None
    for pos, seg in enumerate(sorted_segments):
        next_seg = sorted_segments[pos + 1] if pos + 1 < len(sorted_segments) else None
        path = run_dir / "tts_segments" / f"seg_{seg.index:02d}.mp3"
        audio_s = media_duration(path)
        window_s = max(0.0, float(seg.end - seg.start))
        opts = _coerce_segment_tuning(
            segment_options.get(int(seg.index)) or segment_options.get(str(seg.index))  # type: ignore[arg-type]
        )
        shift_s = opts["audio_shift_ms"] / 1000.0
        guard_gap_s = opts["guard_gap_s"]
        trim_s = max(0.12, window_s - guard_gap_s)
        effective_audio_s = min(audio_s if audio_s is not None else trim_s, trim_s)
        audio_start_s = max(0.0, float(seg.start) + shift_s)
        audio_end_s = audio_start_s + effective_audio_s
        overflow_s = max(0.0, (audio_s or 0.0) - trim_s)

        issues: list[str] = []
        issue_codes: list[str] = []
        if previous_seg is not None:
            visual_gap_s = float(seg.start - previous_seg.end)
            if visual_gap_s < -0.05:
                issues.append(f"画面时间与上一段重叠 {abs(visual_gap_s):.2f}s")
                issue_codes.append("visual_overlap_prev")
            elif visual_gap_s > 0.35:
                issues.append(f"画面与上一段有 {visual_gap_s:.2f}s 空隙")
                issue_codes.append("visual_gap_prev")
        if window_s <= 0.2:
            issues.append("分镜窗口过短，旁白和转场都很容易被截断")
            issue_codes.append("window_too_short")
        if audio_s is None:
            issues.append("缺少该段 TTS 文件，重合成时会重新生成或失败")
            issue_codes.append("tts_missing")
        elif overflow_s > 0.05:
            issues.append(f"TTS 比可用窗口长 {overflow_s:.2f}s，会被裁切")
            issue_codes.append("tts_overflow")
        if shift_s < 0 and float(seg.start) + shift_s < 0:
            issues.append("旁白前移越过 0s，实际会从 0s 开始")
            issue_codes.append("shift_before_zero")
        if audio_start_s > float(seg.start) + 0.35:
            issues.append(f"旁白比画面晚 {audio_start_s - float(seg.start):.2f}s")
            issue_codes.append("audio_late_vs_visual")
        if audio_start_s < float(seg.start) - 0.35:
            issues.append(f"旁白比画面早 {float(seg.start) - audio_start_s:.2f}s")
            issue_codes.append("audio_early_vs_visual")
        if audio_end_s > float(seg.end) + 0.05:
            issues.append(f"旁白尾部超出本段 {audio_end_s - float(seg.end):.2f}s")
            issue_codes.append("audio_end_past_segment")
        if next_seg is not None and audio_end_s > float(next_seg.start) - 0.02:
            issues.append("旁白尾部可能压到下一段")
            issue_codes.append("audio_bleeds_next")
        if previous_audio_end is not None and audio_start_s < previous_audio_end - 0.02:
            issues.append("本段旁白与上一段旁白重叠")
            issue_codes.append("audio_overlap_prev")
        if animation_s is not None and float(seg.end) > animation_s + 0.2:
            issues.append("分镜结束时间超过当前 animation.mp4 时长")
            issue_codes.append("end_past_animation")

        rows.append(
            {
                "idx": seg.index,
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "window_s": round(window_s, 3),
                "tts_s": round(audio_s, 3) if audio_s is not None else None,
                "shift_ms": round(opts["audio_shift_ms"], 1),
                "clip_s": round(trim_s, 3),
                "audio_start": round(audio_start_s, 3),
                "audio_end": round(audio_end_s, 3),
                "overflow_s": round(overflow_s, 3),
                "issue_count": len(issues),
                "issues": "；".join(issues) if issues else "OK",
                "issue_codes": ",".join(dict.fromkeys(issue_codes)),
                "previous_audio_end": round(previous_audio_end, 3)
                if previous_audio_end is not None
                else None,
                "subtitle": seg.subtitle,
                "narration": seg.narration,
            }
        )
        previous_seg = seg
        previous_audio_end = audio_end_s
    return rows


def _segments_by_index(plan: ScriptPlan) -> dict[int, Segment]:
    return {int(s.index): s.model_copy(deep=True) for s in plan.segments}


def _ordered_segments(by_index: dict[int, Segment]) -> list[Segment]:
    return sorted(by_index.values(), key=lambda s: (float(s.start), int(s.index)))


def _rebuild_plan_meta(plan: ScriptPlan, by_index: dict[int, Segment]) -> ScriptPlan:
    ordered = _ordered_segments(by_index)
    last_end = max(float(s.end) for s in ordered)
    return ScriptPlan(
        title=plan.title,
        style=plan.style,
        total_duration=last_end,
        aspect=plan.aspect,
        segments=ordered,
    )


def _shift_segments_after(by_index: dict[int, Segment], after_index: int, delta: float) -> None:
    if abs(delta) < 1e-6:
        return
    ordered = _ordered_segments(by_index)
    pos = next(i for i, s in enumerate(ordered) if s.index == after_index)
    for j in range(pos + 1, len(ordered)):
        s = by_index[ordered[j].index]
        by_index[s.index] = s.model_copy(
            update={"start": float(s.start) + delta, "end": float(s.end) + delta}
        )


def _ensure_min_gap_after(by_index: dict[int, Segment], anchor_index: int) -> None:
    ordered = _ordered_segments(by_index)
    pos = next(i for i, s in enumerate(ordered) if s.index == anchor_index)
    if pos + 1 >= len(ordered):
        return
    cur = by_index[ordered[pos].index]
    nxt = by_index[ordered[pos + 1].index]
    need_start = float(cur.end) + AUTO_SEGMENT_GAP_S
    delta = need_start - float(nxt.start)
    if delta > 1e-3:
        _shift_segments_after(by_index, cur.index, delta)


def _extend_segment_end(
    by_index: dict[int, Segment],
    seg_index: int,
    target_end: float,
    animation_s: float | None,
) -> None:
    s = by_index[seg_index]
    ne = max(float(s.end), float(target_end))
    if animation_s is not None:
        ne = min(ne, animation_s - 0.06)
    ne = max(ne, float(s.start) + AUTO_MIN_SEGMENT_WINDOW_S)
    if abs(ne - float(s.end)) < 1e-6:
        return
    by_index[seg_index] = s.model_copy(update={"end": ne})
    _ensure_min_gap_after(by_index, seg_index)


def _normalize_timeline_start(by_index: dict[int, Segment]) -> bool:
    ordered = _ordered_segments(by_index)
    first = by_index[ordered[0].index]
    if float(first.start) <= 0.45:
        return False
    delta = 0.1 - float(first.start)
    for idx in list(by_index.keys()):
        s = by_index[idx]
        dur = float(s.end) - float(s.start)
        ns = max(0.0, float(s.start) + delta)
        by_index[idx] = s.model_copy(update={"start": ns, "end": ns + dur})
    return True


def _row_issue_codes(row: dict[str, Any]) -> set[str]:
    raw = (row.get("issue_codes") or "").strip()
    if not raw:
        return set()
    return {c.strip() for c in raw.split(",") if c.strip()}


def auto_optimize_from_issues(
    run_dir: Path,
    plan: ScriptPlan,
    segment_options: dict[int, dict[str, float]] | None = None,
    *,
    max_rounds: int = 8,
) -> tuple[ScriptPlan, dict[int, dict[str, float]], list[str]]:
    """Adjust segment timeline + :class:`post_tune` fields using ``tts_diagnostics`` issue codes.

    This targets *post-production* mismatches (window vs TTS, drift, overlaps). It does not rewrite
    narration text; ``tts_missing`` is reported for the user to re-synthesize.
    """
    log: list[str] = []
    by_index = _segments_by_index(plan)
    segment_options = segment_options or {}
    tuning: dict[int, dict[str, float]] = {}
    for s in plan.segments:
        idx = int(s.index)
        tuning[idx] = _coerce_segment_tuning(
            segment_options.get(idx) or segment_options.get(str(idx))  # type: ignore[arg-type]
        )

    animation_s = media_duration(run_dir / "animation.mp4")

    for round_i in range(max_rounds):
        if _normalize_timeline_start(by_index):
            log.append("已把首段开始时间拉回 ~0.1s 以满足时间轴校验。")

        try:
            cur_plan = _rebuild_plan_meta(plan, by_index)
        except Exception as exc:
            log.append(f"第 {round_i + 1} 轮：无法生成合法分镜：{exc}")
            break

        diag = tts_diagnostics(run_dir, cur_plan, tuning)
        if not any(int(r.get("issue_count") or 0) > 0 for r in diag):
            log.append(f"第 {round_i + 1} 轮：诊断已全部通过。")
            return cur_plan, tuning, log

        row_by_idx = {int(r["idx"]): r for r in diag}
        made_change = False

        for seg in _ordered_segments(by_index):
            idx = int(seg.index)
            row = row_by_idx.get(idx)
            if not row:
                continue
            codes = _row_issue_codes(row)
            if not codes:
                continue

            seg_live = by_index[idx]
            t = tuning[idx]
            ordered = _ordered_segments(by_index)
            pos = next(i for i, s in enumerate(ordered) if s.index == idx)
            prev_seg = ordered[pos - 1] if pos > 0 else None

            if "tts_missing" in codes:
                log.append(
                    f"scene_{idx:02d}：缺少 TTS 文件，无法根据时长自动拉伸分镜；请用「重新生成 TTS+成片」。"
                )

            if "shift_before_zero" in codes:
                floor = -float(seg_live.start) * 1000 + 25
                if t["audio_shift_ms"] < floor:
                    t["audio_shift_ms"] = floor
                    made_change = True
                    log.append(f"scene_{idx:02d}：校正旁白偏移，避免早于 0s（audio_shift_ms={t['audio_shift_ms']:.0f}）。")

            if "audio_late_vs_visual" in codes:
                late = float(row["audio_start"]) - float(seg_live.start)
                step = min(late * 1000 * 0.85, AUTO_MAX_SHIFT_STEP_MS)
                if step > 8:
                    t["audio_shift_ms"] -= step
                    made_change = True
                    log.append(f"scene_{idx:02d}：旁白偏晚，前移约 {step:.0f} ms。")

            if "audio_early_vs_visual" in codes:
                early = float(seg_live.start) - float(row["audio_start"])
                step = min(early * 1000 * 0.85, AUTO_MAX_SHIFT_STEP_MS)
                if step > 8:
                    t["audio_shift_ms"] += step
                    made_change = True
                    log.append(f"scene_{idx:02d}：旁白偏早，后移约 {step:.0f} ms。")

            if "audio_overlap_prev" in codes and row.get("previous_audio_end") is not None:
                floor_ms = (float(row["previous_audio_end"]) + 0.04 - float(seg_live.start)) * 1000
                if t["audio_shift_ms"] < floor_ms - 5:
                    t["audio_shift_ms"] = floor_ms
                    made_change = True
                    log.append(f"scene_{idx:02d}：提升旁白起点，消除与上一段重叠（audio_shift_ms={t['audio_shift_ms']:.0f}）。")

            if "visual_overlap_prev" in codes or "visual_gap_prev" in codes:
                if prev_seg is not None:
                    anchor_end = float(by_index[prev_seg.index].end)
                    new_start = anchor_end + AUTO_SEGMENT_GAP_S
                    dur = max(
                        AUTO_MIN_SEGMENT_WINDOW_S,
                        float(seg_live.end) - float(seg_live.start),
                    )
                    by_index[idx] = seg_live.model_copy(update={"start": new_start, "end": new_start + dur})
                    made_change = True
                    log.append(f"scene_{idx:02d}：校正与上一段的画面间距（start={new_start:.2f}s）。")
                    _ensure_min_gap_after(by_index, idx)
                    seg_live = by_index[idx]

            if "window_too_short" in codes:
                win = float(seg_live.end) - float(seg_live.start)
                if win < AUTO_MIN_SEGMENT_WINDOW_S:
                    extra = AUTO_MIN_SEGMENT_WINDOW_S - win
                    _extend_segment_end(
                        by_index,
                        idx,
                        float(seg_live.end) + extra,
                        animation_s,
                    )
                    made_change = True
                    log.append(f"scene_{idx:02d}：分镜窗口过短，延长画面约 {extra:.2f}s。")
                    seg_live = by_index[idx]

            guard = float(t["guard_gap_s"])
            if "tts_overflow" in codes and row.get("tts_s") is not None:
                need_end = float(seg_live.start) + float(row["tts_s"]) + guard + AUTO_TTS_END_MARGIN_S
                if need_end > float(seg_live.end) + 1e-3:
                    _extend_segment_end(by_index, idx, need_end, animation_s)
                    made_change = True
                    log.append(
                        f"scene_{idx:02d}：原 TTS {float(row['tts_s']):.2f}s，延长画面结束以容纳旁白。"
                    )
                    seg_live = by_index[idx]

            if "audio_end_past_segment" in codes:
                need_end = float(row["audio_end"]) + AUTO_TTS_END_MARGIN_S
                if need_end > float(seg_live.end) + 1e-3:
                    _extend_segment_end(by_index, idx, need_end, animation_s)
                    made_change = True
                    log.append(f"scene_{idx:02d}：对齐画面结束时间与旁白尾部。")
                    seg_live = by_index[idx]

            if "audio_bleeds_next" in codes and row.get("tts_s") is not None:
                need_end = float(seg_live.start) + float(row["tts_s"]) + guard + AUTO_TTS_END_MARGIN_S
                if need_end > float(seg_live.end) + 1e-3:
                    _extend_segment_end(by_index, idx, need_end, animation_s)
                    made_change = True
                    log.append(f"scene_{idx:02d}：延长画面，减少旁白压入下一段的风险。")
                    seg_live = by_index[idx]

            if "end_past_animation" in codes and animation_s is not None:
                cap = animation_s - 0.08
                if float(seg_live.end) > cap:
                    s2 = by_index[idx]
                    new_end = max(float(s2.start) + AUTO_MIN_SEGMENT_WINDOW_S, cap)
                    by_index[idx] = s2.model_copy(update={"end": new_end})
                    made_change = True
                    log.append(
                        f"scene_{idx:02d}：将分镜结束裁到 animation.mp4 时长内（完整对齐请重渲染动画）。"
                    )
                    _ensure_min_gap_after(by_index, idx)

        if not made_change:
            tightened = False
            for r in diag:
                codes = _row_issue_codes(r)
                if not codes & {"tts_overflow", "audio_bleeds_next", "audio_end_past_segment"}:
                    continue
                idx = int(r["idx"])
                t = tuning[idx]
                if t["guard_gap_s"] > 0.035:
                    t["guard_gap_s"] = max(0.02, round(t["guard_gap_s"] - 0.04, 3))
                    tightened = True
                    log.append(
                        f"scene_{idx:02d}：将段尾留白调至 {t['guard_gap_s']:.2f}s，缓解旁白裁切。"
                    )
            if tightened:
                made_change = True
                continue

            log.append(
                f"第 {round_i + 1} 轮：没有可自动应用的修改（可能仅剩缺 TTS、animation 时长不足、或需改文案/重渲染）。"
            )
            break

    try:
        final_plan = _rebuild_plan_meta(plan, by_index)
    except Exception as exc:
        log.append(f"生成最终分镜失败：{exc}")
        return plan, {k: _coerce_segment_tuning(v) for k, v in tuning.items()}, log
    for idx in tuning:
        tuning[idx] = _coerce_segment_tuning(tuning[idx])
    return final_plan, tuning, log


def write_plan_with_backup(plan: ScriptPlan, path: Path) -> Path:
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return path


def patch_project_plan(project_dir: Path, plan: ScriptPlan) -> None:
    """Patch timings/subtitles in an existing project without regenerating scenes."""
    index = project_dir / "index.html"
    if not index.exists():
        return
    html = index.read_text(encoding="utf-8")
    payload = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    html2, n = re.subn(
        r"segments:\s*.*?\.segments,\n\s*ready:",
        f"segments: {payload}.segments,\n      ready:",
        html,
        count=1,
        flags=re.S,
    )
    if n == 0:
        raise RuntimeError("Could not patch window.__VIDFORGE__.segments in project/index.html")

    for seg in plan.segments:
        pattern = (
            rf'(<section\b[^>]*class="[^"]*\bscene-{seg.index}\b[^"]*"[^>]*'
            rf'data-start=")[^"]*(" data-end=")[^"]*(")'
        )
        html2 = re.sub(
            pattern,
            rf"\g<1>{seg.start}\g<2>{seg.end}\g<3>",
            html2,
            count=1,
        )
    index.write_text(html2, encoding="utf-8")


def extract_frames(video_path: Path, plan: ScriptPlan, frames_dir: Path) -> Path:
    frames_dir.mkdir(parents=True, exist_ok=True)
    dur = media_duration(video_path)
    for seg in plan.segments:
        t = (seg.start + seg.end) / 2
        if dur is not None:
            # final_tuned uses -shortest vs animation; timeline can extend past video (tune without re-render).
            t = max(0.0, min(float(t), max(0.0, dur - 0.05)))
        out = frames_dir / f"scene_{seg.index:02d}.jpg"
        subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-loglevel",
                "error",
                "-ss",
                str(t),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(out),
            ],
            check=True,
        )
    return frames_dir


def frame_override_files(run_dir: Path) -> dict[int, Path]:
    overrides = run_dir / "frame_overrides"
    found: dict[int, Path] = {}
    if not overrides.exists():
        return found
    for f in sorted(overrides.glob("scene_*.*")):
        m = re.match(r"scene_(\d+)\.", f.name)
        if m:
            found[int(m.group(1))] = f
    return found


def apply_frame_overrides(
    animation_mp4: Path,
    plan: ScriptPlan,
    overrides: dict[int, Path],
    output_path: Path,
) -> Path:
    selected = [seg for seg in plan.segments if seg.index in overrides]
    if not selected:
        return animation_mp4

    cmd: list[str] = [_ffmpeg(), "-y", "-i", str(animation_mp4)]
    for seg in selected:
        cmd += ["-loop", "1", "-i", str(overrides[seg.index])]

    filters: list[str] = []
    base = "[0:v]"
    for i, seg in enumerate(selected, start=1):
        ov = f"[ov{i}]"
        out = f"[v{i}]"
        filters.append(
            f"[{i}:v]scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase,"
            f"crop={settings.width}:{settings.height},setsar=1{ov}"
        )
        filters.append(
            f"{base}{ov}overlay=0:0:enable='between(t,{seg.start:.3f},{seg.end:.3f})'{out}"
        )
        base = out

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        base,
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return output_path


def rebuild_tuned_outputs(
    run_dir: Path,
    plan: ScriptPlan,
    *,
    render_animation: bool = False,
    synth_tts: bool = False,
    apply_overrides: bool = True,
    segment_options: dict[int, dict[str, float]] | None = None,
) -> dict[str, Path]:
    project_dir = run_dir / "project"
    patch_project_plan(project_dir, plan)

    animation_base = run_dir / "animation.mp4"
    if render_animation:
        animation_base = run_dir / "animation_tuned.mp4"
        job = render_stage.render(project_dir, animation_base, plan)
        if not job.success:
            raise RuntimeError(f"render failed: {job.log[-2000:]}")

    animation_for_final = animation_base
    overrides = frame_override_files(run_dir) if apply_overrides else {}
    if overrides:
        animation_for_final = run_dir / "animation_overrides.mp4"
        apply_frame_overrides(animation_base, plan, overrides, animation_for_final)

    tts_dir = run_dir / "tts_segments"
    if synth_tts:
        files = tts_stage.synth_segments(plan, out_dir=tts_dir)
    else:
        files = [tts_dir / f"seg_{seg.index:02d}.mp3" for seg in plan.segments]
        if not all(f.exists() for f in files):
            files = tts_stage.synth_segments(plan, out_dir=tts_dir)

    narration = run_dir / "narration_tuned.mp3"
    tts_stage.assemble_track(plan, files, narration, segment_options=segment_options)

    final = run_dir / "final_tuned.mp4"
    composite_stage.composite_no_pip(animation_for_final, final, narration_mp3=narration)
    frames = extract_frames(final, plan, run_dir / "frames_tuned")
    return {
        "animation": animation_for_final,
        "narration": narration,
        "final": final,
        "frames": frames,
    }


def save_frame_override(run_dir: Path, scene_index: int, data: bytes, suffix: str) -> Path:
    suffix = suffix.lower().lstrip(".") or "png"
    if suffix == "jpeg":
        suffix = "jpg"
    out_dir = run_dir / "frame_overrides"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"scene_{scene_index:02d}.*"):
        old.unlink()
    out = out_dir / f"scene_{scene_index:02d}.{suffix}"
    out.write_bytes(data)
    preview_dir = run_dir / "frames"
    preview_dir.mkdir(exist_ok=True)
    for old in preview_dir.glob(f"scene_{scene_index:02d}.*"):
        old.unlink()
    (preview_dir / f"scene_{scene_index:02d}.{suffix}").write_bytes(data)
    return out


def _write_image_payload(payload: dict[str, Any], output_path: Path) -> Path:
    image = None
    if isinstance(payload.get("data"), list) and payload["data"]:
        image = payload["data"][0]
    elif payload.get("image") or payload.get("url") or payload.get("b64_json"):
        image = payload
    if not isinstance(image, dict):
        raise RuntimeError("Image API response did not contain an image payload")

    b64 = image.get("b64_json") or image.get("base64") or image.get("image")
    url = image.get("url")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if b64:
        output_path.write_bytes(base64.b64decode(str(b64).split(",", 1)[-1]))
        return output_path
    if url:
        with urllib.request.urlopen(str(url), timeout=120) as resp:
            output_path.write_bytes(resp.read())
        return output_path
    raise RuntimeError("Image API response had neither base64 nor url")


def generate_keyframe_image(
    *,
    provider: str,
    prompt: str,
    output_path: Path,
    model: str | None = None,
) -> Path:
    provider = provider.lower()
    model = model or settings.image_model
    if provider == "openai":
        if not settings.image_api_key:
            raise RuntimeError("OPENAI_API_KEY or VIDFORGE_IMAGE_API_KEY missing")
        from openai import OpenAI

        client = OpenAI(api_key=settings.image_api_key)
        rsp = client.images.generate(model=model, prompt=prompt, size=settings.image_size)
        data = rsp.model_dump(mode="json")
        return _write_image_payload(data, output_path)

    if provider == "http-json":
        if not settings.image_api_url:
            raise RuntimeError("VIDFORGE_IMAGE_API_URL missing")
        body = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "size": settings.image_size,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if settings.image_api_key:
            headers["Authorization"] = f"Bearer {settings.image_api_key}"
        req = urllib.request.Request(settings.image_api_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return _write_image_payload(payload, output_path)

    raise ValueError(f"Unsupported image provider: {provider}")
