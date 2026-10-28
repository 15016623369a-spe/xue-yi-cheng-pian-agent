"""A local, reusable resource catalogue. Only registered files are exposed."""
from __future__ import annotations

import copy
import fcntl
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .util import digest, file_hash, probe, read_json, resolve_path, run_command, write_json
from .workspace import Conflict, LOCK

CATEGORIES = {"voice":"语音素材", "md":"MD 文件", "image":"图片素材", "clip":"视频切片", "sound":"音效 / 音乐", "generated":"生成视频"}
IMAGE_EXTS={".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXTS={".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
VIDEO_EXTS={".mp4", ".mov", ".webm"}


@contextmanager
def locked(ws):
    with LOCK:
        with (ws.web/"library.lock").open("a+") as guard:
            fcntl.flock(guard,fcntl.LOCK_EX)
            yield


def index(ws):
    p=ws.web/"library.json"
    return read_json(p) if p.is_file() else {"items":{}, "labels":{}}


def catalogue(ws, private=False):
    stored=index(ws); found={}; aliases={}
    def add(path, category, name, project="", context="", metadata=None):
        path=Path(path).resolve()
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS|AUDIO_EXTS|VIDEO_EXTS|{".md",".txt"}:return
        rid=digest({"path":str(path),"category":category})[:24]
        if rid in found:
            entry=found[rid]
            if project and project not in entry["projects"]:entry["projects"].append(project)
            if context and context not in entry["context"]:entry["context"]=(entry["context"]+" "+context)[-2000:]
            return
        meta=metadata or {}; labels=stored.get("labels",{}).get(rid,{})
        if meta.get("library_id") and meta["library_id"]!=rid:aliases[rid]=meta["library_id"]
        found[rid]={"id":rid,"category":category,"name":labels.get("name") or meta.get("name") or name,
                    "tags":labels.get("tags",meta.get("tags",[])),"projects":[project] if project else [],"context":context[:2000],
                    "size":path.stat().st_size,"updated":path.stat().st_mtime,"source":meta.get("source",{}),
                    "seconds":meta.get("seconds"),"url":"/api/library/"+rid+"/file","_path":path}
    for p in ws.projects.glob("web-*/workspace.json"):
        try:
            doc=read_json(p); cfg=doc["config"]; title=doc["title"]
            add(p.parent/"handoff.md","md",doc.get("source_name","handoff.md"),title)
            ref=cfg.get("voice",{}).get("reference_audio")
            if ref:add(resolve_path(p.parent,ref),"voice","参考音色 · "+Path(ref).name,title)
            for key,value in cfg.get("assets",{}).items():
                asset=resolve_path(p.parent,value); ext=asset.suffix.lower(); meta=cfg.get("asset_metadata",{}).get(key,{})
                category="sound" if ext in AUDIO_EXTS else "image" if ext in IMAGE_EXTS else "clip" if ext in VIDEO_EXTS else None
                if not category:continue
                uses=[" ".join([e.get("anchor",""),e.get("popup","")]) for s in (doc.get("storyboard") or {}).get("scenes",[]) for e in s.get("events",[]) if key in {e.get("meme"),e.get("sound_asset")}]
                add(asset,category,key,title," ".join(uses),meta)
        except (OSError,ValueError,KeyError,TypeError):continue
    for p in (ws.web/"jobs").glob("*/job.json"):
        try:
            job=read_json(p)
            if job.get("status")!="completed" or job.get("kind") not in {"render","cover"}:continue
            pid=job["project_id"]; base=ws.path(pid); out=Path(job["result"]["output_directory"]).resolve()
            if not out.is_relative_to((base/"outputs").resolve()):continue
            title=ws.get(pid)["title"]; suffix=" · "+job["scene_id"] if job.get("scene_id") else " · 整片"
            if job["kind"]=="cover":add(out/"cover.png","image",title+" · 封面",title);continue
            add(out/"video.mp4","generated",title+suffix+f" · r{job['revision']}",title)
            report=out/"run-report.json"
            if report.is_file():
                for scene in read_json(report).get("scene_artifacts",[]):
                    path=Path(scene.get("narration","")).resolve()
                    if path.is_relative_to((base/".agent").resolve()):
                        add(path,"voice",title+" · "+scene["id"]+" 配音",title,scene.get("timing",{}).get("speech",""))
        except (OSError,ValueError,KeyError,TypeError):continue
    for saved in stored.get("items",{}).values():
        path=(ws.root/saved["path"]).resolve()
        if path.is_relative_to((ws.root/"assets/library").resolve()):
            add(path,saved["category"],saved["name"],metadata=saved,context=saved.get("context",""))
    # Project-local copies stay portable but appear once under their library source.
    for copied,origin in aliases.items():
        if copied in found and origin in found:
            target=found[origin]; duplicate=found.pop(copied)
            target["projects"]=list(dict.fromkeys(target["projects"]+duplicate["projects"]))
            if duplicate["context"] and duplicate["context"] not in target["context"]:
                target["context"]=(target["context"]+" "+duplicate["context"])[-2000:]
    entries=sorted(found.values(),key=lambda e:e["updated"],reverse=True)
    return entries if private else [{k:v for k,v in e.items() if k!="_path"} for e in entries]


def resource(ws,rid):
    if not isinstance(rid,str) or not re.fullmatch(r"[a-f0-9]{24}",rid):raise ValueError("无效资源编号")
    item=next((i for i in catalogue(ws,True) if i["id"]==rid),None)
    if not item:raise FileNotFoundError("资源不存在")
    return item


def annotate(ws,rid,data):
    current=resource(ws,rid)
    name=data.get("name",current["name"]); tags=data.get("tags",current["tags"])
    if not isinstance(name,str) or not 1<=len(name.strip())<=180 or any(ord(c)<32 for c in name):raise ValueError("名称需为 1–180 个字")
    if not isinstance(tags,list) or len(tags)>16 or any(not isinstance(t,str) or len(t)>40 for t in tags):raise ValueError("最多16个标签，每个不超过40字")
    with locked(ws):
        saved=index(ws);saved["labels"][rid]={"name":name.strip(),"tags":[t.strip() for t in tags if t.strip()]}
        write_json(ws.web/"library.json",saved)
    return {"id":rid,**saved["labels"][rid]}


def register(ws,source,filename,category,metadata=None):
    from .engine import ROOT
    from .uploads import MAX_UPLOAD, normalize
    if category not in {"voice","md","image","clip","sound"}:raise ValueError("请选择资源分类")
    filename=Path(filename.replace("\\","/")).name
    if not filename or len(filename)>180 or any(ord(c)<32 for c in filename):raise ValueError("文件名不正确")
    if not 0<source.stat().st_size<=MAX_UPLOAD:raise ValueError("单个文件需在100MB以内")
    runtime=read_json(ws.root/"config/local.json" if (ws.root/"config/local.json").is_file() else ROOT/"config/local.json")
    with tempfile.TemporaryDirectory(prefix="library-",dir=ws.web) as tmp:
        stage=Path(tmp); ext=source.suffix.lower()
        if category=="md":
            if ext not in {".md",".txt"} or source.stat().st_size>600000:raise ValueError("学习文档需为不超过600KB的MD/TXT")
            text=source.read_text(encoding="utf-8-sig")
            if not text.strip() or len(text)>50000:raise ValueError("学习文档需为1–5万字")
            normalized=stage/"resource.md";normalized.write_text(text);meta={"name":filename}
        elif category=="voice":
            if ext not in AUDIO_EXTS:raise ValueError("请选择音频文件")
            info=probe(runtime["ffprobe"],source);seconds=float(info.get("format",{}).get("duration",0))
            if not 0<seconds<=1800 or not any(s.get("codec_type")=="audio" for s in info.get("streams",[])):raise ValueError("语音素材需为不超过30分钟的音频")
            normalized=stage/"resource.wav"
            run_command([runtime["ffmpeg"],"-v","error","-y","-i",str(source),"-vn","-ac","1","-ar","24000","-c:a","pcm_s16le",str(normalized)],stage/"convert.log")
            meta={"name":filename,"seconds":seconds}
        else:
            local_source=stage/("input"+ext);shutil.copy2(source,local_source)
            part,normalized,meta,_=normalize(local_source,filename,"sound" if category=="sound" else "media",runtime)
            actual={"sound":"sound","image":"image","video":"clip"}[meta["kind"]]
            if actual!=category and not (category=="image" and ext==".gif"):raise ValueError("文件与所选分类不匹配")
            category=actual
        sha=file_hash(normalized); dest=ws.root/"assets/library"/sha
        with locked(ws):
            dest.mkdir(parents=True,exist_ok=True)
            target=dest/("resource"+normalized.suffix.lower())
            if not target.exists():shutil.copy2(normalized,target)
            original=dest/("original"+ext)
            if not original.exists():shutil.copy2(source,original)
            rid=digest({"path":str(target.resolve()),"category":category})[:24]
            saved=index(ws)
            # Identical imports retain earlier source/labels rather than overwriting provenance.
            saved["items"].setdefault(rid,{**meta,"category":category,"path":str(target.relative_to(ws.root)),"sha256":sha,"uploaded":time.time(),**(metadata or {})})
            write_json(ws.web/"library.json",saved)
    return {k:v for k,v in resource(ws,rid).items() if k!="_path"}


def attach(ws,pid,revision,ids,apply=None):
    """Copy selected resources locally, then register in one revision transaction."""
    if not isinstance(ids,list) or not 1<=len(ids)<=30:raise ValueError("一次选择1–30个资源")
    chosen=[resource(ws,rid) for rid in dict.fromkeys(ids)]
    if any(i["category"] not in {"image","clip","sound"} for i in chosen):raise ValueError("此处可复用图片、切片或音效；MD用于新建项目，语音用于下载试听")
    with ws.locked_project(pid):
        doc=ws.get(pid)
        if doc["revision"]!=revision:raise Conflict("项目已有修改；未覆盖，请刷新后重新选择")
        mapping={}
        for item in chosen:
            src=item["_path"];sha=file_hash(src);key=("sfx_" if item["category"]=="sound" else "meme_")+sha[:16]
            dest=ws.path(pid)/"assets/library"/(sha+src.suffix.lower());dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.is_file() or file_hash(dest)!=sha:
                temp=dest.with_suffix(dest.suffix+".tmp");shutil.copy2(src,temp);os.replace(temp,dest)
            doc["config"].setdefault("assets",{})[key]=str(dest.relative_to(ws.path(pid)))
            doc["config"].setdefault("asset_metadata",{})[key]={"name":item["name"],"kind":{"image":"image","clip":"video","sound":"sound"}[item["category"]],"sha256":sha,"library_id":item["id"],"source":item["source"],"tags":item["tags"],"seconds":item["seconds"]}
            mapping[item["id"]]=key
        if apply:apply(doc,mapping)
        if doc.get("storyboard"):doc["config"]=ws.compile(doc)
        doc.update(revision=revision+1,updated=time.time(),approved=False,review={});ws._write(ws.path(pid),doc)
    return {"project":doc,"mapping":mapping}
