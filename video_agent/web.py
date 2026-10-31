"""Loopback-only workbench. Standard library, no dependency installation."""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .engine import ROOT
from .jobs import Jobs
from .workspace import Conflict, Workspace


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,root=ROOT):
        self.ws=Workspace(root);self.jobs=Jobs(self.ws);self.token=secrets.token_urlsafe(32)
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"

    def log_message(self,*args):
        pass  # Do not log URLs or settings payloads.

    def headers_ok(self,write=False):
        port=self.server.server_port
        if self.headers.get("Host") not in {f"127.0.0.1:{port}",f"localhost:{port}"}:
            self.send_json({"error":"仅允许本机访问"},403);return False
        origin=self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{port}",f"http://localhost:{port}"}:
            self.send_json({"error":"请求来源不匹配"},403);return False
        if write and not secrets.compare_digest(self.headers.get("X-Workspace-Token",""),self.server.token):
            self.send_json({"error":"页面会话已过期，请刷新后再试"},403);return False
        return True

    def send_bytes(self,body,status=200,kind="application/json; charset=utf-8",extra=None):
        self.send_response(status)
        self.send_header("Content-Type",kind)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k,v in (extra or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(body)

    def send_json(self,data,status=200):
        self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),status)

    def body(self):
        size=int(self.headers.get("Content-Length","0"))
        if not 0<size<=2_000_000:
            raise ValueError("请求内容为空或超过 2 MB")
        if self.headers.get("Content-Type","").split(";")[0]!="application/json":
            raise ValueError("请使用 JSON 请求")
        data=json.loads(self.rfile.read(size))
        if not isinstance(data,dict):raise ValueError("请求必须为 JSON 对象")
        return data

    def do_GET(self):
        if not self.headers_ok():return
        try:
            path=urllib.parse.urlsplit(self.path).path
            ws=self.server.ws;jobs=self.server.jobs
            if path=="/api/bootstrap":
                return self.send_json({"token":self.server.token,"settings":ws.settings(),"projects":ws.list(),"version":"0.2 · 本地创作工作台"})
            if path=="/api/projects":return self.send_json(ws.list())
            if path=="/api/settings":return self.send_json(ws.settings())
            if path=="/api/library":
                from .library import catalogue
                return self.send_json({"items":catalogue(ws)})
            library_match=re.fullmatch(r"/api/library/([a-f0-9]{24})/file",path)
            if library_match:
                from .library import resource
                item=resource(ws,library_match[1])
                return self.stream_file(item["_path"],item["_path"].name)
            if path=="/api/jobs":
                pid=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("project",[None])[0]
                return self.send_json(jobs.list(pid))
            cover_match=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/cover(?:/images/([a-f0-9]{64}))?",path)
            if cover_match:
                from . import covers
                if cover_match[2]:
                    return self.stream_file(covers.image_path(ws,cover_match[1],cover_match[2]),"image.png")
                return self.send_json(covers.load(ws,cover_match[1]))
            opening_match=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/openings",path)
            if opening_match:
                from .openings import latest
                return self.send_json(latest(ws,opening_match[1]))
            asset_match=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/assets/([A-Za-z0-9_-]+)",path)
            if asset_match:
                from .util import resolve_path
                doc=ws.get(asset_match[1]);registered=doc["config"].get("assets",{})
                if asset_match[2] not in registered:return self.send_json({"error":"未登记这个素材"},404)
                image=resolve_path(ws.path(doc["id"]),registered[asset_match[2]])
                if image.suffix.lower() not in {".png",".jpg",".jpeg",".webp",".gif",".svg",".mp4",".mov",".webm",".wav",".mp3",".m4a",".aac",".ogg",".flac"}:return self.send_json({"error":"该素材不支持预览"},400)
                return self.stream_file(image, image.name)
            m=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})(/worksheet.md)?",path)
            if m:
                doc=ws.get(m[1])
                if m[2]:return self.send_bytes(ws.export_md(doc).encode(),kind="text/markdown; charset=utf-8",extra={"Content-Disposition":"attachment; filename=worksheet.md"})
                return self.send_json(doc)
            m=re.fullmatch(r"/api/jobs/([a-f0-9]{32})(?:/files/([A-Za-z0-9_.-]+))?",path)
            if m:
                job=jobs.progress(m[1])
                if m[2]:return self.job_file(job,m[2])
                return self.send_json(job)
            static={"/":"index.html","/app.js":"app.js","/library.js":"library.js","/opening.js":"opening.js","/style.css":"style.css",
                    "/player.html":"player.html","/player.js":"player.js","/player.css":"player.css"}
            if path not in static:return self.send_json({"error":"页面不存在"},404)
            p=ROOT/"web"/static[path]
            return self.send_bytes(p.read_bytes(),kind=mimetypes.guess_type(p.name)[0]+"; charset=utf-8")
        except FileNotFoundError:return self.send_json({"error":"文件或项目不存在"},404)
        except (ValueError,KeyError,TypeError) as exc:return self.send_json({"error":str(exc)},400)
        except (BrokenPipeError,ConnectionResetError):return
        except Exception:return self.send_json({"error":"服务内部错误，未完成此操作；请刷新后重试"},500)

    def do_POST(self):
        if not self.headers_ok(True):return
        try:
            path=urllib.parse.urlsplit(self.path).path
            if path=="/api/library/upload":return self.upload(None,library=True)
            cover_upload=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/cover/upload",path)
            if cover_upload:
                return self.upload(cover_upload[1],cover=True)
            upload=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/upload",path)
            if upload:
                return self.upload(upload[1])
            data=self.body();ws=self.server.ws;jobs=self.server.jobs
            library_match=re.fullmatch(r"/api/library/([a-f0-9]{24})/(label|project)",path)
            if library_match:
                from .library import annotate,resource
                if library_match[2]=="label":return self.send_json(annotate(ws,library_match[1],data))
                item=resource(ws,library_match[1])
                if item["category"]!="md":raise ValueError("请选择MD文档来新建项目")
                return self.send_json(ws.create({"source":item["_path"].read_text(),"source_name":item["name"],"title":data.get("title") or Path(item["name"]).stem}),201)
            attach_match=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/library",path)
            if attach_match:
                from .library import attach
                return self.send_json(attach(ws,attach_match[1],data.get("revision"),data.get("ids")))
            cover_match=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/cover",path)
            if cover_match:
                return self.send_json(jobs.create("cover",cover_match[1],cover=data),202)
            opening_match=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/openings",path)
            if opening_match:
                return self.send_json(jobs.create("opening",opening_match[1],data.get("revision"),opening=data),202)
            if path=="/api/settings":return self.send_json(ws.save_settings(data))
            if path=="/api/settings/test":return self.send_json(jobs.create("test"),202)
            if path=="/api/projects":return self.send_json(ws.create(data),201)
            if path=="/api/sample":return self.send_json(ws.sample(),201)
            m=re.fullmatch(r"/api/projects/(web-[a-f0-9]{12})/(save|approve|plan|render|materials)",path)
            if m:
                pid,action=m[1],m[2]
                if action=="save":return self.send_json(ws.update(pid,data.get("revision"),data))
                if action=="approve":return self.send_json(ws.approve(pid,data.get("revision")))
                return self.send_json(jobs.create(action,pid,data.get("revision"),data.get("scene_id"),online=data.get("online",True)),202)
            return self.send_json({"error":"操作不存在"},404)
        except Conflict as exc:return self.send_json({"error":str(exc)},409)
        except FileNotFoundError:return self.send_json({"error":"项目不存在"},404)
        except (ValueError,KeyError,TypeError,AttributeError) as exc:return self.send_json({"error":str(exc)},400)
        except (BrokenPipeError,ConnectionResetError):return
        except Exception:return self.send_json({"error":"服务内部错误，未完成此操作；请刷新后重试"},500)

    def job_file(self,job,filename):
        result=job.get("result",{})
        if filename in {"planning-result.json","material-result.json"} and job["status"]=="conflict":
            p=self.server.jobs.folder(job["id"])/filename
        else:
            if job["status"]!="completed" or filename not in result.get("files",[]):
                return self.send_json({"error":"这个任务没有该产物"},404)
            p=(Path(result["output_directory"])/filename).resolve()
            allowed=(self.server.ws.path(job["project_id"])/"outputs").resolve()
            if not p.is_relative_to(allowed):raise ValueError("产物路径越界")
        return self.stream_file(p,filename)

    def upload(self,pid,cover=False,library=False):
        from .uploads import MAX_UPLOAD, register
        if cover:
            from .covers import MAX_COVER_UPLOAD, register
            MAX_UPLOAD=MAX_COVER_UPLOAD
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        size=int(self.headers.get("Content-Length","0"))
        if self.headers.get("Transfer-Encoding") or not 0<size<=MAX_UPLOAD:
            self.close_connection=True
            raise ValueError("封面图片需在 20 MB 以内" if cover else "单个素材需在 100 MB 以内")
        revision=int(query.get("revision",["0"])[0])
        filename=query.get("name",[""])[0]
        category=query.get("kind",[""])[0]
        ws=self.server.ws
        if not library:ws.path(pid)  # Validate project identity before any disk write.
        with tempfile.TemporaryDirectory(prefix="media-upload-",dir=ws.web) as folder:
            source=Path(folder)/("source"+Path(filename).suffix.lower())
            self.connection.settimeout(60)
            with source.open("wb") as f:
                left=size
                while left:
                    block=self.rfile.read(min(65536,left))
                    if not block:raise ValueError("素材上传中断，请重试")
                    f.write(block);left-=len(block)
            if library:
                from .library import register as library_register
                result=library_register(ws,source,filename,category)
            else:result=register(ws,pid,revision,source,filename) if cover else register(ws,pid,revision,source,filename,category)
        return self.send_json(result,201)

    def stream_file(self,p,filename):
        total=p.stat().st_size
        start,end=0,total-1;status=200
        value=self.headers.get("Range")
        if value:
            match=re.fullmatch(r"bytes=(\d*)-(\d*)",value)
            if not match or not any(match.groups()):return self.send_bytes(b"",416,extra={"Content-Range":f"bytes */{total}"})
            if not match[1]:start=max(0,total-int(match[2]))
            else:start=int(match[1]);end=min(end,int(match[2]) if match[2] else end)
            if start>end or start>=total:return self.send_bytes(b"",416,extra={"Content-Range":f"bytes */{total}"})
            status=206
        self.send_response(status)
        self.send_header("Content-Type",mimetypes.guess_type(filename)[0] or "application/octet-stream")
        self.send_header("Content-Length",str(end-start+1))
        self.send_header("Accept-Ranges","bytes")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Cache-Control","no-store")
        # Direct media navigation creates a browser document that must load its
        # own audio/video URL. Keep other files (especially SVG) fully sandboxed.
        kind=mimetypes.guess_type(filename)[0] or "application/octet-stream"
        policy="default-src 'none'; sandbox"
        if kind.startswith(("video/","audio/")):
            policy="default-src 'none'; media-src 'self'; sandbox allow-same-origin"
        self.send_header("Content-Security-Policy",policy)
        if filename.endswith((".json",".srt")):self.send_header("Content-Disposition",f'attachment; filename="{filename}"')
        if status==206:self.send_header("Content-Range",f"bytes {start}-{end}/{total}")
        self.end_headers()
        with p.open("rb") as f:
            f.seek(start);left=end-start+1
            while left:
                block=f.read(min(left,65536))
                if not block:break
                self.wfile.write(block);left-=len(block)


def serve(port=8765):
    server=Server(("127.0.0.1",port))
    print(f"本地工作台：http://127.0.0.1:{server.server_port}",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
