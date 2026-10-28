from __future__ import annotations

import math
import re
from pathlib import Path
from .util import read_json, resolve_path

ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
TEMPLATES = {"flow", "branch", "calculation", "figure"}
SOUNDS = {"none", "custom", "click", "beep", "pop", "whoosh", "error"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def number(value, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def load_project(path: Path) -> dict:
    path = path.resolve()
    data = read_json(path)
    require(data.get("schema_version") == 1, "schema_version must be 1")
    require(bool(ID.fullmatch(data.get("project_id", ""))), "Invalid project_id")
    require(isinstance(data.get("title"), str) and bool(data["title"].strip()), "Missing title")
    settings = data.get("settings", {})
    for key, default, low, high in [("width", 1280, 640, 3840), ("height", 720, 360, 2160), ("fps", 30, 24, 60), ("sample_rate", 24000, 16000, 48000)]:
        value = settings.setdefault(key, default)
        require(isinstance(value, int) and not isinstance(value, bool) and low <= value <= high, f"Invalid settings.{key}")
    require(settings["width"] % 2 == settings["height"] % 2 == 0, "Video dimensions must be even")
    require(abs(settings["width"]/settings["height"]-16/9)<1e-6, "Stage A templates currently require 16:9 video")
    require(settings["sample_rate"] % settings["fps"] == 0, "sample_rate must divide exactly into video frames")
    settings.setdefault("maximum_seconds", 240)
    require(number(settings["maximum_seconds"], 1, 3600), "Invalid maximum_seconds")
    data["settings"] = settings
    if data.get("opening"):
        opening=data["opening"]
        require(opening.get("asset") in data.get("assets",{}), "Opening asset not registered")
        require(isinstance(opening.get("captions",[]),list), "Opening captions must be a list")
        require(all(isinstance(c.get("text"),str) and number(c.get("start"),0,120) and number(c.get("end"),0,120) and c["start"]<c["end"] for c in opening.get("captions",[])),"Invalid opening captions")
    voice = data.get("voice", {})
    require(voice.get("conditioning","icl") in {"icl","speaker_embedding"}, "Unsupported voice conditioning")
    require(number(voice.get("speed", 1), .5, 2), "voice.speed must be 0.5–2")
    require(number(voice.get("volume", 1), 0, 2), "voice.volume must be 0–2")
    require(voice.get("provider") in {"qwen3_local", "import_only"}, "Unsupported voice provider")
    for key in ["reference_audio","reference_text"]:
        require(isinstance(voice.get(key), str) and resolve_path(path.parent,voice[key]).is_file(), f"Missing voice.{key}")
    require(isinstance(data.get("scenes"), list) and bool(data["scenes"]), "Need at least one scene")
    ids: set[str] = set()
    for scene in data["scenes"]:
        sid = scene.get("id", "")
        require(bool(ID.fullmatch(sid)) and sid not in ids, f"Invalid or duplicate scene id: {sid}")
        ids.add(sid)
        require(isinstance(scene.get("speech"), str) and bool(scene["speech"].strip()), f"{sid}: speech is empty")
        from .pronunciation import validate as validate_pronunciation
        validate_pronunciation(scene["speech"],scene.get("pronunciations",{}))
        require(isinstance(scene.get("title"), str) and bool(scene["title"]), f"{sid}: title missing")
        visual = scene.get("visual", {})
        require(visual.get("template") in TEMPLATES, f"{sid}: unsupported visual template")
        if visual["template"] == "flow":
            require(isinstance(visual.get("steps"), list) and 2 <= len(visual["steps"]) <= 4, f"{sid}: need 2–4 flow steps")
            require(all(isinstance(s.get("label"), str) and bool(s["label"]) for s in visual["steps"]), f"{sid}: each step needs label")
        elif visual["template"] == "branch":
            require(all(isinstance(visual.get(k), str) and bool(visual[k]) for k in ["input", "output", "bypass", "formula"]), f"{sid}: branch labels missing")
            require(isinstance(visual.get("steps"), list) and 1 <= len(visual["steps"]) <= 2, f"{sid}: branch template supports 1–2 processing steps")
        elif visual["template"] == "figure":
            require(visual.get("asset") in data.get("assets",{}), f"{sid}: figure asset not registered")
            require(isinstance(visual.get("caption",""),str), f"{sid}: invalid figure caption")
        else:
            require(isinstance(visual.get("lines"), list) and 1 <= len(visual["lines"]) <= 4, f"{sid}: need calculation lines")
            require(all(isinstance(line, str) and line for line in visual["lines"]), f"{sid}: invalid calculation line")
        event_ids: set[str] = set()
        for event in scene.get("events", []):
            eid = event.get("id", "")
            require(bool(ID.fullmatch(eid)) and eid not in event_ids, f"{sid}: duplicate/invalid event id")
            event_ids.add(eid)
            anchor = event.get("anchor", "")
            require(bool(anchor) and scene["speech"].count(anchor) == 1, f"{sid}/{eid}: anchor must occur exactly once in speech: {anchor}")
            require(event.get("sound", "click") in SOUNDS, f"{sid}/{eid}: unsupported sound")
            require(number(event.get("sound_gain",1),0,3), f"{sid}/{eid}: invalid sound gain")
            if event.get("sound") == "custom":
                key=event.get("sound_asset", "")
                require(key in data.get("assets",{}), f"{sid}/{eid}: sound asset not registered")
                require(Path(data["assets"][key]).suffix.lower() in {".wav",".mp3",".m4a",".aac",".ogg",".flac"}, f"{sid}/{eid}: expected audio asset")
            require(number(event.get("seconds", 1.5), .1, 10), f"{sid}/{eid}: invalid duration")
            if event.get("meme"):
                require(event["meme"] in data.get("assets",{}), f"{sid}/{eid}: meme asset not registered")
    bgm = data.get("bgm", {})
    if bgm:
        require(number(bgm.get("relative_db", -24), -60, -6), "bgm.relative_db must be -60 to -6")
        require(bgm.get("asset") in data.get("assets",{}), "BGM asset not registered")
    require(number(data.get("sfx_volume",.045),0,.3), "sfx_volume must be 0–0.3")
    for asset in data.get("assets", {}).values():
        require(isinstance(asset, str), "asset paths must be strings")
        require(resolve_path(path.parent, asset).is_file(), f"Missing asset: {asset}")
    data["_path"] = str(path)
    return data
