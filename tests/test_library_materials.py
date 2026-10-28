from __future__ import annotations
import copy
import json
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from pathlib import Path
from unittest.mock import patch

from video_agent.engine import ROOT
from video_agent import library,materials
from video_agent.workspace import Workspace,Conflict
from video_agent.web import Server
from tests.test_uploads import PNG


class LibraryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'projects').mkdir();(self.root/'projects/lesson04-validation').symlink_to(ROOT/'projects/lesson04-validation',target_is_directory=True)
  self.ws=Workspace(self.root);self.doc=self.ws.sample();self.pid=self.doc['id'];self.image=self.root/'picture.png';self.image.write_bytes(PNG)
 def tearDown(self):self.tmp.cleanup()
 def test_catalogue_has_categories_deduplicates_and_hides_paths(self):
  items=library.catalogue(self.ws);self.assertTrue({'md','voice','image'}<=set(i['category'] for i in items));self.assertTrue(all('_path' not in i for i in items))
  self.ws.sample();both=library.catalogue(self.ws);self.assertEqual(sum(i['category']=='voice' for i in both),sum(i['category']=='voice' for i in items))
 def test_upload_label_attach_and_revision_conflict(self):
  item=library.register(self.ws,self.image,'疑惑.png','image');again=library.register(self.ws,self.image,'same.png','image');self.assertEqual(item['id'],again['id'])
  library.annotate(self.ws,item['id'],{'name':'疑惑反应','tags':['疑问','CS']});self.assertEqual(library.resource(self.ws,item['id'])['tags'],['疑问','CS'])
  result=library.attach(self.ws,self.pid,1,[item['id']]);key=result['mapping'][item['id']];self.assertTrue((self.ws.path(self.pid)/result['project']['config']['assets'][key]).is_file());self.assertEqual(result['project']['revision'],2)
  copies=[i for i in library.catalogue(self.ws) if i['name']=='疑惑反应'];self.assertEqual(len(copies),1);self.assertEqual(copies[0]['id'],item['id'])
  with self.assertRaises(Conflict):library.attach(self.ws,self.pid,1,[item['id']])
  self.assertEqual(self.ws.get(self.pid)['revision'],2)
 def test_corrupt_media_and_md_limit_are_rejected(self):
  bad=self.root/'bad.png';bad.write_text('not an image')
  with self.assertRaises(ValueError):library.register(self.ws,bad,'bad.png','image')
  md=self.root/'class.md';md.write_text('# 本课\n一个概念')
  item=library.register(self.ws,md,'class.md','md');self.assertEqual(library.resource(self.ws,item['id'])['_path'].read_text(),md.read_text())
  with self.assertRaises(ValueError):library.attach(self.ws,self.pid,1,[item['id']])
  md.write_text('字'*50001)
  with self.assertRaises(ValueError):library.register(self.ws,md,'class.md','md')
 def test_atomic_attach_callback_failure_preserves_workspace(self):
  item=library.register(self.ws,self.image,'x.png','image')
  def invalid(doc,mapping):doc['storyboard']['scenes'][0]['events'][0]['meme']='not_registered'
  with self.assertRaises(ValueError):library.attach(self.ws,self.pid,1,[item['id']],invalid)
  self.assertEqual(self.ws.get(self.pid)['revision'],1);self.assertNotIn('asset_metadata',self.ws.get(self.pid)['config'])
 def clean(self):
  doc=copy.deepcopy(self.doc)
  for s in doc['storyboard']['scenes']:
   for e in s['events']:e.pop('meme',None)
  return self.ws.update(self.pid,1,{'storyboard':doc['storyboard']})
 def answer(self,doc,rid):
  return {'matches':[{'scene':t['scene'],'resource':rid,'confidence':.85,'reason':'与讲解误区相符','anchor':t['anchor'],'query':''} for t in materials.targets(doc)]}
 def test_model_ids_anchors_confidence_are_validated(self):
  d=self.clean();wanted=materials.targets(d);r=self.answer(d,'local');self.assertEqual(len(materials.selections(r,wanted,{'local'})),3)
  r['matches'][0]['anchor']='模型不应改写现有锚点'
  self.assertEqual(materials.selections(r,wanted,{'local'})[0]['anchor'],wanted[0]['anchor'])
  new_targets=copy.deepcopy(wanted);new_targets[0]['anchor']=''
  with self.assertRaises(ValueError):materials.selections(r,new_targets,{'local'})
  r=self.answer(d,'fake')
  with self.assertRaises(ValueError):materials.selections(r,wanted,{'local'})
  r=self.answer(d,'local');r['matches'][0]['confidence']=.3;self.assertIsNone(materials.selections(r,wanted,{'local'})[0]['resource'])
 def test_preserve_manual_assets_real_attach_and_repeat_no_model(self):
  d=self.clean();item=library.register(self.ws,self.image,'困惑.png','image');folder=self.root/'job';folder.mkdir()
  with patch('video_agent.materials.call_model',return_value=(self.answer(d,item['id']),{'usage':{'total_tokens':10}})) as model:
   result=materials.run(self.ws,d,folder,online=False);self.assertEqual(result['applied'],3);self.assertEqual(model.call_count,1)
  current=self.ws.get(self.pid);self.assertEqual(current['revision'],3)
  for s in current['storyboard']['scenes']:self.assertTrue(any(e.get('material_match') for e in s['events']))
  with patch('video_agent.materials.call_model',side_effect=AssertionError('must not call')):
   again=materials.run(self.ws,current,folder,online=True);self.assertEqual(again['applied'],0);self.assertEqual(again['preserved'],3);self.assertEqual(again['skipped'],[])
  self.assertEqual(self.ws.get(self.pid)['revision'],3)
 def test_scene_without_events_gets_valid_narration_anchor(self):
  doc=copy.deepcopy(self.doc)
  for scene in doc['storyboard']['scenes']:scene['events']=[]
  d=self.ws.update(self.pid,1,{'storyboard':doc['storyboard']});item=library.register(self.ws,self.image,'反应.png','image');folder=self.root/'job';folder.mkdir()
  answer=self.answer(d,item['id'])
  for match in answer['matches']:
   scene=next(s for s in d['storyboard']['scenes'] if s['id']==match['scene']);match['anchor']=scene['speech'][:4]
  with patch('video_agent.materials.call_model',return_value=(answer,{})):
   report=materials.run(self.ws,d,folder,online=False)
  self.assertEqual(report['applied'],3)
  current=self.ws.get(self.pid)
  for old,scene in zip(d['storyboard']['scenes'],current['storyboard']['scenes']):
   self.assertEqual(old['speech'],scene['speech']);self.assertEqual(len(scene['events']),1);self.assertIn(scene['events'][0]['anchor'],scene['speech'])
 def test_revision_changed_during_model_call_keeps_edits_and_report(self):
  d=self.clean();item=library.register(self.ws,self.image,'反应.png','image');folder=self.root/'job';folder.mkdir()
  def response(*args,**kwargs):
   brief=copy.deepcopy(d['brief']);brief['direction']='用户在等待时自行修改的方向';self.ws.update(self.pid,d['revision'],{'brief':brief})
   return self.answer(d,item['id']),{'usage':{'total_tokens':12}}
  with patch('video_agent.materials.call_model',side_effect=response):
   with self.assertRaises(Conflict):materials.run(self.ws,d,folder,online=False)
  current=self.ws.get(self.pid);self.assertEqual(current['brief']['direction'],'用户在等待时自行修改的方向');self.assertEqual(current['revision'],3)
  self.assertTrue((folder/'material-result.json').is_file());self.assertTrue((folder/'material-usage.json').is_file())
 def test_empty_result_leaves_project_unchanged(self):
  d=self.clean();folder=self.root/'job';folder.mkdir();answer=self.answer(d,None)
  with patch('video_agent.materials.call_model',return_value=(answer,{})):
   result=materials.run(self.ws,d,folder,online=False)
  self.assertEqual(result['applied'],0);self.assertEqual(self.ws.get(self.pid)['revision'],2)
 def test_download_failed_is_reported_without_inserting_invalid_asset(self):
  d=self.clean();folder=self.root/'job';folder.mkdir();first=self.answer(d,None)
  for m in first['matches']:m['query']='Counter Strike'
  remote={'id':'net_test','name':'esports.png','mime':'image/png','context':'CS event','source':{'page':'https://commons.wikimedia.org/wiki/File:X'}}
  with patch('video_agent.materials.call_model',side_effect=[(first,{}),(self.answer(d,'net_test'),{})]),patch('video_agent.materials.search',return_value=[remote]),patch('video_agent.materials.download',side_effect=ValueError('network failed')):
   result=materials.run(self.ws,d,folder)
  self.assertEqual(result['applied'],0);self.assertTrue(result['warnings']);self.assertEqual(self.ws.get(self.pid)['revision'],2)
 def test_network_private_unapproved_or_credential_urls_rejected(self):
  for url in ['http://commons.wikimedia.org/a','https://127.0.0.1/','https://evil.example/','https://user:secret@commons.wikimedia.org/a','https://commons.wikimedia.org:444/a']:
   with self.assertRaises(ValueError):materials.fetch(url)
 def test_mediawiki_results_keep_sources_and_filter_nonimages(self):
  payload={'query':{'pages':{'1':{'title':'File:CS.jpg','imageinfo':[{'mime':'image/jpeg','url':'https://upload.wikimedia.org/x.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:CS.jpg','extmetadata':{'Artist':{'value':'<b>Author</b>'},'LicenseShortName':{'value':'CC BY-SA 4.0'}}}]},'2':{'title':'File:X.pdf','imageinfo':[{'mime':'application/pdf'}]}}}}
  with patch('video_agent.materials.fetch',return_value=json.dumps(payload).encode()) as fetch:
   items=materials.search(self.ws,'Counter Strike');self.assertEqual(len(items),1);self.assertEqual(items[0]['source']['author'],'Author');self.assertEqual(items[0]['source']['license'],'CC BY-SA 4.0')
   materials.search(self.ws,'Counter Strike');self.assertEqual(fetch.call_count,1)


class LibraryHttpTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.server=Server(('127.0.0.1',0),root);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.url=f'http://127.0.0.1:{self.server.server_port}';self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 def tearDown(self):self.server.shutdown();self.server.server_close();self.tmp.cleanup()
 def req(self,path,data=None,headers=None):return self.opener.open(urllib.request.Request(self.url+path,data=data,headers=headers or {}))
 def test_upload_requires_token_catalogue_and_preview_are_registered_only(self):
  import urllib.parse
  query=urllib.parse.urlencode({'name':'真实图片.png','kind':'image'})
  with self.assertRaises(urllib.error.HTTPError) as e:self.req('/api/library/upload?'+query,PNG)
  self.assertEqual(e.exception.code,403)
  with self.req('/api/library/upload?'+query,PNG,{'X-Workspace-Token':self.server.token}) as r:item=json.load(r)
  with self.req('/api/library') as r:self.assertEqual(len(json.load(r)['items']),1)
  with self.req(item['url'],headers={'Range':'bytes=0-7'}) as r:self.assertEqual(r.read(),b'\x89PNG\r\n\x1a\n')
  with self.assertRaises(urllib.error.HTTPError):self.req('/api/library/'+'f'*24+'/file')
