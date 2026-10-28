from __future__ import annotations

import array
import math
import random
import re
import shutil
import sys
import wave
from pathlib import Path

from .alignment import align, srt_time, check_reference_bleed
from .util import probe, read_json, run_command, write_json


def read_pcm(path: Path) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as f:
        if (f.getnchannels(),f.getsampwidth()) != (1,2):
            raise ValueError("Expected mono 16-bit PCM")
        rate = f.getframerate()
        data = array.array("h",f.readframes(f.getnframes()))
    if sys.byteorder != "little":
        data.byteswap()
    return data,rate


def write_pcm(path: Path, data: array.array, rate: int):
    with wave.open(str(path),"wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(data.tobytes())


def soften_edges(samples, rate, milliseconds=8):
    """Meet silence at zero amplitude without moving samples or word timings."""
    count=min(round(rate*milliseconds/1000),len(samples)//2)
    for i in range(count):
        gain=math.sin(math.pi*.5*i/max(1,count-1))**2
        samples[i]=round(samples[i]*gain)
        samples[-1-i]=round(samples[-1-i]*gain)
    return samples


def prepare(work: Path, scene: dict, raw: Path, asr: Path, config: dict, runtime: dict) -> dict[str,str]:
    ffmpeg = runtime["ffmpeg"]
    rate,fps = config["settings"]["sample_rate"],config["settings"]["fps"]
    speed = config["voice"].get("speed",1)
    from .util import resolve_path
    reference=resolve_path(Path(config.get("_path",work)).parent,config["voice"]["reference_text"]).read_text()
    from .pronunciation import spoken_text
    hygiene=check_reference_bleed(spoken_text(scene["speech"],scene.get("pronunciations")),read_json(asr),reference)
    run_command([ffmpeg,"-v","error","-y","-i",str(raw),"-ac","1","-ar",str(rate),"-c:a","pcm_s16le",str(work/"raw-pcm.wav")],work/"convert.log")
    samples,sr = read_pcm(work/"raw-pcm.wav")
    hop = max(1,round(sr*.02))
    active = [i/sr for i in range(0,len(samples),hop) if samples[i:i+hop] and math.sqrt(sum(x*x for x in samples[i:i+hop])/len(samples[i:i+hop]))>500]
    trim = max(0,active[0]-.12) if active else 0
    run_command([ffmpeg,"-v","error","-y","-i",str(raw),"-af",f"atrim=start={trim},asetpts=N/SR/TB,atempo={speed},loudnorm=I=-19:TP=-3:LRA=9", "-ac","1","-ar",str(rate),"-c:a","pcm_s16le",str(work/"fast.wav")],work/"prepare.log")
    fast,sr = read_pcm(work/"fast.wav")
    soften_edges(fast,sr)
    lead,tail = 3,max(6,round(.3*fps))
    frames = lead+math.ceil(len(fast)*fps/sr)+tail
    frame_samples = sr//fps
    track = array.array("h",[0])*(frames*frame_samples)
    track[lead*frame_samples:lead*frame_samples+len(fast)] = fast
    write_pcm(work/"narration.wav",track,sr)
    aligned = align(scene["speech"],read_json(asr),scene.get("events",[]),trim=trim,speed=speed,
                    audio_seconds=len(fast)/sr,frames=frames,fps=fps,lead=lead,
                    pronunciations=scene.get("pronunciations"))
    timeline = {"id":scene["id"],"title":scene["title"],"speech":scene["speech"],"frames":frames,
                "fps":fps,"trim_start_seconds":trim,"voice_hygiene":hygiene,**aligned}
    write_json(work/"timing.json",timeline)
    (work/"captions.srt").write_text("\n\n".join(f"{i+1}\n{srt_time(c['start'],fps)} --> {srt_time(c['end'],fps)}\n{c['text']}" for i,c in enumerate(timeline["captions"]))+"\n")
    return {"audio":"narration.wav","timing":"timing.json","srt":"captions.srt"}


def render(work: Path, scene: dict, timing: Path, config: dict, runtime: dict, assets: dict[str,Path], root: Path) -> dict[str,str]:
    public = work/"public"
    public.mkdir()
    asset_names = {}
    for name,p in assets.items():
        if name == "bgm":
            continue
        filename = name+p.suffix.lower()
        shutil.copy2(p,public/filename)
        asset_names[name] = filename
    props = {"scene":{**read_json(timing),"title":scene["title"],"visual":scene["visual"],"tag":scene.get("tag","")},
             "theme":config.get("theme",{}),"settings":config["settings"],"assets":asset_names}
    write_json(work/"props.json",props)
    write_json(work/"request.json",{"props":str(work/"props.json"),"public":str(public),"output":str(work/"silent.mp4"),
                                     "still":str(work/"preview.png"),"bundle":str(work/"bundle"),
                                     "remotion_root":runtime["remotion_root"],"browser":runtime["browser"]})
    run_command([runtime["node"],str(root/"renderer/render.mjs"),str(work/"request.json")],work/"render.log")
    return {"video":"silent.mp4","preview":"preview.png","props":"props.json"}


def mix_scene(work: Path, narration: Path, timing: Path, config: dict, assets=None, runtime=None) -> dict[str,str]:
    samples,rate = read_pcm(narration)
    voice_volume=config.get("voice",{}).get("volume",1)
    floats = array.array("f",(x/32768*voice_volume for x in samples))
    data = read_json(timing)
    rng = random.Random(404)
    volume = config.get("sfx_volume",.045)
    for e in data["events"]:
        start = round(e["start"]*rate/data["fps"])
        kind = e.get("sound","click")
        event_gain=e.get("sound_gain",1)
        if kind == "none":
            continue
        if kind == "custom":
            source=(assets or {})[e["sound_asset"]]
            converted=work/(e["id"]+"-effect.wav")
            run_command([runtime["ffmpeg"],"-v","error","-y","-i",str(source),"-t",str(e.get("seconds",1.5)),"-ac","1","-ar",str(rate),"-c:a","pcm_s16le",str(converted)],work/(e["id"]+"-effect.log"))
            effect,_=read_pcm(converted)
            soften_edges(effect,rate)
            peak=max((abs(x) for x in effect),default=0)
            gain=volume*event_gain*.9/max(1,peak)
            for i in range(min(len(effect),len(floats)-start)):
                floats[start+i]+=effect[i]*gain
            continue
        duration = {"click":.1,"beep":.25,"pop":.16,"whoosh":.18,"error":.18}[kind]
        count = min(round(duration*rate),len(floats)-start)
        for i in range(count):
            t = i/rate
            window = math.sin(math.pi*i/max(1,count-1))**2
            if kind == "click":
                x = (rng.uniform(-1,1)*.35+math.sin(2*math.pi*1700*t)*.65)*math.exp(-t*70)
            elif kind == "beep":
                q = t if t<.1 else t-.13
                x = math.sin(2*math.pi*(960 if t<.1 else 1240)*q)*math.sin(math.pi*q/.085)**2 if 0<=q<.085 else 0
            elif kind == "pop":
                x = math.sin(2*math.pi*(430*t+900*t*t))*math.exp(-t*23)
            elif kind == "error":
                x = math.sin(2*math.pi*(320*t-350*t*t))*window
            else:
                x = rng.uniform(-1,1)*window*.3
            floats[start+i] += x*volume*event_gain
    peak = max(abs(x) for x in floats)
    gain = min(1,.85/max(peak,1e-9))
    result = array.array("h",(round(max(-1,min(1,x*gain))*32767) for x in floats))
    write_pcm(work/"mixed.wav",result,rate)
    write_json(work/"mix.json",{"peak":peak*gain,"gain":gain,"voice_volume":voice_volume,"events":len(data["events"]),"sample_count":len(result)})
    return {"audio":"mixed.wav","report":"mix.json"}


def mux(work: Path, video: Path, audio: Path, runtime: dict) -> dict[str,str]:
    # PCM intermediates avoid cumulative AAC encoder padding across scene joins.
    run_command([runtime["ffmpeg"],"-v","error","-y","-i",str(video),"-i",str(audio),"-map","0:v:0","-map","1:a:0",
                 "-c:v","copy","-c:a","pcm_s16le","-video_track_timescale","90000",str(work/"scene.mov")],work/"mux.log")
    return {"video":"scene.mov"}


def export(work: Path, scenes: list[dict], config: dict, runtime: dict, bgm: Path | None) -> dict[str,str]:
    # Copy to local safe filenames: concat quoting never depends on user paths.
    for i,s in enumerate(scenes):
        (work/f"part-{i}.mov").symlink_to(s["movie"])
    (work/"concat.txt").write_text("\n".join(f"file 'part-{i}.mov'" for i in range(len(scenes)))+"\n")
    args = [runtime["ffmpeg"],"-v","error","-y","-f","concat","-safe","0","-i",str(work/"concat.txt")]
    frames = sum(s["timing"]["frames"] for s in scenes)
    fps = config["settings"]["fps"]
    seconds = frames/fps
    if seconds>config["settings"]["maximum_seconds"]:
        raise ValueError(f"Project exceeds duration limit: {seconds:.3f}s")
    mix_info = {"bgm_enabled":bool(bgm)}
    if bgm:
        head_seconds=sum(s["timing"]["frames"]/fps for s in scenes if s["timing"].get("source_clip"))
        # Measure actual soundtrack RMS, then set a narration-relative music gain.
        run_command([runtime["ffmpeg"],"-v","error","-y","-i",str(bgm),"-t",str(min(seconds,30)),"-ac","1","-ar","24000","-c:a","pcm_s16le",str(work/"music-measure.wav")],work/"music-measure.log")
        music,_ = read_pcm(work/"music-measure.wav")
        music_rms = math.sqrt(sum(x*x for x in music)/max(1,len(music)))
        energy,n = 0,0
        for s in scenes:
            if s["timing"].get("source_clip"):
                continue
            # Music stays at its own setting when narration volume changes.
            # Measure the unscaled narration, excluding opening clips and SFX.
            voice,_ = read_pcm(s.get("narration",s["audio"]))
            active = [x for x in voice if abs(x)>700]
            energy += sum(x*x for x in active)
            n += len(active)
        voice_rms = math.sqrt(energy/max(1,n))
        relative = config.get("bgm",{}).get("relative_db",-24)
        gain = voice_rms*10**(relative/20)/max(1,music_rms)
        args += ["-stream_loop","-1","-i",str(bgm),"-filter_complex",f"[1:a]volume={gain},afade=t=in:d=0.35,adelay={round(head_seconds*1000)}:all=1,afade=t=out:st={max(0,seconds-1)}:d=1[b];[0:a][b]amix=inputs=2:normalize=0[a]","-map","0:v:0","-map","[a]"]
        mix_info.update(relative_db=relative,bgm_gain=gain)
    else:
        args += ["-map","0:v:0","-map","0:a:0"]
    args += ["-c:v","copy","-c:a","aac","-b:a","192k","-ar",str(config["settings"]["sample_rate"]),
             "-t",str(seconds),"-video_track_timescale","90000","-movflags","+faststart",str(work/"video.mp4")]
    run_command(args,work/"export.log")
    position,captions,items = 0,[],[]
    for s in scenes:
        t = s["timing"]
        items.append({**t,"start":position})
        captions += [{**c,"start":position+c["start"],"end":position+c["end"]} for c in t["captions"]]
        position += t["frames"]
    timeline = {"fps":fps,"frames":frames,"seconds":seconds,"scenes":items}
    write_json(work/"timeline.json",timeline)
    write_json(work/"mix.json",mix_info)
    (work/"captions.srt").write_text("\n\n".join(f"{i+1}\n{srt_time(c['start'],fps)} --> {srt_time(c['end'],fps)}\n{c['text']}" for i,c in enumerate(captions))+"\n")
    return {"video":"video.mp4","timeline":"timeline.json","srt":"captions.srt","mix":"mix.json"}


def audit(work: Path, video: Path, timing: Path, config: dict, runtime: dict, scenes: list[dict], root: Path) -> dict[str,str]:
    data = read_json(timing)
    meta = probe(runtime["ffprobe"],video)
    v = next(s for s in meta["streams"] if s["codec_type"]=="video")
    a = next(s for s in meta["streams"] if s["codec_type"]=="audio")
    settings = config["settings"]
    checks = {
        "expected_resolution": (v["width"],v["height"])==(settings["width"],settings["height"]),
        "expected_frame_count": int(v["nb_frames"])==data["frames"],
        "expected_fps": abs(eval_fraction(v["r_frame_rate"])-settings["fps"])<1e-6,
        "duration_within_limit":float(meta["format"]["duration"])<=settings["maximum_seconds"]+.05,
        "audio_video_duration_difference_under_80ms": abs(float(v["duration"])-float(a["duration"]))<.08,
    }
    run_command([runtime["ffmpeg"],"-v","error","-i",str(video),"-f","null","-"],work/"decode.log")
    checks["full_decode"] = True
    boundary_checks = []
    for s in data["scenes"]:
        last = -1
        for c in s["captions"]:
            boundary_checks.append(0<=c["start"]<c["end"]<=s["frames"] and c["start"]>=last)
            last = c["end"]
        for e in s["events"]:
            boundary_checks.append(0<=e["start"]<e["start"]+e["duration"]<=s["frames"] and e["anchor_matched"])
    checks["caption_event_boundaries"] = all(boundary_checks)
    run_command([runtime["ffmpeg"],"-v","info","-i",str(video),"-vn","-af","volumedetect","-f","null","-"],work/"volume.log")
    m = re.search(r"max_volume: ([\-\d.]+) dB",(work/"volume.log").read_text())
    peak = float(m.group(1)) if m else 0
    checks["no_sample_clipping"] = peak<-.1
    run_command([runtime["ffmpeg"],"-v","error","-y","-i",str(video),"-vn","-ac","1","-ar","24000","-c:a","pcm_s16le",str(work/"decoded.wav")],work/"audio-decode.log")
    write_json(work/"sync-request.json",{"decoded":str(work/"decoded.wav"),"output":str(work/"sync.json"),
                "scenes":[{"id":s["id"],"audio":str(s["audio"]),"seconds":s["timing"]["frames"]/s["timing"]["fps"],
                           "allow_silent":config["voice"].get("volume",1)==0 and not s["timing"].get("source_clip")} for s in scenes]})
    run_command([runtime["voice_python"],str(root/"workers/check_sync.py"),str(work/"sync-request.json")],work/"sync.log")
    sync=read_json(work/"sync.json")
    checks["all_scene_audio_aligned_under_40ms"] = sync["passed"]
    result = {"passed":all(checks.values()),"checks":checks,"duration_seconds":float(meta["format"]["duration"]),
              "peak_dbfs":peak,"per_scene_audio_sync":sync["scenes"],"direct_listening":False,"continuous_visual_review":False,
              "scope":"technical checks only; course correctness and editorial review remain separate"}
    write_json(work/"audit.json",result)
    if not result["passed"]:
        raise RuntimeError(f"Technical checks failed: {checks}; report: {work/'audit.json'}")
    return {"report":"audit.json"}


def eval_fraction(value: str) -> float:
    a,b = value.split("/")
    return float(a)/float(b)


def opening(work: Path, clip: Path, config: dict, runtime: dict) -> dict[str,str]:
    settings=config["settings"];fps=settings["fps"];rate=settings["sample_rate"]
    info=probe(runtime["ffprobe"],clip)
    frames=round(float(info["format"]["duration"])*fps)
    seconds=frames/fps
    run_command([runtime["ffmpeg"],"-v","error","-y","-i",str(clip),"-vf",f"scale={settings['width']}:{settings['height']}:force_original_aspect_ratio=decrease,pad={settings['width']}:{settings['height']}:(ow-iw)/2:(oh-ih)/2,fps={fps},setpts=N/({fps}*TB)","-frames:v",str(frames),"-an","-c:v","libx264","-crf","18","-pix_fmt","yuv420p",str(work/"silent.mp4")],work/"video.log")
    run_command([runtime["ffmpeg"],"-v","error","-y","-i",str(clip),"-vn","-af",f"apad,atrim=duration={seconds},asetpts=N/SR/TB","-ac","1","-ar",str(rate),"-c:a","pcm_s16le",str(work/"narration.wav")],work/"audio.log")
    mux(work,work/"silent.mp4",work/"narration.wav",runtime)
    run_command([runtime["ffmpeg"],"-v","error","-y","-ss",str(min(seconds/2,7.1)),"-i",str(work/"silent.mp4"),"-frames:v","1",str(work/"preview.png")],work/"still.log")
    captions=[{"text":c["text"],"start":round(c["start"]*fps),"end":min(frames,round(c["end"]*fps))} for c in config["opening"].get("captions",[])]
    if any(not 0<=c["start"]<c["end"]<=frames for c in captions):
        raise ValueError("系列片头字幕超出实际时长")
    write_json(work/"timing.json",{"id":"SERIES_OPENING","title":config["opening"].get("title","已确认的系列片头"),"speech":config["opening"].get("description","原切片及固定前置说明"),"frames":frames,"fps":fps,"source_clip":True,"captions":captions,"events":[]})
    return {"video":"scene.mov","audio":"narration.wav","timing":"timing.json","preview":"preview.png"}
