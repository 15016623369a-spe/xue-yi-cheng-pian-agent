from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from video_agent.engine import ROOT, voice_signature_for
from video_agent.jobs import Jobs
from video_agent.planning import call_model, endpoint, plan, validate_knowledge, validate_plan, locate_evidence, tls_context, ModelOutputError
from video_agent.web import Server
from video_agent.workspace import Conflict, Workspace


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/"projects").mkdir()
        (self.root/"projects/lesson04-validation").symlink_to(ROOT/"projects/lesson04-validation",target_is_directory=True)
        self.ws=Workspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_default_voice_does_not_change_registered_practice_audio(self):
        (self.root/'config').mkdir()
        (self.root/'config/default-voice.json').write_text(json.dumps({
            'profile':'new-reference','conditioning':'icl'}))
        self.assertEqual(self.ws.preset(self.root)['voice']['profile'],'new-reference')
        doc=self.ws.sample()
        for scene in doc['config']['scenes']:
            self.assertEqual(scene['import']['voice_signature'],
                             voice_signature_for(doc['config']['voice'],self.ws.path(doc['id'])))

    def test_key_is_owner_only_and_never_returned(self):
        result=self.ws.save_settings({"base_url":"https://example.com/v1","model":"test","api_key":"TEST_NOT_A_REAL_SECRET"})
        self.assertNotIn("api_key",result)
        self.assertTrue(result["has_key"])
        self.assertEqual(os.stat(self.ws.web/"model.json").st_mode&0o777,0o600)
        self.ws.save_settings({"base_url":"https://example.com/v1","model":"test","api_key":""})
        self.assertEqual(self.ws.settings(True)["api_key"],"TEST_NOT_A_REAL_SECRET")
        self.ws.save_settings({"base_url":"https://example.com/v1","model":"test","clear_key":True})
        self.assertFalse(self.ws.settings()["has_key"])

    def test_remote_http_and_key_in_url_rejected(self):
        for address in ["http://remote.example/v1","https://user:key@example.com","https://example.com?key=foo","file:///tmp/key"]:
            with self.assertRaises(ValueError):endpoint(address)
        self.assertEqual(endpoint("https://example.com/v1"),"https://example.com/v1/chat/completions")

    def test_unique_exact_quote_relocates_without_fuzzy_matching(self):
        d={"facts":[{"evidence":[{"start_line":99,"end_line":99,"quote":"**准确原文**"}]}]}
        fixed=locate_evidence(d,"开头\n\n**准确原文**\n结尾")
        self.assertEqual(fixed["facts"][0]["evidence"][0]["start_line"],3)
        self.assertEqual(d["facts"][0]["evidence"][0]["start_line"],99)
        self.assertEqual(locate_evidence(d,"**准确原文**\n**准确原文**"),d)
        self.assertEqual(locate_evidence(d,"准确原文"),d)

    def test_tls_keeps_certificate_and_hostname_validation(self):
        import ssl
        context=tls_context()
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(len(context.get_ca_certs()),0)

    def test_source_quotations_must_match(self):
        doc=self.ws.sample()
        validate_knowledge(doc["knowledge"],doc["source"])
        doc["knowledge"]["facts"][0]["evidence"][0]["quote"]="编造出来的来源"
        with self.assertRaises(ValueError):validate_knowledge(doc["knowledge"],doc["source"])

    def test_revision_conflicts_and_approval_invalidation(self):
        doc=self.ws.sample();pid=doc["id"]
        self.assertTrue(self.ws.approve(pid,1)["approved"])
        changed=copy.deepcopy(doc["storyboard"])
        changed["scenes"][0]["title"]="只改标题，也要重新确认"
        current=self.ws.update(pid,1,{"storyboard":changed})
        self.assertFalse(current["approved"])
        self.assertEqual(current["revision"],2)
        with self.assertRaises(Conflict):self.ws.update(pid,1,{"storyboard":doc["storyboard"]})
        self.assertTrue((self.ws.path(pid)/"revisions/r0001.json").is_file())

    def test_pronunciation_survives_save_compile_and_worksheet(self):
        doc=self.ws.sample();pid=doc['id']
        self.ws.approve(pid,1)
        story=copy.deepcopy(doc['storyboard'])
        first=story['scenes'][0]
        source=first['speech'][:2]
        first['pronunciations']={source:'测试读法'}
        changed=self.ws.update(pid,1,{'storyboard':story})
        self.assertFalse(changed['approved'])
        reloaded=self.ws.get(pid)
        self.assertEqual(reloaded['storyboard']['scenes'][0]['pronunciations'],{source:'测试读法'})
        self.assertEqual(self.ws.compile(reloaded)['scenes'][0]['pronunciations'],{source:'测试读法'})
        self.assertEqual(reloaded['storyboard']['scenes'][0]['speech'],doc['storyboard']['scenes'][0]['speech'])
        self.assertIn('pronunciations',self.ws.export_md(reloaded))

    def test_anchor_error_cannot_be_saved(self):
        d=self.ws.sample();story=copy.deepcopy(d["storyboard"])
        story["scenes"][0]["speech"]="新讲稿已没有原有事件锚点。"
        with self.assertRaises(ValueError):self.ws.update(d["id"],1,{"storyboard":story})
        self.assertEqual(self.ws.get(d["id"])["revision"],1)

    def test_model_cannot_supply_media_paths_or_imports(self):
        d=self.ws.sample();story=copy.deepcopy(d["storyboard"])
        story["scenes"][0]["import"]={"audio":"/private/key"}
        clean=validate_plan(story,d["knowledge"])
        self.assertNotIn("import",clean["scenes"][0])
        self.assertNotIn("seed",clean["scenes"][0])

    def test_figure_requires_registered_asset(self):
        d=self.ws.sample();story=copy.deepcopy(d["storyboard"])
        story["scenes"][0]["visual"]={"template":"figure","asset":"reaction","caption":"已登记素材"}
        self.ws.compile(d,story)
        story["scenes"][0]["visual"]["asset"]="/private/unregistered"
        with self.assertRaises(ValueError):self.ws.compile(d,story)

    def test_uncovered_knowledge_is_rejected(self):
        d=self.ws.sample();story=copy.deepcopy(d["storyboard"]);story["scenes"]=story["scenes"][:1]
        with self.assertRaises(ValueError):validate_plan(story,d["knowledge"])

    def test_render_requires_review_but_single_scene_does_not_start_on_invalid_id(self):
        d=self.ws.sample();jobs=Jobs(self.ws)
        with self.assertRaisesRegex(ValueError,"审阅"):jobs.create("render",d["id"],1)
        with self.assertRaisesRegex(ValueError,"未找到"):jobs.create("render",d["id"],1,"UNKNOWN")

    def test_project_paths_cannot_escape(self):
        for pid in ["../outside","web-123/../../","foo",None]:
            with self.assertRaises(ValueError):self.ws.path(pid)

    def test_model_repair_and_cache_integrity(self):
        d=self.ws.sample();bad=copy.deepcopy(d["knowledge"]);bad["facts"][0]["evidence"][0]["quote"]="假引用内容"
        outputs=[bad,d["knowledge"],d["storyboard"]]
        def fake(*args):return copy.deepcopy(outputs.pop(0)),{"usage":{"total_tokens":20},"model":"test"}
        def validate(story,k):
            clean=validate_plan(story,k);self.ws.compile(d,clean,k);return clean
        cache=self.root/"cache"
        with patch("video_agent.planning.call_model",side_effect=fake) as model:
            result=plan(d["source"],d["brief"],{"model":"test"},cache,validate)
            self.assertEqual(model.call_count,3)
            self.assertEqual(result["new_api_calls"],3)
        with patch("video_agent.planning.call_model",side_effect=AssertionError("No new request expected")):
            cached=plan(d["source"],d["brief"],{"model":"test"},cache,validate)
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(cached["new_api_calls"],0)
        target=next(p for p in cache.glob("*.json") if not p.name.endswith("usage.json"))
        stored=json.loads(target.read_text());stored["result"]["knowledge"]["title"]="tampered"
        target.write_text(json.dumps(stored))
        with patch("video_agent.planning.call_model",side_effect=[(d["knowledge"],{}),(d["storyboard"],{})]) as model:
            plan(d["source"],d["brief"],{"model":"test"},cache,validate)
            self.assertEqual(model.call_count,2)

    def test_model_repair_is_bounded(self):
        d=self.ws.sample()
        with patch("video_agent.planning.call_model",return_value=({},{})) as model:
            with self.assertRaises(ValueError):plan(d["source"],d["brief"],{},self.root/"cache",lambda d,k:d)
            self.assertEqual(model.call_count,2)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        (root/"projects").mkdir()
        (root/"projects/lesson04-validation").symlink_to(ROOT/"projects/lesson04-validation",target_is_directory=True)
        self.server=Server(("127.0.0.1",0),root)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.tmp.cleanup()

    def request(self,path,data=None,headers=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers={"Content-Type":"application/json",**(headers or {})})
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req)

    def test_bootstrap_and_csrf(self):
        with self.request('/api/bootstrap') as r:
            b=json.load(r);self.assertNotIn("api_key",b["settings"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/api/projects',{"source":"课程"})
        self.assertEqual(ctx.exception.code,403)
        with self.request('/api/projects',{"source":"课程","title":"真实导入"},{"X-Workspace-Token":b["token"]}) as r:
            doc=json.load(r)
        self.assertEqual(doc["source"],"课程")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request('/api/projects',{"source":"课程"},{"X-Workspace-Token":b["token"],"Origin":"https://untrusted.example"})
        self.assertEqual(ctx.exception.code,403)

    def test_host_rebinding_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/api/bootstrap',headers={"Host":"remote.example"})
        self.assertEqual(ctx.exception.code,403)

    def test_unknown_media_cannot_read_local_files(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/api/jobs/'+'a'*32+'/files/model.json')
        self.assertEqual(ctx.exception.code,404)


class ProviderTests(unittest.TestCase):
    def test_local_http_provider_protocol(self):
        received=[]
        class Fake(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.path,data,self.headers.get("Authorization")))
                body=json.dumps({"choices":[{"message":{"content":"{\"ok\":true}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}).encode()
                self.send_response(200);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(("127.0.0.1",0),Fake)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            settings={"base_url":f"http://127.0.0.1:{server.server_port}/v1","model":"mock-protocol-only","api_key":"TEST_ONLY","max_output_tokens":256}
            data,record=call_model(settings,[{"role":"user","content":"JSON"}])
            self.assertTrue(data["ok"]);self.assertEqual(record["usage"]["total_tokens"],13)
            self.assertEqual(received[0][0],"/v1/chat/completions")
            self.assertEqual(received[0][1]["response_format"],{"type":"json_object"})
        finally:server.shutdown();server.server_close()


class ProviderFailureTests(unittest.TestCase):
    def test_redirect_stops_before_sending_key_to_target(self):
        hits=[]
        class Fake(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                hits.append(self.path)
                self.send_response(307);self.send_header("Location","/target");self.end_headers()
        server=ThreadingHTTPServer(("127.0.0.1",0),Fake)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with self.assertRaisesRegex(ValueError,"重定向"):
                call_model({"base_url":f"http://127.0.0.1:{server.server_port}","model":"mock","api_key":"TEST_ONLY"},[])
            self.assertEqual(hits,["/chat/completions"])
        finally:server.shutdown();server.server_close()

    def test_truncated_response_preserves_usage(self):
        import io
        response={"choices":[{"finish_reason":"length","message":{"content":"{"}}],"usage":{"prompt_tokens":5,"completion_tokens":8,"total_tokens":13}}
        class Opener:
            def open(self,*args,**kwargs):return io.BytesIO(json.dumps(response).encode())
        with patch("video_agent.planning.urllib.request.build_opener",return_value=Opener()):
            with self.assertRaises(ModelOutputError) as ctx:
                call_model({"base_url":"https://example.com","model":"mock","api_key":"TEST_ONLY"},[])
            self.assertEqual(ctx.exception.record["usage"]["total_tokens"],13)
