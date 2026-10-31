"""Reversible local watermark processing for registered visual assets."""
from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .util import digest, file_hash, probe, read_json, resolve_path, run_command, write_json
from .workspace import Conflict

VERSION = "watermark-corners-1"
MARK = re.compile(r"抖音|douyin|bilibili|哔哩哔哩|快手|kuaishou|小红书|xiaohongshu|(?:抖音号|快手号|小红书号)[:：]?|@\w{3,}", re.I)


def regions(raw):
    if not isinstance(raw, list) or len(raw) > 8:
        raise ValueError("最多框选8处水印")
    result = []
    for box in raw:
        if not isinstance(box, (list, tuple)) or len(box) != 4 or any(type(v) not in {float, int} or not math.isfinite(v) for v in box):
            raise ValueError("水印区域需要有效的相对坐标")
        x, y, w, h = box
        if not (0 <= x < 1 and 0 <= y < 1 and .003 <= w <= 1 and .003 <= h <= 1 and x+w <= 1.000001 and y+h <= 1.000001) or w*h > .3:
            raise ValueError("水印区域超出画面或过大，请只框选水印")
        result.append([round(v, 6) for v in box])
    if sum(b[2]*b[3] for b in result) > .45:
        raise ValueError("所选水印区域总面积过大")
    return result


def options(data):
    if not isinstance(data, dict) or data.get("mode", "auto") not in {"auto", "manual", "restore"}:
        raise ValueError("请选择自动识别、框选清理或恢复原件")
    mode = data.get("mode", "auto")
    selected = regions(data.get("regions", []))
    if mode == "manual" and not selected:
        raise ValueError("请先框选水印区域")
    return {"mode": mode, "regions": selected if mode == "manual" else []}


def detected(items):
    """Recognize only small platform/account text at corners, not body captions."""
    out = []
    for item in items:
        if not isinstance(item, dict) or not MARK.search(str(item.get("text", ""))) or item.get("confidence", 0) < .45:
            continue
        try:
            x, y, w, h = regions([item["box"]])[0]
        except (ValueError, KeyError, TypeError):
            continue
        if not ((x+w/2 < .28 or x+w/2 > .72) and (y+h/2 < .24 or y+h/2 > .76)) or w > .4 or h > .14 or w*h > .04:
            continue
        a, b, c, d = max(.002, x-.012), max(.002, y-.009), min(.998, x+w+.012), min(.998, y+h+.009)
        candidate = [a, b, c-a, d-b]
        # Merge sampled jitter and nearby lines; avoid repeatedly blurring one mark.
        overlap = next((r for r in out if a < r[0]+r[2]+.015 and c > r[0]-.015 and b < r[1]+r[3]+.025 and d > r[1]-.025), None)
        if overlap:
            left, top = min(a, overlap[0]), min(b, overlap[1])
            right, bottom = max(c, overlap[0]+overlap[2]), max(d, overlap[1]+overlap[3])
            overlap[:] = [left, top, right-left, bottom-top]
        else:
            out.append(candidate)
    return regions(out[:8])


def inspect(ws, source, work, info, runtime):
    if sys.platform != "darwin" or not Path("/usr/bin/swiftc").is_file():
        raise ValueError("此环境未配置本机文字识别，可使用框选清理")
    worker = ws.root / "workers/watermark_ocr.swift"
    tools = ws.web / "tools"; tools.mkdir(exist_ok=True)
    binary = tools / ("watermark-ocr-"+file_hash(worker)[:16])
    with (tools / "ocr-build.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not binary.is_file():
            temporary = binary.with_suffix(".tmp")
            run_command(["/usr/bin/swiftc", "-O", "-module-cache-path", str(tools/"swift-module-cache"), str(worker), "-o", str(temporary)], work / "ocr-build.log", timeout=120)
            os.replace(temporary, binary)
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    count = min(10, max(1, math.ceil(duration/2)))
    frames = []
    for i in range(count):
        frame = work / f"frame-{i:02}.png"
        at = max(0, (duration-.08)*i/max(1, count-1))
        run_command([runtime["ffmpeg"], "-v", "error", "-y", "-ss", str(at), "-i", str(source), "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=1280:1280:force_original_aspect_ratio=decrease", "-update", "1", str(frame)], work / f"sample-{i}.log", timeout=40)
        frames.append(frame)
    result = subprocess.run([str(binary), *map(str, frames)], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise ValueError("本机水印识别未完成，可改用框选清理")
    data = json.loads(result.stdout)
    return detected([item for f in data for item in f.get("items", [])]), len(frames)


def filters(selected, width, height):
    parts = []
    for x, y, w, h in regions(selected):
        a = max(1, min(width-4, round(x*width)))
        b = max(1, min(height-4, round(y*height)))
        c = min(width-2, max(a+2, round((x+w)*width)))
        d = min(height-2, max(b+2, round((y+h)*height)))
        parts.append(f"delogo=x={a}:y={b}:w={c-a}:h={d-b}")
    return ",".join(parts)


def process(ws, source, work, selection=None, notify=lambda *_: None):
    selection = options(selection or {"mode": "auto"})
    work.mkdir(parents=True, exist_ok=True)
    runtime = read_json(ws.root / "config/local.json")
    info = probe(runtime["ffprobe"], source)
    view = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if not view or not 0 < view["width"]*view["height"] <= 32_000_000:
        raise ValueError("请选择有效的图片、动图或视频素材")
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    if not math.isfinite(duration) or not 0 <= duration <= 120:
        raise ValueError("水印处理的素材最长120秒")
    original_sha = file_hash(source)
    selected = selection["regions"]
    report = {"version": VERSION, "mode": selection["mode"], "original_sha256": original_sha, "regions": [], "status": "not_detected", "sampled_frames": 0}
    if selection["mode"] == "restore":
        return source, {**report, "status": "restored"}
    identity = digest({"source": original_sha, "options": selection, "version": VERSION,
                       "implementation": file_hash(Path(__file__)), "ocr": file_hash(ws.root/"workers/watermark_ocr.swift") if (ws.root/"workers/watermark_ocr.swift").is_file() else None,
                       "ffmpeg": file_hash(Path(runtime["ffmpeg"]))})
    cache = ws.web / "cleanup-cache" / identity
    try:
        saved = read_json(cache / "record.json")
        target = cache / saved["file"]
        if target.is_file() and file_hash(target) == saved["sha256"] and saved.get("report_sha256") == digest(saved["report"]):
            return target, {**saved["report"], "cache_hit": True}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if selection["mode"] == "auto":
        notify("正在本机抽帧识别平台角标和账号水印")
        try:
            selected, report["sampled_frames"] = inspect(ws, source, work, info, runtime)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return source, {**report, "status": "needs_review", "message": str(exc)[:250]}
    if not selected:
        return source, {**report, "message": "抽样未识别到可自动处理的角落文字水印；请预览，需要时框选"}
    notify("正在清理选定角标区域，保留素材原件和原声音轨")
    image = source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    output = work / ("clean.png" if image else "clean.mov")
    vf = filters(selected, view["width"], view["height"])
    command = [runtime["ffmpeg"], "-v", "error", "-y", "-i", str(source), "-map_metadata", "-1", "-map", "0:v:0"]
    if image:
        command += ["-vf", vf, "-frames:v", "1", "-update", "1"]
    else:
        command += ["-map", "0:a:0?", "-vf", vf+",scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"]
    run_command(command+[str(output)], work / "cleanup.log", timeout=240)
    run_command([runtime["ffmpeg"], "-v", "error", "-i", str(output), "-f", "null", "-"], work / "decode.log", timeout=120)
    final = probe(runtime["ffprobe"], output)
    if not image and abs(float(final["format"]["duration"])-duration) > .1:
        raise ValueError("水印处理后的时长检查未通过，原素材保持")
    report.update(status="processed", regions=selected, output_sha256=file_hash(output), message="已处理选定区域，请预览边缘与画面；该处理不等于无损重建")
    cache.mkdir(parents=True, exist_ok=True)
    temp = cache / (output.name+".tmp"); shutil.copy2(output, temp); os.replace(temp, cache/output.name)
    write_json(cache / "record.json", {"file": output.name, "sha256": file_hash(output), "report": report, "report_sha256": digest(report)})
    return cache/output.name, report


def job_options(doc, data):
    if not isinstance(data, dict) or data.get("asset") not in doc["config"].get("assets", {}):
        raise ValueError("请选择本项目已登记的素材")
    return {"asset": data["asset"], **options(data)}


def apply(ws, doc, data, work, notify=lambda *_: None):
    selection = job_options(doc, data)
    key = selection["asset"]; base = ws.path(doc["id"])
    meta = copy.deepcopy(doc["config"].get("asset_metadata", {}).get(key, {}))
    original = meta.get("cleanup_original_asset") or key
    source = resolve_path(base, doc["config"]["assets"][original])
    # Uploaded assets retain their original beside the normalized derivative.
    if not meta.get('cleanup_original_asset'):
        saved=next((p for p in source.parent.glob('original.*') if p.is_file()),None)
        if saved:source=saved
    result, report = process(ws, source, work, selection, notify)
    if report["status"] in {"needs_review", "not_detected"}:
        return {"applied": False, "revision": doc["revision"], "report": report}
    from .uploads import normalize
    opening = doc["config"].get("opening") or doc.get("opening_selection")
    is_opening = bool(opening and opening["asset"] == key)
    with ws.locked_project(doc["id"]):
        current = ws.get(doc["id"])
        if current["revision"] != doc["revision"]:
            raise Conflict("清理期间项目已修改，处理结果已保留，未覆盖当前素材")
        destination = base / "assets/cleaned" / file_hash(result); destination.mkdir(parents=True, exist_ok=True)
        if is_opening:
            media = destination / ("opening"+result.suffix.lower()); shutil.copy2(result, media)
            preview = destination / "preview.mp4"
            runtime = read_json(ws.root/"config/local.json")
            run_command([runtime["ffmpeg"], "-v", "error", "-y", "-i", str(media), "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(preview)], work/"preview.log")
        else:
            stage, normalized, _, _ = normalize(result, "cleaned"+result.suffix.lower(), "media", read_json(ws.root/"config/local.json"))
            media = destination/normalized.name; shutil.copy2(normalized, media); shutil.rmtree(stage, ignore_errors=True)
        original_key = meta.get("cleanup_original_asset") or ("original_"+digest(key)[:16])
        if not meta.get('cleanup_original_asset'):
            raw_copy=destination/('original'+source.suffix.lower())
            if raw_copy.resolve()!=source.resolve():shutil.copy2(source,raw_copy)
            original_location=str(raw_copy.relative_to(base))
        else:original_location=doc["config"]["assets"][original]
        current["config"]["assets"].setdefault(original_key,original_location)
        current["config"]["assets"][key] = str(media.relative_to(base))
        current["config"].setdefault("asset_metadata", {})[key] = {**meta, "sha256": file_hash(media), "cleanup_original_asset": original_key, "cleanup": report}
        if is_opening:
            active = "opening" if current["config"].get("opening") else None
            updated = copy.deepcopy(opening); preview_key = key+"_clean_preview"
            current["config"]["assets"][preview_key] = str(preview.relative_to(base)); updated["preview_asset"] = preview_key
            updated["cleanup"] = report
            current["opening_selection"] = updated
            if active: current["config"]["opening"] = updated
        if current.get("storyboard"): current["config"] = ws.compile(current)
        current.update(revision=current["revision"]+1, updated=time.time(), approved=False, review={})
        ws._write(base, current)
    return {"applied": True, "revision": current["revision"], "asset": key, "report": report}
