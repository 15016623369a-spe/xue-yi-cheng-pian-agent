"""Project-local cover inputs and verified, immutable PNG deliveries."""
from __future__ import annotations

import copy
import fcntl
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .engine import ROOT
from .util import digest, file_hash, probe, read_json, run_command, write_json
from .workspace import Conflict, LOCK

MAX_COVER_UPLOAD = 20 * 1024 * 1024
SIZES = {"16:9": (1920, 1080), "4:3": (1440, 1080), "3:4": (1080, 1440)}
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def base(ws, pid):
    ws.get(pid)  # A valid ID alone must not create a new project.
    return ws.path(pid) / "cover"


@contextmanager
def locked(ws, pid):
    with LOCK:
        directory = base(ws, pid)
        directory.mkdir(exist_ok=True)
        with (directory / "state.lock").open("a+") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                yield directory
            finally:
                fcntl.flock(guard, fcntl.LOCK_UN)


def load(ws, pid):
    path = base(ws, pid) / "cover.json"
    if path.exists():
        return read_json(path)
    return {"schema_version": 1, "revision": 0,
            "settings": {"title": ws.get(pid)["title"], "aspect": "both", "image_id": ""},
            "images": {}, "renders": []}


def runtime(ws):
    path = ws.root / "config/local.json"
    return read_json(path if path.exists() else ROOT / "config/local.json")


def check_revision(doc, revision):
    if type(revision) is not int or revision != doc["revision"]:
        raise Conflict("封面已在其他页面更新，请重新打开项目后再试；没有覆盖新版本。")


def image_path(ws, pid, image_id):
    if not isinstance(image_id, str) or not re.fullmatch(r"[a-f0-9]{64}", image_id):
        raise ValueError("请先上传并选择一张封面图片")
    if image_id not in load(ws, pid)["images"]:
        raise ValueError("这张封面图片尚未登记")
    return base(ws, pid) / "images" / image_id / "image.png"


def register(ws, pid, revision, source, filename):
    check_revision(load(ws, pid), revision)
    filename = filename.replace("\\", "/").split("/")[-1]
    ext = Path(filename).suffix.lower()
    if not filename or len(filename) > 180 or any(ord(c) < 32 for c in filename) or ext not in EXTENSIONS:
        raise ValueError("请选择 PNG、JPG 或 WebP 图片")
    if not 0 < source.stat().st_size <= MAX_COVER_UPLOAD:
        raise ValueError("封面图片需在 20 MB 以内")
    local = runtime(ws)
    try:
        info = probe(local["ffprobe"], source)
        view = next(s for s in info["streams"] if s["codec_type"] == "video")
        if view.get("codec_name") not in {"png", "mjpeg", "webp"} or not 0 < view["width"] * view["height"] <= 32_000_000:
            raise ValueError("需要不超过 3200 万像素的静态图片")
    except Exception as exc:
        raise ValueError("无法读取图片，请检查格式、尺寸或文件是否损坏") from exc
    directory = base(ws, pid)
    directory.mkdir(exist_ok=True)
    original_hash = file_hash(source)
    with tempfile.TemporaryDirectory(prefix="upload-", dir=directory) as tmp:
        stage = Path(tmp)
        run_command([local["ffmpeg"], "-v", "error", "-y", "-i", str(source), "-map_metadata", "-1",
                     "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=2560:2560:force_original_aspect_ratio=decrease",
                     "-update", "1", str(stage / "image.png")], stage / "conversion.log", timeout=90)
        shutil.copy2(source, stage / ("original" + ext))
        sha = file_hash(stage / "image.png")
        metadata = {"name": filename, "sha256": sha, "original_sha256": original_hash,
                    "width": view["width"], "height": view["height"], "uploaded": time.time()}
        with locked(ws, pid):
            doc = load(ws, pid)
            check_revision(doc, revision)
            destination = directory / "images" / original_hash
            destination.parent.mkdir(exist_ok=True)
            if not destination.exists():
                os.replace(stage, destination)
            else:
                # Check an existing upload instead of trusting a hash-shaped directory.
                if file_hash(destination / "image.png") != sha:
                    raise ValueError("已保存的图片校验失败，请重新上传其他文件")
            doc["images"][original_hash] = metadata
            doc["settings"]["image_id"] = original_hash
            doc.update(revision=doc["revision"] + 1, updated=time.time())
            write_json(directory / "cover.json", doc)
    return doc


def validate_settings(doc, settings):
    if not isinstance(settings, dict):
        raise ValueError("请填写封面标题和尺寸")
    title = settings.get("title")
    if not isinstance(title, str):
        raise ValueError("请填写封面标题")
    title = title.strip().replace("\r\n", "\n")
    lines = title.split("\n")
    if not 1 <= len(title) <= 80 or len(lines) > 4 or any(not line.strip() or len(line) > 40 for line in lines) or any(ord(c) < 32 and c != "\n" for c in title):
        raise ValueError("标题需为 1–80 字，最多 4 行，每行不超过 40 字；可回车调整换行")
    aspect = settings.get("aspect")
    if aspect not in (*SIZES, "both"):
        raise ValueError("请选择横竖双封面、16:9、4:3 或 3:4 尺寸")
    image_id = settings.get("image_id")
    if not isinstance(image_id, str) or image_id not in doc["images"]:
        raise ValueError("请先上传并选择一张封面图片")
    return {"title": title, "aspect": aspect, "image_id": image_id}


def prepare(ws, pid, revision, settings):
    with locked(ws, pid) as directory:
        doc = load(ws, pid)
        check_revision(doc, revision)
        clean = validate_settings(doc, settings)
        image = image_path(ws, pid, clean["image_id"])
        sha = file_hash(image)
        if sha != doc["images"][clean["image_id"]]["sha256"]:
            raise ValueError("封面图片已损坏，请重新上传")
        doc.update(settings=clean, revision=doc["revision"] + 1, updated=time.time())
        write_json(directory / "cover.json", doc)
        return {"settings": copy.deepcopy(clean), "cover_revision": doc["revision"],
                "image_sha256": sha, "image_name": doc["images"][clean["image_id"]]["name"]}


def _generate_one(ws, pid, request):
    settings = request["settings"]
    image = image_path(ws, pid, settings["image_id"])
    if file_hash(image) != request["image_sha256"]:
        raise ValueError("封面图片已变化，请重新上传后生成")
    local = runtime(ws)
    renderer = ROOT / "renderer"
    recipe = {"settings": settings, "image_sha256": request["image_sha256"],
              "runtime": {"node": local["node"], "browser": local["browser"]},
              "renderer": {name: file_hash(renderer / name) for name in
                           ["cover-studio.mjs", "src/cover-entry.tsx", "src/CoverStudio.tsx", "package.json"]}}
    signature = digest(recipe)
    outputs = ws.path(pid) / "outputs" / "covers"
    outputs.mkdir(parents=True, exist_ok=True)
    destination = outputs / signature
    width, height = SIZES[settings["aspect"]]

    def verified(path):
        try:
            manifest = read_json(path / "manifest.json")
            if manifest["signature"] != signature or file_hash(path / "cover.png") != manifest["sha256"]:
                return False
            stream = next(s for s in probe(local["ffprobe"], path / "cover.png")["streams"] if s["codec_type"] == "video")
            return stream["width"] == width and stream["height"] == height and stream["codec_name"] == "png"
        except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
            return False

    cached = verified(destination)
    if not cached:
        with tempfile.TemporaryDirectory(prefix=".cover-", dir=outputs) as tmp:
            stage = Path(tmp)
            write_json(stage / "request.json", {"image": str(image), "output": str(stage),
                       "runtime": local, "props": {**settings, "width": width, "height": height}})
            run_command([local["node"], str(renderer / "cover-studio.mjs"), str(stage / "request.json")], stage / "render.log", timeout=300)
            write_json(stage / "manifest.json", {"schema_version": 1, "signature": signature,
                       "recipe": recipe, "width": width, "height": height,
                       "sha256": file_hash(stage / "cover.png"), "created": time.time()})
            if not verified(stage):
                raise ValueError("封面图片未通过完整性与尺寸检查，请重试")
            # Incomplete/corrupt prior output remains recoverable.
            if destination.exists():
                destination.rename(outputs / (signature + "-invalid-" + uuid.uuid4().hex[:8]))
            shutil.rmtree(stage / "bundle", ignore_errors=True)
            shutil.rmtree(stage / "public", ignore_errors=True)
            os.replace(stage, destination)
    result = {"output_directory": str(destination), "files": ["cover.png", "manifest.json"],
              "width": width, "height": height, "settings": settings, "cache_hit": cached,
              "cover_revision": request["cover_revision"]}
    return result


def generate(ws, pid, jid, request):
    if request["settings"]["aspect"] != "both":
        result = _generate_one(ws, pid, request)
    else:
        # Keep each layout's verified cache so a failed second render can resume.
        variants = []
        for aspect, filename in [("4:3", "landscape.png"), ("3:4", "portrait.png")]:
            single = _generate_one(ws, pid, {**request, "settings": {**request["settings"], "aspect": aspect}})
            image = Path(single["output_directory"]) / "cover.png"
            variants.append({"file": filename, "aspect": aspect, "width": single["width"],
                             "height": single["height"], "sha256": file_hash(image),
                             "cache_hit": single["cache_hit"], "_source": image})
        manifest = {"schema_version": 1, "settings": request["settings"],
                    "covers": [{k: v for k, v in item.items() if k not in {"cache_hit", "_source"}} for item in variants]}
        signature = digest(manifest)
        destination = ws.path(pid) / "outputs" / "covers" / signature
        try:
            cached = read_json(destination / "manifest.json") == manifest and all(
                file_hash(destination / item["file"]) == item["sha256"] for item in variants)
        except (OSError, ValueError):
            cached = False
        if not cached:
            with tempfile.TemporaryDirectory(prefix=".cover-pair-", dir=destination.parent) as tmp:
                stage = Path(tmp)
                for item in variants:
                    shutil.copy2(item["_source"], stage / item["file"])
                    if file_hash(stage / item["file"]) != item["sha256"]:
                        raise ValueError("封面复制校验失败，请重试")
                write_json(stage / "manifest.json", manifest)
                if destination.exists():
                    destination.rename(destination.with_name(signature + "-invalid-" + uuid.uuid4().hex[:8]))
                os.replace(stage, destination)
        result = {"output_directory": str(destination), "files": ["landscape.png", "portrait.png", "manifest.json"],
                  "settings": request["settings"], "covers": [{k: v for k, v in item.items() if k != "_source"} for item in variants],
                  "cache_hit": cached and all(item["cache_hit"] for item in variants),
                  "cover_revision": request["cover_revision"]}
    with locked(ws, pid) as directory:
        doc = load(ws, pid)
        # Append the finished version without replacing newer form settings.
        doc["renders"] = ([{"job_id": jid, "created": time.time(), **result}] +
                          [x for x in doc["renders"] if x["job_id"] != jid])[:30]
        write_json(directory / "cover.json", doc)
    return result
