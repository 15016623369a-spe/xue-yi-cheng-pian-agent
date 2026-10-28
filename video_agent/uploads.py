"""Validated, project-local uploads. Originals are retained beside normalized media."""
from __future__ import annotations

import math
import os
import shutil
import time
import uuid
from pathlib import Path

from .engine import ROOT
from .util import file_hash, probe, read_json, run_command
from .workspace import Conflict

MAX_UPLOAD = 100 * 1024 * 1024
MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm"}
SOUND_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}


def normalize(source: Path, filename: str, category: str, runtime: dict):
    filename = filename.replace("\\", "/").split("/")[-1]
    if not filename or len(filename) > 180 or any(ord(c) < 32 for c in filename):
        raise ValueError("素材文件名不正确")
    ext = Path(filename).suffix.lower()
    allowed = SOUND_EXTENSIONS if category == "sound" else MEDIA_EXTENSIONS if category == "media" else set()
    if ext not in allowed:
        raise ValueError("请选择支持的图片、视频或音效文件")
    if not 0 < source.stat().st_size <= MAX_UPLOAD:
        raise ValueError("单个素材需在 100 MB 以内")
    try:
        info = probe(runtime["ffprobe"], source)
    except Exception as exc:
        raise ValueError("无法读取素材，请检查文件是否损坏或格式是否正确") from exc
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    image = category == "media" and ext in {".png", ".jpg", ".jpeg", ".webp"}
    kind = "sound" if category == "sound" else "image" if image else "video"
    seconds = float(info.get("format", {}).get("duration", 0) or 0)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("素材时长不正确")
    if kind == "sound" and (not audio or video and not video.get("disposition", {}).get("attached_pic") or not 0 < seconds <= 30):
        raise ValueError("音效需为不超过 30 秒的音频文件")
    if kind != "sound" and (not video or video.get("width", 0) * video.get("height", 0) > 32_000_000):
        raise ValueError("需要有效的图片或视频，画面不可超过 3200 万像素")
    if kind == "video" and not 0 < seconds <= 120:
        raise ValueError("单段视频或动图请控制在 120 秒以内")
    key = ("sfx_" if kind == "sound" else "meme_") + uuid.uuid4().hex[:12]
    stage = source.parent / key
    stage.mkdir()
    normalized = stage / ("media.wav" if kind == "sound" else "media.png" if image else "media.mp4")
    args = [runtime["ffmpeg"], "-v", "error", "-y", "-i", str(source), "-map_metadata", "-1"]
    if kind == "sound":
        args += ["-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le"]
    elif image:
        args += ["-map", "0:v:0", "-frames:v", "1", "-vf", "scale=1920:1920:force_original_aspect_ratio=decrease", "-update", "1"]
    else:
        args += ["-map", "0:v:0", "-an", "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    try:
        run_command(args + [str(normalized)], stage / "conversion.log")
        result_info = probe(runtime["ffprobe"], normalized)
        shutil.copy2(source, stage / ("original" + ext))
        metadata = {"name": filename, "kind": kind, "seconds": seconds if not image else None,
                    "sha256": file_hash(normalized), "original_sha256": file_hash(source), "uploaded": time.time()}
        if kind != "sound":
            view = next(s for s in result_info["streams"] if s["codec_type"] == "video")
            metadata.update(width=view["width"], height=view["height"])
        return stage, normalized, metadata, key
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        if isinstance(exc,ValueError):raise
        raise ValueError("素材转换失败，请检查文件内容或换一个文件") from exc


def register(ws, pid, revision, source: Path, filename: str, category: str):
    if ws.get(pid)["revision"] != revision:
        raise Conflict("项目已有更新，请刷新后重新上传；没有覆盖新版本。")
    runtime = read_json((ws.root / "config/local.json") if (ws.root / "config/local.json").exists() else ROOT / "config/local.json")
    stage, normalized, metadata, key = normalize(source, filename, category, runtime)
    try:
        with ws.locked_project(pid):
            doc = ws.get(pid)
            if doc["revision"] != revision:
                raise Conflict("处理素材期间项目已有更新，请刷新后重试；没有覆盖新版本。")
            destination = ws.path(pid) / "assets" / "uploads" / key
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, destination)
            try:
                doc["config"].setdefault("assets", {})[key] = str((destination / normalized.name).relative_to(ws.path(pid)))
                doc["config"].setdefault("asset_metadata", {})[key] = metadata
                if doc.get("storyboard"):
                    doc["config"] = ws.compile(doc)
                doc.update(revision=revision + 1, updated=time.time(), approved=False, review={})
                ws._write(ws.path(pid), doc)
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                raise
        return {"project": doc, "asset": key, "metadata": metadata}
    except (Conflict, ValueError):
        raise
    except Exception as exc:
        raise ValueError("素材转换失败，请换一个文件后重试") from exc
    finally:
        shutil.rmtree(stage, ignore_errors=True)
