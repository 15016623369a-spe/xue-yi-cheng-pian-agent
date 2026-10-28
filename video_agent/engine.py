from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from . import media
from .pronunciation import spoken_text
from .schema import load_project
from .state import Paused, Store
from .util import digest, emit, file_hash, read_json, resolve_path, run_command, write_json

ROOT = Path(__file__).resolve().parents[1]


def voice_signature_for(profile: dict, base: Path) -> str:
    synthesis = {k:v for k,v in profile.items() if k not in {"speed","volume","consent_basis"}}
    return digest({"voice":synthesis,
                   "reference_audio":file_hash(resolve_path(base,profile["reference_audio"])),
                   "reference_text":file_hash(resolve_path(base,profile["reference_text"]))})


def runtime_identity(runtime: dict, kind: str) -> dict:
    if kind in {"voice","asr"}:
        model = Path(runtime["voice_model" if kind=="voice" else "asr_model"])
        files = {p.name:file_hash(p) for p in model.glob("*.json")}
        # Fingerprint the weight inventory too, without re-reading GBs every stage.
        weights = [(p.name,p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(model.glob("*.safetensors"))]
        return {"python":runtime["voice_python"],"model":str(model),"model_config":files,"weights_inventory":weights,
                "worker":file_hash(ROOT/"workers/mlx_tool.py")}
    return {"node":runtime["node"],"browser":runtime["browser"],
            "package":file_hash(Path(runtime["remotion_root"])/"package.json"),
            "renderer":file_hash(ROOT/"renderer/render.mjs"),"component":file_hash(ROOT/"renderer/src/index.tsx")}


def build(project_path: Path, runtime_path: Path, *, fail_once: str | None = None, stop_after: str | None = None) -> dict:
    config = load_project(project_path)
    base = project_path.resolve().parent
    runtime = read_json(runtime_path)
    store = Store(base/".agent")
    with store.locked():
        run_id = store.begin(config)
        try:
            result = execute(config,base,runtime,store,fail_once,stop_after)
            store.finish("completed")
            result["run_id"] = run_id
            result["stages"] = store.events
            result["counts"] = {s:sum(x["status"]==s for x in store.events) for s in ["built","cached","failed"]}
            write_json(base/".agent/runs"/f"{run_id}.json",result)
            destination = base/"outputs"/project_path.stem/result["export_key"][:12]
            destination.mkdir(parents=True,exist_ok=True)
            for filename,path in result["deliverables"].items():
                temp = destination/(filename+".tmp")
                shutil.copy2(path,temp)
                temp.replace(destination/filename)
            write_json(destination/"project-snapshot.json",{k:v for k,v in config.items() if not k.startswith("_")})
            result["output_directory"] = str(destination)
            write_json(destination/"run-report.json",result)
            write_json(base/"outputs"/f"{project_path.stem}-latest.json",result)
            emit("completed",output=str(destination),counts=result["counts"])
            return result
        except Paused as exc:
            store.finish("paused",str(exc))
            emit("paused",message=str(exc),run_id=run_id)
            return {"status":"paused","run_id":run_id}
        except BaseException as exc:
            store.finish("failed",str(exc))
            emit("failed",message=str(exc),run_id=run_id)
            raise


def execute(config: dict, base: Path, runtime: dict, store: Store, fail_once, stop_after) -> dict:
    # UI / API settings edits do not invalidate already verified media artifacts.
    code = digest({name:file_hash(ROOT/"video_agent"/name) for name in ["engine.py","media.py","alignment.py","pronunciation.py","schema.py","state.py","util.py"]})
    voice_id = runtime_identity(runtime,"voice")
    asr_id = runtime_identity(runtime,"asr")
    render_id = runtime_identity(runtime,"render")
    profile = config["voice"]
    reference_audio = resolve_path(base,profile["reference_audio"])
    reference_text = resolve_path(base,profile["reference_text"])
    voice_signature = voice_signature_for(profile,base)
    assets = {name:resolve_path(base,path) for name,path in config.get("assets",{}).items()}
    completed = []

    def step(name, recipe, action):
        return store.step(name,{"engine":code,"ffmpeg":file_hash(Path(runtime["ffmpeg"])),**recipe},action,fail_once=fail_once,stop_after=stop_after)

    if config.get("opening"):
        clip=assets[config["opening"]["asset"]]
        intro=step("opening",{"asset":file_hash(clip),"opening":config["opening"],"settings":config["settings"]},
                   lambda work:media.opening(work,clip,config,runtime))
        completed.append({"id":"SERIES_OPENING","movie":intro["files"]["video"],"audio":intro["files"]["audio"],
                          "timing":read_json(intro["files"]["timing"]),"preview":intro["files"]["preview"],
                          "voice_method":"approved_existing_opening_reused","keys":{"opening":intro["key"]}})

    for index,scene in enumerate(config["scenes"]):
        sid = scene["id"]
        imported = scene.get("import",{})
        seed = scene.get("seed",430+index)
        eligible = bool(imported and imported.get("speech")==scene["speech"] and imported.get("voice_signature")==voice_signature and imported.get("seed")==seed
                        and imported.get("pronunciations",{})==scene.get("pronunciations",{}))
        raw_source = resolve_path(base,imported["audio"]) if eligible else None
        asr_source = resolve_path(base,imported["asr"]) if eligible else None
        if eligible and (file_hash(raw_source)!=imported.get("audio_sha256") or file_hash(asr_source)!=imported.get("asr_sha256")):
            raise ValueError(f"{sid}: registered imported audio/transcript changed; re-register after review")

        def voice_action(work,scene=scene,eligible=eligible,raw_source=raw_source,seed=seed):
            if eligible:
                shutil.copy2(raw_source,work/"raw.wav")
                method = "imported_existing_lesson_audio"
            else:
                if profile["provider"]=="import_only":
                    raise ValueError("Imported audio does not match edited speech/voice; select a synthesis provider")
                request = {"task":"voice","hf_home":runtime["hf_home"],"model":runtime["voice_model"],"seed":seed,
                           "text":spoken_text(scene["speech"],scene.get("pronunciations")),"reference_audio":str(reference_audio),"reference_text":str(reference_text),
                           "language":profile.get("language","chinese"),"conditioning":profile.get("conditioning","icl"),
                           "asr_model":runtime["asr_model"],"output":str(work/"raw.wav")}
                write_json(work/"request.json",request)
                run_command([runtime["voice_python"],str(ROOT/"workers/mlx_tool.py"),str(work/"request.json")],work/"voice.log")
                method = "local_qwen3_synthesis"
            write_json(work/"provenance.json",{"speech":scene["speech"],"spoken_text":spoken_text(scene["speech"],scene.get("pronunciations")),
                       "pronunciations":scene.get("pronunciations",{}),"voice_signature":voice_signature,"seed":seed,
                       "method":method,"raw_sha256":file_hash(work/"raw.wav"),"direct_listening":False})
            return {"audio":"raw.wav","provenance":"provenance.json"}

        voice = step(f"voice:{sid}",{"text":scene["speech"],"pronunciations":scene.get("pronunciations",{}),"voice_signature":voice_signature,"runtime":voice_id,
                    "seed":seed,"import_audio":file_hash(raw_source) if raw_source else None},voice_action)

        def asr_action(work,voice=voice,eligible=eligible,asr_source=asr_source):
            if eligible:
                shutil.copy2(asr_source,work/"transcript.json")
                method = "imported_transcript_for_exact_audio"
            else:
                request = {"task":"asr","hf_home":runtime["hf_home"],"model":runtime["asr_model"],
                           "audio":str(voice["files"]["audio"]),"output_prefix":str(work/"transcript")}
                write_json(work/"request.json",request)
                run_command([runtime["voice_python"],str(ROOT/"workers/mlx_tool.py"),str(work/"request.json")],work/"asr.log")
                method = "local_whisper_word_timestamps"
            read_json(work/"transcript.json")
            write_json(work/"provenance.json",{"method":method,"audio_sha256":file_hash(voice["files"]["audio"])})
            return {"transcript":"transcript.json","provenance":"provenance.json"}

        asr = step(f"asr:{sid}",{"audio":file_hash(voice["files"]["audio"]),"runtime":asr_id,
                   "import_asr":file_hash(asr_source) if asr_source else None},asr_action)
        prepared = step(f"prepare:{sid}",{"audio":file_hash(voice["files"]["audio"]),"asr":file_hash(asr["files"]["transcript"]),
                        "speech":scene["speech"],"pronunciations":scene.get("pronunciations",{}),"events":scene.get("events",[]),"speed":profile.get("speed",1),
                        "fps":config["settings"]["fps"],"sample_rate":config["settings"]["sample_rate"]},
                        lambda work:media.prepare(work,scene,voice["files"]["audio"],asr["files"]["transcript"],config,runtime))
        used = {"reaction"} | {e["meme"] for e in scene.get("events",[]) if e.get("meme")}
        if scene["visual"].get("template")=="figure":
            used.add(scene["visual"]["asset"])
        selected_assets = {name:path for name,path in assets.items() if name in used}
        rendered = step(f"render:{sid}",{"timing":file_hash(prepared["files"]["timing"]),"visual":scene["visual"],
                        "theme":config.get("theme",{}),"title":scene["title"],"tag":scene.get("tag",""),"runtime":render_id,
                        "settings":{k:config["settings"][k] for k in ["width","height","fps"]},
                        "assets":{name:file_hash(p) for name,p in selected_assets.items()}},
                        lambda work:media.render(work,scene,prepared["files"]["timing"],config,runtime,selected_assets,ROOT))
        sound_assets={e["sound_asset"]:assets[e["sound_asset"]] for e in scene.get("events",[]) if e.get("sound")=="custom"}
        mixed = step(f"mix:{sid}",{"voice":file_hash(prepared["files"]["audio"]),"events":read_json(prepared["files"]["timing"])["events"],
                     "assets":{name:file_hash(p) for name,p in sound_assets.items()},
                     "fps":config["settings"]["fps"],"sfx_volume":config.get("sfx_volume",.045),"voice_volume":profile.get("volume",1)},
                     lambda work:media.mix_scene(work,prepared["files"]["audio"],prepared["files"]["timing"],config,sound_assets,runtime))
        muxed = step(f"mux:{sid}",{"video":file_hash(rendered["files"]["video"]),"audio":file_hash(mixed["files"]["audio"])},
                     lambda work:media.mux(work,rendered["files"]["video"],mixed["files"]["audio"],runtime))
        completed.append({"id":sid,"movie":muxed["files"]["video"],"audio":mixed["files"]["audio"],"narration":prepared["files"]["audio"],
                          "timing":read_json(prepared["files"]["timing"]),"preview":rendered["files"]["preview"],
                          "voice_method":read_json(voice["files"]["provenance"])["method"],
                          "keys":{name:item["key"] for name,item in [("voice",voice),("asr",asr),("prepare",prepared),("render",rendered),("mix",mixed),("mux",muxed)]}})
    bgm = assets.get(config.get("bgm",{}).get("asset",""))
    exported = step("export",{"scenes":[{"video":file_hash(s["movie"]),"timing":s["timing"],"narration":file_hash(s.get("narration",s["audio"]))} for s in completed],
                    "settings":config["settings"],"bgm":config.get("bgm",{}),"bgm_file":file_hash(bgm) if bgm else None},
                    lambda work:media.export(work,completed,config,runtime,bgm))
    source_hash = file_hash(resolve_path(base,config["source"])) if config.get("source") else None
    checked = step("audit",{"video":file_hash(exported["files"]["video"]),"timeline":file_hash(exported["files"]["timeline"]),
                   "settings":config["settings"],"source":source_hash,"sync_worker":file_hash(ROOT/"workers/check_sync.py"),
                   "scene_audio":[file_hash(s["audio"]) for s in completed],"voice_volume":profile.get("volume",1)},
                   lambda work:media.audit(work,exported["files"]["video"],exported["files"]["timeline"],config,runtime,completed,ROOT))
    deliverables = {"video.mp4":str(exported["files"]["video"]),"captions.srt":str(exported["files"]["srt"]),
                    "timeline.json":str(exported["files"]["timeline"]),"audit.json":str(checked["files"]["report"])}
    for s in completed:
        deliverables[f"{s['id']}-preview.png"] = str(s["preview"])
    return {"status":"completed","project_id":config["project_id"],"export_key":exported["key"],"source_sha256":source_hash,
            "scene_artifacts":[{k:str(v) if isinstance(v,Path) else v for k,v in s.items()} for s in completed],
            "deliverables":deliverables,"scope":"deterministic media execution; text planning and human review recorded separately"}
