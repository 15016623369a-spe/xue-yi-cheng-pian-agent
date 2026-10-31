"""Persistent editable projects; revision checks keep concurrent tabs honest."""
from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

from .engine import ROOT
from .planning import DEFAULT_BRIEF, endpoint, validate_knowledge, validate_plan
from .schema import load_project
from .util import digest, read_json, resolve_path, write_json

LOCK=threading.RLock()


class Conflict(ValueError):
    pass


class Workspace:
    def __init__(self, root=ROOT):
        self.root=Path(root).resolve()
        self.web=self.root/".web"
        self.web.mkdir(exist_ok=True)
        self.projects=self.root/"projects"
        self.projects.mkdir(exist_ok=True)

    def path(self,pid):
        if not isinstance(pid,str) or not re.fullmatch(r"web-[a-f0-9]{12}",pid):
            raise ValueError("无效项目编号")
        p=(self.projects/pid).resolve()
        if not p.is_relative_to(self.projects.resolve()):
            raise ValueError("项目路径越界")
        return p

    @contextmanager
    def locked_project(self,pid):
        with LOCK:
            with (self.path(pid)/"workspace.lock").open("a+") as f:
                fcntl.flock(f,fcntl.LOCK_EX)
                try:yield
                finally:fcntl.flock(f,fcntl.LOCK_UN)

    def get(self,pid):
        with LOCK:
            p=self.path(pid)
            return read_json(p/"workspace.json")

    def list(self):
        items=[]
        with LOCK:
            for p in self.projects.glob("web-*/workspace.json"):
                d=read_json(p)
                items.append({k:d.get(k) for k in ["id","title","revision","updated","created","approved","scene_count"]})
        return sorted(items,key=lambda x:x["updated"],reverse=True)

    def settings(self, private=False):
        p=self.web/"model.json"
        d=read_json(p) if p.exists() else {}
        if private:
            return d
        return {**{k:v for k,v in d.items() if k!="api_key"},"has_key":bool(d.get("api_key")),"configured":bool(d.get("api_key") and d.get("base_url") and d.get("model"))}

    def preferences(self):
        p=self.web/"creation.json"
        saved=read_json(p) if p.is_file() else {}
        return {"series_name":saved.get("series_name",""),"auto_clean_online":saved.get("auto_clean_online",True)}

    def save_preferences(self,data):
        with LOCK:
            old=self.preferences()
            series=data.get("series_name",old["series_name"])
            clean=data.get("auto_clean_online",old["auto_clean_online"])
            if not isinstance(series,str) or len(series.strip())>40 or any(ord(c)<32 for c in series):
                raise ValueError("系列名最多40字，不能含换行")
            if type(clean)!=bool:raise ValueError("自动清理选项格式不正确")
            result={"series_name":series.strip(),"auto_clean_online":clean}
            write_json(self.web/"creation.json",result)
            return result

    def save_settings(self,data):
        with LOCK:
            old=self.settings(True)
            base=data.get("base_url","").strip()
            endpoint(base)
            if not data.get("model","").strip():
                raise ValueError("请填写模型名")
            maximum=data.get("max_output_tokens",8000)
            if type(maximum)!=int or not 256<=maximum<=16000:
                raise ValueError("输出 token 上限需在 256–16000 之间")
            if data.get("token_parameter","max_tokens") not in {"max_tokens","max_completion_tokens"}:
                raise ValueError("无效输出上限参数")
            key=data.get("api_key","").strip() or old.get("api_key","")
            if data.get("clear_key"):
                key=""
            if any(c in key for c in "\r\n"):
                raise ValueError("密钥格式不正确")
            result={"base_url":base,"model":data["model"].strip(),"api_key":key,"json_mode":bool(data.get("json_mode",True)),"max_output_tokens":maximum,"token_parameter":data.get("token_parameter","max_tokens")}
            for k in ["input_price","output_price"]:
                v=data.get(k)
                if v is not None and (not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) or v<0):
                    raise ValueError("模型单价需为非负数，或留空")
                result[k]=v
            p=self.web/"model.json"
            # Create both temporary and final credential files owner-only.
            temp=p.with_suffix(".secret-tmp")
            fd=os.open(temp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
            os.fchmod(fd,0o600)
            with os.fdopen(fd,"w") as f:
                json.dump(result,f,ensure_ascii=False)
                f.flush(); os.fsync(f.fileno())
            os.replace(temp,p)
        return self.settings()

    def preset(self,base):
        original=self.root/"projects/lesson04-validation/project.json"
        private_fixture=original.is_file()
        # The author's reviewed media fixture stays private. A fresh checkout
        # can import/edit notes without that fixture or any installed models.
        if not private_fixture:
            original=self.root/"config/project.example.json"
        config=read_json(original)
        for k in ["reference_audio","reference_text"]:
            config["voice"][k]=str(resolve_path(original.parent,config["voice"][k]))
        config["assets"]={k:str(resolve_path(original.parent,v)) for k,v in config["assets"].items()}
        config["voice"]["conditioning"]="icl"
        default_voice=self.root/"config/default-voice.json"
        if default_voice.is_file():
            config["voice"].update(read_json(default_voice))
        config["source"]="handoff.md"
        config["scenes"]=[]
        config["title"]="新学习视频"
        # A visual/voice preset does not select a course or its spoken opening.
        config["theme"]={"label":"学习分享","series":self.preferences()["series_name"] or "本期内容","side_label":"本期重点"}
        config.pop("opening",None)
        return config

    def create(self,data):
        source=data.get("source","")
        if not isinstance(source,str) or not source.strip() or len(source)>50_000:
            raise ValueError("请导入非空 MD（最多 5 万字）")
        pid="web-"+uuid.uuid4().hex[:12]
        base=self.path(pid);base.mkdir()
        (base/"handoff.md").write_text(source,encoding="utf-8")
        now=time.time()
        doc={"id":pid,"title":str(data.get("title","新学习视频"))[:120],"revision":1,"created":now,"updated":now,"approved":False,"scene_count":0,
             "source":source,"source_name":Path(str(data.get("source_name","handoff.md"))).name,
             "brief":copy.deepcopy(DEFAULT_BRIEF),"knowledge":None,"storyboard":None,"config":self.preset(base),"review":{},"planning":None}
        doc["config"]["project_id"]=pid
        self._write(base,doc)
        return doc

    def sample(self):
        original=self.root/"projects/lesson04-validation/project.json"
        if not original.is_file():
            return self._public_sample()
        config=read_json(original)
        source=(original.parent/"source.md").read_text()
        doc=self.create({"title":"第四期 · 三镜交互练习","source":source,"source_name":"Lesson04-source.md"})
        facts=[]
        matches=["**FFN 的形状为 D → D_ff → D。**", "**两次残差相加都保留原旁路，且发生在 [B,T,D] 上。**", "**nn.LayerNorm(D) 对每个 Token 的 D 个特征独立计算均值和方差。**"]
        for i,needle in enumerate(matches):
            # Exact, existing classroom source. This is a reviewed fixture, not model extraction.
            line=next((n+1 for n,l in enumerate(source.splitlines()) if needle in l),None)
            if line is None:
                raise ValueError("示例课程来源发生变化，无法定位引用")
            facts.append({"id":f"K{i+1:02}","claim":["FFN 只改变中间特征宽度","残差保留原旁路","LayerNorm 对同一 Token 的特征计算统计量"][i],"boundaries":["示例只选三项知识，不代表第四期完整课堂"],"evidence":[{"start_line":line,"end_line":line,"quote":source.splitlines()[line-1]}]})
        scenes=[]
        for i,s in enumerate(config["scenes"]):
            s=copy.deepcopy(s)
            for k in ["audio","asr"]:
                s["import"][k]=str(resolve_path(original.parent,s["import"][k]))
            s["knowledge_ids"]=[f"K{i+1:02}"]
            s["analogy"]={"mapping":"沿用已审核的第四期三镜讲解","boundary":"战术比喻辅助理解；以原始张量定义为准"}
            scenes.append(s)
        doc["knowledge"]={"title":doc["title"],"facts":facts,"questions":[]}
        doc["storyboard"]={"title":doc["title"],"scenes":scenes,"publishing_copy":"工具交互验证片；配音为参考音色合成，非人物本人讲授。"}
        doc["planning"]={"method":"reviewed_existing_fixture","new_api_calls":0}
        # The practice fixture's registered speech must keep its original voice
        # signature even when the user's default reference is changed later.
        doc["config"]["voice"]=copy.deepcopy(config["voice"])
        for k in ["reference_audio","reference_text"]:
            doc["config"]["voice"][k]=str(resolve_path(original.parent,config["voice"][k]))
        doc["config"].pop("opening",None)  # validation fragment, not a formal new episode
        doc["scene_count"]=3
        self._write(self.path(doc["id"]),doc)
        return doc

    def _public_sample(self):
        """Editable, text-only example; no API calls or bundled speaker media."""
        source=(self.root/"examples/handoff.md").read_text(encoding="utf-8")
        story=read_json(self.root/"examples/storyboard.json")
        doc=self.create({"title":story["title"],"source":source,"source_name":"handoff.md"})
        facts=[]
        for i,quote in enumerate(story.pop("source_quotes"),1):
            line=source.splitlines().index(quote)+1
            facts.append({"id":f"K{i:02}","claim":quote,"boundaries":["本示例说明本项目的缓存机制，不代表所有软件的实现。"],
                          "evidence":[{"start_line":line,"end_line":line,"quote":quote}]})
        doc["knowledge"]=validate_knowledge({"title":doc["title"],"facts":facts,"questions":[]},source)
        doc["storyboard"]=validate_plan(story,doc["knowledge"])
        doc["planning"]={"method":"public_text_example","new_api_calls":0}
        doc["scene_count"]=len(doc["storyboard"]["scenes"])
        self._write(self.path(doc["id"]),doc)
        return doc

    def _write(self,base,doc):
        write_json(base/"workspace.json",doc)
        write_json(base/"revisions"/f"r{doc['revision']:04}.json",doc)

    def compile(self,doc,storyboard=None,knowledge=None):
        story=copy.deepcopy(storyboard if storyboard is not None else doc["storyboard"])
        facts=knowledge if knowledge is not None else doc["knowledge"]
        validate_knowledge(facts,doc["source"])
        clean=validate_plan(story,facts)
        config=copy.deepcopy(doc["config"])
        config["title"]=clean["title"]
        config["scenes"]=clean["scenes"]
        for i,s in enumerate(config["scenes"]):
            # Preserve a registered import only from this project revision, never model output.
            old=next((x for x in (doc.get("storyboard") or {}).get("scenes",[]) if x["id"]==s["id"]),{})
            s["seed"]=old.get("seed",500+i)
            if old.get("import"):
                s["import"]=old["import"]
            s["knowledge_refs"]=[{"source":"handoff.md","knowledge_id":k,"evidence":next(f["evidence"] for f in facts["facts"] if f["id"]==k)} for k in s["knowledge_ids"]]
        self.validate_config(self.path(doc["id"]),config)
        return config

    def validate_config(self,base,config):
        target=base/(".validate-"+uuid.uuid4().hex+".json")
        try:
            write_json(target,config)
            load_project(target)
        finally:
            target.unlink(missing_ok=True)

    def update(self,pid,revision,changes):
        with self.locked_project(pid):
            doc=self.get(pid)
            if type(revision)!=int or doc["revision"]!=revision:
                raise Conflict("项目已被另一页面或任务更新。请刷新后重新修改；当前修改未覆盖新版本。")
            for key in ["brief","knowledge","storyboard"]:
                if key in changes:
                    doc[key]=copy.deepcopy(changes[key])
            if not isinstance(doc["brief"],dict) or not all(isinstance(doc["brief"].get(k),str) for k in ["audience","direction","style"]):
                raise ValueError("受众与表达方向格式不正确")
            seconds=doc["brief"].get("seconds")
            if type(seconds)!=int or not 20<=seconds<=240:
                raise ValueError("目标时长需为 20–240 秒")
            doc["config"]["theme"]=doc["config"].get("theme") or {"label":"学习分享","series":"本期内容","side_label":"本期重点"}
            if "series_name" in changes:
                series=changes["series_name"]
                if not isinstance(series,str) or len(series.strip())>40 or any(ord(c)<32 for c in series):
                    raise ValueError("系列名最多40字，不能含换行")
                doc["config"]["theme"]["series"]=series.strip()
            if "episode_title" in changes:
                title=changes["episode_title"]
                if not isinstance(title,str) or not 1<=len(title.strip())<=120 or any(ord(c)<32 for c in title):
                    raise ValueError("本期标题需为1–120字，不能含换行")
                doc["title"]=title.strip();doc["config"]["title"]=title.strip()
                if doc.get("storyboard"):doc["storyboard"]["title"]=title.strip()
            if doc["knowledge"]:
                validate_knowledge(doc["knowledge"],doc["source"])
            if "media" in changes:
                m=changes["media"]
                if type(m.get('forget_opening',False))!=bool:raise ValueError('移除片头选项不正确')
                if m.get('forget_opening') and m.get('opening'):raise ValueError('不能同时启用和移除片头')
                speed=m.get("speed",1.18);volume=m.get("bgm_db",-24)
                voice_volume=m.get("voice_volume",doc["config"]["voice"].get("volume",1))
                if not isinstance(speed,(int,float)) or isinstance(speed,bool) or not .5<=speed<=2:
                    raise ValueError("语速需在 0.5–2 之间")
                if not isinstance(volume,(int,float)) or isinstance(volume,bool) or not -60<=volume<=-6:
                    raise ValueError("背景音乐音量需在 -60 到 -6 dB 之间")
                if not isinstance(voice_volume,(int,float)) or isinstance(voice_volume,bool) or not math.isfinite(voice_volume) or not 0<=voice_volume<=2:
                    raise ValueError("人声音量需在 0%–200% 之间")
                doc["config"]["voice"]["speed"]=speed
                doc["config"]["voice"]["volume"]=voice_volume
                doc["config"]["bgm"]={"asset":"bgm","relative_db":volume} if m.get("bgm",True) else {}
                if m.get("opening",False):
                    opening=doc["config"].get("opening") or doc.get("opening_selection")
                    if not opening:
                        raise ValueError("尚未选择本期片头，请先查找并接入素材")
                    doc["config"]["opening"]=opening
                else:
                    if doc["config"].get("opening"):
                        doc["opening_selection"]=copy.deepcopy(doc["config"]["opening"])
                    doc["config"].pop("opening",None)
                if m.get('forget_opening'):
                    doc.pop('opening_selection',None)
                    if doc['config'].get('theme',{}).get('series')=='Transformer 基础':
                        doc['config']['theme']={"label":"学习分享","series":"本期内容","side_label":"本期重点"}
            if doc.get("storyboard"):
                doc["config"]=self.compile(doc)
                doc["title"]=doc["storyboard"]["title"]
                doc["scene_count"]=len(doc["storyboard"]["scenes"])
            doc["approved"]=False
            doc["review"]={}
            if "planning" in changes:
                doc["planning"]=changes["planning"]
            doc["revision"]+=1; doc["updated"]=time.time()
            self._write(self.path(pid),doc)
            return doc

    def approve(self,pid,revision):
        with self.locked_project(pid):
            doc=self.get(pid)
            if doc["revision"]!=revision:
                raise Conflict("版本已改变，请刷新并重新审阅")
            if not doc.get("storyboard"):
                raise ValueError("请先生成或导入分镜")
            doc["config"]=self.compile(doc)
            doc["approved"]=True
            doc["review"]={"revision":revision,"at":time.time(),"basis":"interactive_review_confirmation","config_sha256":digest(doc["config"])}
            self._write(self.path(pid),doc)
            return doc

    def export_md(self,doc):
        text=[f"# {doc['title']}",f"系列：{doc['config'].get('theme',{}).get('series','')}",f"版本：{doc['revision']}；人工审阅：{'已确认' if doc['approved'] else '待审阅'}", "\n## 受众与方向",doc["brief"]["audience"],doc["brief"]["direction"]]
        for s in (doc.get("storyboard") or {}).get("scenes",[]):
            text += [f"\n## {s['id']}：{s['title']}",f"知识引用：{', '.join(s['knowledge_ids'])}",f"口播：{s['speech']}",f"类比：{s['analogy']['mapping']}",f"边界：{s['analogy']['boundary']}","```json",json.dumps({"visual":s["visual"],"events":s.get("events",[]),"pronunciations":s.get("pronunciations",{})},ensure_ascii=False,indent=2),"```"]
        text += ["\n## 发布文案",(doc.get("storyboard") or {}).get("publishing_copy","")]
        return "\n\n".join(text)
