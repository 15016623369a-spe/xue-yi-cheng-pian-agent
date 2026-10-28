from __future__ import annotations

import copy
import fcntl
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .planning import call_model, plan
from .util import digest, read_json, write_json
from .workspace import LOCK, Workspace


class Jobs:
    def __init__(self,workspace):
        self.ws=workspace
        self.root=workspace.web/"jobs"
        self.root.mkdir(exist_ok=True)

    def folder(self,jid):
        if not re.fullmatch(r"[a-f0-9]{32}",jid or ""):
            raise ValueError("无效任务编号")
        return self.root/jid

    def describe(self,data):
        # Old tasks must report the settings in their immutable input snapshot,
        # rather than borrowing the current project's possibly edited speed.
        if data.get("kind")!="render" or "voice_volume" in data.get("render_settings",{}):
            return data
        try:
            snapshot=read_json(self.folder(data["id"])/"input.json")
            speed=snapshot["config"]["voice"].get("speed",1)
            volume=snapshot["config"]["voice"].get("volume",1)
            if type(speed) in {int,float} and .5<=speed<=2:
                return {**data,"render_settings":{**data.get("render_settings",{}),"voice_speed":speed,"voice_volume":volume}}
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            pass
        return data

    def get(self,jid):
        folder=self.folder(jid)
        data=read_json(folder/"job.json")
        if data["status"] in {"queued","running","waiting"} and time.time()-data["created"]>15:
            with (folder/"worker.lock").open("a+") as f:
                try:
                    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    return self.describe(data)
                # No worker holds its lock. A process crash, never reported as success.
                data.update(status="interrupted",message="后台进程已中断；可重新开始，已完成媒体阶段会复用",finished=time.time())
                write_json(folder/"job.json",data)
        return self.describe(data)

    def list(self,pid=None):
        items=[]
        for p in self.root.glob("*/job.json"):
            item=self.get(p.parent.name)
            if pid is None or item.get("project_id")==pid or item.get("kind")=="test":
                items.append(item)
        return sorted(items,key=lambda x:x["created"],reverse=True)[:50]

    def create(self,kind,pid=None,revision=None,scene_id=None,cover=None,online=True):
        with LOCK:
            if kind not in {"plan","render","test","cover","materials"}:
                raise ValueError("不支持的任务类型")
            doc=None
            if kind=="cover":
                from .covers import prepare
                doc=self.ws.get(pid)
                if any(j["kind"]=="cover" and j["status"] in {"queued","running","waiting"} for j in self.list(pid)):
                    raise ValueError("封面正在生成，请等待完成后再开始")
                if not isinstance(cover,dict):
                    raise ValueError("请填写封面设置")
                cover_input=prepare(self.ws,pid,cover.get("revision"),cover.get("settings"))
                revision=doc["revision"]
            if kind in {"plan","render","materials"}:
                doc=self.ws.get(pid)
                if doc["revision"]!=revision:
                    raise ValueError("请先保存并刷新至最新项目版本")
                if any(j["status"] in {"queued","running","waiting"} for j in self.list(pid)):
                    raise ValueError("该项目已有后台任务，请等待完成后再开始")
            if kind in {"plan","test","materials"} and not self.ws.settings()["configured"]:
                raise ValueError("请先配置模型 API；不会用假分镜代替模型结果")
            if kind=="materials":
                if not doc.get("storyboard"):raise ValueError("请先生成分镜，再自动匹配素材")
                if type(online)!=bool:raise ValueError("联网选项不正确")
            if kind=="render":
                if not scene_id and not doc["approved"]:
                    raise ValueError("整片导出前，请先确认知识与分镜已审阅")
                config=self.ws.compile(doc)
                if scene_id:
                    config["scenes"]=[s for s in config["scenes"] if s["id"]==scene_id]
                    config.pop("opening",None)
                    if not config["scenes"]:
                        raise ValueError("未找到这个镜头")
            jid=uuid.uuid4().hex
            folder=self.folder(jid); folder.mkdir()
            data={"id":jid,"kind":kind,"project_id":pid,"revision":revision,"scene_id":scene_id,"created":time.time(),"status":"queued","message":"任务已排队"}
            if kind=="cover":
                data["cover_input"]=cover_input
            if kind=="materials":data["online"]=online
            if kind=="render":
                path=self.ws.path(pid)/f"run-{jid}.json"
                write_json(path,config)
                data["config_path"]=str(path)
                data["render_settings"]={"voice_speed":config["voice"].get("speed",1),"voice_volume":config["voice"].get("volume",1)}
            if doc:
                write_json(folder/"input.json",doc)
            write_json(folder/"job.json",data)
            with (folder/"worker.log").open("wb") as log:
                process=subprocess.Popen([sys.executable,"-m","video_agent.web_worker",str(self.ws.root),jid],cwd=self.ws.root,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            data["pid"]=process.pid
            # Worker owns status from here; never overwrite after spawning.
            return {**data,"pid":process.pid}

    def progress(self,jid):
        data=self.get(jid)
        if data["kind"]=="render" and data["status"] in {"running","waiting"}:
            path=self.folder(jid)/"engine.jsonl"
            events=[]
            if path.exists():
                import json
                for line in path.read_text(errors="replace").splitlines()[-200:]:
                    try:
                        e=json.loads(line)
                        if e.get("event") in {"stage_started","stage_completed","failed"}:
                            events.append(e)
                    except ValueError:
                        pass
            data["progress"]=events
        return data


def work(root,jid):
    ws=Workspace(root);jobs=Jobs(ws);folder=jobs.folder(jid)
    with (folder/"worker.lock").open("a+") as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        data=read_json(folder/"job.json")
        def update(status="running",message="处理中",**more):
            data.update(status=status,message=message,updated=time.time(),**more)
            write_json(folder/"job.json",data)
        try:
            update(message="后台任务已开始")
            if data["kind"]=="test":
                result,usage=call_model(ws.settings(True),[{"role":"system","content":"Return JSON only."},{"role":"user","content":"Return a JSON object with a single key ok and value true."}])
                if result.get("ok") is not True:
                    raise ValueError("模型响应未通过 JSON 连通测试")
                update("completed","模型连接与 JSON 响应测试通过",result={"calls":[usage]})
            elif data["kind"]=="plan":
                doc=read_json(folder/"input.json")
                def validate(story,knowledge):
                    from .planning import validate_plan
                    clean=validate_plan(story,knowledge)
                    ws.compile(doc,clean,knowledge)
                    return clean
                # Generated video titles change after planning. Keep the imported
                # topic stable so the same course/settings can reuse its cache.
                original=ws.path(doc["id"])/"revisions/r0001.json"
                topic=read_json(original)["title"] if original.is_file() else doc["title"]
                result=plan(doc["source"],{**doc["brief"],"topic":topic},ws.settings(True),ws.path(doc["id"])/"planning-cache",validate,
                            lambda stage,message:update(message=message))
                write_json(folder/"planning-result.json",result)
                # Optimistic revision check: never replace edits made during a request.
                try:
                    new=ws.update(doc["id"],data["revision"],{"knowledge":result["knowledge"],"storyboard":result["storyboard"],"planning":{k:v for k,v in result.items() if k not in {"knowledge","storyboard"}}})
                    update("completed","知识和分镜已生成，请逐镜审阅",result={"revision":new["revision"],"calls":result["calls"],"cache_hit":result["cache_hit"],"new_api_calls":result["new_api_calls"]})
                except Exception:
                    update("conflict","模型结果已保存，但项目期间有修改；请下载本次结果后手工合并",result={"file":"planning-result.json","calls":result["calls"]})
            elif data["kind"]=="materials":
                from .materials import run
                doc=read_json(folder/"input.json")
                try:
                    result=run(ws,doc,folder,lambda message:update(message=message),online=data.get("online",True))
                    update("completed",f"已为 {result['applied']} 个分镜放入素材；请预览，不合适可手动替换",result=result,finished=time.time())
                except Exception as exc:
                    from .workspace import Conflict
                    if not isinstance(exc,Conflict):raise
                    usage=read_json(folder/"material-usage.json") if (folder/"material-usage.json").is_file() else {}
                    update("conflict","匹配结果已保留，期间项目有修改；未覆盖新版本",result={"file":"material-result.json",**usage},finished=time.time())
            elif data["kind"]=="cover":
                from .covers import generate
                update("waiting","等待本机制作队列，随后自动生成封面")
                with (ws.web/"media.lock").open("a+") as media_guard:
                    fcntl.flock(media_guard,fcntl.LOCK_EX)
                    update(message="正在排版封面与检查图片尺寸")
                    result=generate(ws,data["project_id"],jid,data["cover_input"])
                    update("completed","封面已生成，可以预览并下载",result=result,finished=time.time())
            else:
                update("waiting","等待本机制作队列；其他镜头任务完成后自动继续")
                with (ws.web/"media.lock").open("a+") as media_guard:
                    fcntl.flock(media_guard,fcntl.LOCK_EX)
                    update(message="正在制作：配音、字幕、画面、混音与技术检查")
                    config=Path(data["config_path"])
                    with (folder/"engine.jsonl").open("wb") as log:
                        process=subprocess.Popen([sys.executable,"-m","video_agent","run",str(config)],cwd=ws.root,stdout=log,stderr=subprocess.STDOUT)
                        code=process.wait()
                    if code:
                        # Engine failures may include arbitrary document paths, but never API settings.
                        tail=(folder/"engine.jsonl").read_text(errors="replace")[-3500:]
                        raise ValueError("制作未完成。可修正后重试，成功阶段会复用。\n"+tail)
                    result=read_json(config.parent/"outputs"/(config.stem+"-latest.json"))
                    out=Path(result["output_directory"])
                    audit=read_json(out/"audit.json")
                    write_json(folder/"render-result.json",result)
                    update("completed","制作完成；技术检查通过，仍可播放检查语气和画面",result={"output_directory":str(out),"files":sorted(p.name for p in out.iterdir() if p.is_file()),"audit":audit,"counts":result["counts"],"source_revision":data["revision"]})
        except BaseException as exc:
            message=str(exc)
            key=ws.settings(True).get("api_key","")
            if key:
                message=message.replace(key,"[已隐藏]")
            extra={}
            if data.get("kind")=="materials" and (folder/"material-usage.json").is_file():
                extra["result"]=read_json(folder/"material-usage.json")
            update("failed",message[:4500],finished=time.time(),**extra)
