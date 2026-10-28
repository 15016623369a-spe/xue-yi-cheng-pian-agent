from __future__ import annotations
import array
import base64
import copy
import json
import math
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from video_agent.alignment import check_reference_bleed
from video_agent.engine import ROOT
from video_agent.media import mix_scene,read_pcm,write_pcm,soften_edges
from video_agent.uploads import register
from video_agent.web import Server
from video_agent.workspace import Conflict, Workspace

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

class UploadTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  (self.root/'projects').mkdir();(self.root/'projects/lesson04-validation').symlink_to(ROOT/'projects/lesson04-validation',target_is_directory=True)
  self.ws=Workspace(self.root);self.doc=self.ws.sample();self.pid=self.doc['id']
  self.source=self.root/'picture.png';self.source.write_bytes(PNG)
  self.runtime=json.loads((ROOT/'config/local.json').read_text())
 def tearDown(self):self.temp.cleanup()
 def sound(self):
  p=self.root/'sound.wav';write_pcm(p,array.array('h',(round(16000*math.sin(i*.17)) for i in range(6000))),24000);return p
 def test_uploaded_image_retains_original_and_survives_save(self):
  result=register(self.ws,self.pid,1,self.source,'../猫图.png','media');d=result['project'];key=result['asset']
  self.assertEqual(result['metadata']['name'],'猫图.png');self.assertFalse(d['approved']);self.assertEqual(d['revision'],2)
  asset=self.ws.path(self.pid)/d['config']['assets'][key]
  self.assertTrue(asset.is_file());self.assertEqual((asset.parent/'original.png').read_bytes(),PNG)
  story=copy.deepcopy(d['storyboard']);story['scenes'][0]['events'][0]['meme']=key
  saved=self.ws.update(self.pid,2,{'storyboard':story});self.assertEqual(saved['config']['scenes'][0]['events'][0]['meme'],key)
 def test_invalid_content_and_stale_revision_leave_registry_unchanged(self):
  self.source.write_text('not an image')
  with self.assertRaises(ValueError):register(self.ws,self.pid,1,self.source,'fake.png','media')
  self.assertNotIn('asset_metadata',self.ws.get(self.pid)['config'])
  self.ws.update(self.pid,1,{})
  with self.assertRaises(Conflict):register(self.ws,self.pid,1,self.source,'fake.png','media')
 def test_wrong_category_and_unregistered_custom_sound_rejected(self):
  with self.assertRaises(ValueError):register(self.ws,self.pid,1,self.source,'x.png','sound')
  d=self.ws.get(self.pid);story=copy.deepcopy(d['storyboard']);story['scenes'][0]['events'][0].update(sound='custom',sound_asset='missing')
  with self.assertRaisesRegex(ValueError,'not registered'):self.ws.compile(d,story)
 def test_custom_sound_mixes_at_anchor_respects_duration_and_gain(self):
  result=register(self.ws,self.pid,1,self.sound(),'我的提示音.wav','sound');d=result['project'];key=result['asset']
  story=copy.deepcopy(d['storyboard']);story['scenes'][0]['events'][0].update(sound='custom',sound_asset=key,sound_gain=.7)
  self.ws.compile(d,story)
  narration=self.root/'narration.wav';write_pcm(narration,array.array('h',[0]*24000),24000)
  timing=self.root/'timing.json';timing.write_text(json.dumps({'fps':30,'events':[{'id':'E01','start':6,'seconds':.1,'sound':'custom','sound_asset':key,'sound_gain':.7}]}))
  work=self.root/'mix';work.mkdir();mix_scene(work,narration,timing,{'sfx_volume':.1},{key:self.ws.path(self.pid)/d['config']['assets'][key]},self.runtime)
  pcm,_=read_pcm(work/'mixed.wav');self.assertTrue(any(pcm[4800:7200]));self.assertTrue(all(x==0 for x in pcm[:4800]+pcm[7200:]));self.assertLess(max(abs(x) for x in pcm),2100)
 def test_none_effect_is_silent_and_fades_preserve_length(self):
  s=array.array('h',[10000]*2400);soften_edges(s,24000);self.assertEqual(len(s),2400);self.assertEqual(s[0],0);self.assertEqual(s[-1],0);self.assertEqual(s[1200],10000)
  timing=self.root/'timing.json';timing.write_text(json.dumps({'fps':30,'events':[{'id':'E01','start':0,'sound':'none'}]}));p=self.root/'voice.wav';write_pcm(p,array.array('h',[0]*24000),24000)
  work=self.root/'mix';work.mkdir();mix_scene(work,p,timing,{});self.assertFalse(any(read_pcm(work/'mixed.wav')[0]))
 def test_reference_echo_is_rejected_but_legitimate_words_are_kept(self):
  ref='你看这防守夹角打的。'
  with self.assertRaisesRegex(ValueError,'尾音'):check_reference_bleed('总结一下关系图。',{'text':'打的总结一下关系图'},ref)
  check_reference_bleed('这把打的不错。',{'text':'这把打的不错'},ref)
  check_reference_bleed('重点来了。',{'text':'动点来了'},ref)

class UploadHttpTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);(root/'projects').mkdir();(root/'projects/lesson04-validation').symlink_to(ROOT/'projects/lesson04-validation',target_is_directory=True)
  self.server=Server(('127.0.0.1',0),root);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.doc=self.server.ws.sample();self.url=f'http://127.0.0.1:{self.server.server_port}';self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 def tearDown(self):self.server.shutdown();self.server.server_close();self.temp.cleanup()
 def request(self,path,data=None,headers=None):return self.opener.open(urllib.request.Request(self.url+path,data=data,headers=headers or {}))
 def test_binary_upload_and_range_preview(self):
  query=urllib.parse.urlencode({'name':'真实中文.png','kind':'media','revision':1});path=f"/api/projects/{self.doc['id']}/upload?{query}"
  with self.assertRaises(urllib.error.HTTPError) as ctx:self.request(path,PNG)
  self.assertEqual(ctx.exception.code,403)
  with self.request(path,PNG,{'X-Workspace-Token':self.server.token,'Content-Type':'application/octet-stream'}) as r:d=json.load(r)
  url=f"/api/projects/{self.doc['id']}/assets/{d['asset']}"
  with self.request(url,headers={'Range':'bytes=0-7'}) as r:self.assertEqual(r.status,206);self.assertEqual(r.read(),b'\x89PNG\r\n\x1a\n')
  with self.assertRaises(urllib.error.HTTPError) as ctx:self.request(url,headers={'Range':'bytes=999999-'})
  self.assertEqual(ctx.exception.code,416)
  with self.assertRaises(urllib.error.HTTPError) as ctx:self.request(path,PNG,{'X-Workspace-Token':self.server.token})
  self.assertEqual(ctx.exception.code,409)
 def test_audio_preview_supports_seeking_and_no_path_escape(self):
  p=Path(self.temp.name)/'tone.wav';write_pcm(p,array.array('h',[500]*4800),24000)
  query=urllib.parse.urlencode({'name':'提示.wav','kind':'sound','revision':1})
  with self.request(f"/api/projects/{self.doc['id']}/upload?{query}",p.read_bytes(),{'X-Workspace-Token':self.server.token}) as r:d=json.load(r)
  with self.request(f"/api/projects/{self.doc['id']}/assets/{d['asset']}",headers={'Range':'bytes=-20'}) as r:self.assertEqual(r.status,206);self.assertEqual(len(r.read()),20);self.assertEqual(r.headers['Content-Type'],'audio/x-wav')
  with self.assertRaises(urllib.error.HTTPError):self.request(f"/api/projects/{self.doc['id']}/assets/../../.web/model.json")
