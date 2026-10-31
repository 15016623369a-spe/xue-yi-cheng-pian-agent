"""Discovery evidence, safe boundaries and original-audio/optimistic imports."""
import copy
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from video_agent import openings as o
from video_agent.engine import ROOT
from video_agent.jobs import Jobs, work
from video_agent.util import digest, file_hash, probe, read_json, write_json
from video_agent.workspace import Workspace, Conflict
from video_agent.web import Server

BILI='https://www.bilibili.com/video/BV161QiYzENZ/'
DOUYIN='https://www.douyin.com/video/7482436875987225875'

class PureOpeningTests(unittest.TestCase):
 def test_specific_platform_pages_only(self):
  self.assertEqual(o.canonical(BILI+'?x=1'),('bilibili',BILI))
  self.assertEqual(o.canonical(DOUYIN),('douyin',DOUYIN))
  for url in ['http://127.0.0.1/a','https://www.bilibili.com.evil.test/video/BV161QiYzENZ/', 'https://user:secret@www.bilibili.com/video/BV161QiYzENZ/','https://www.bilibili.com:999/video/BV161QiYzENZ/','file:///etc/passwd','https://www.douyin.com/search/foo', 'https://www.bilibili.com/video/../../a']:
   with self.subTest(url=url),self.assertRaises(ValueError):o.canonical(url)
 def test_criteria_and_cut_bounds(self):
  for value in [True,float('nan'),0,31]:
   with self.assertRaises(ValueError):o.criteria({'keywords':'看书','seconds':value})
  for data in [{},{'person':'a\nb'},{'person':'a','platforms':[]},{'person':'a','platforms':[{}]}]:
   with self.assertRaises(ValueError):o.criteria(data)
  for data in [{'candidate':'arbitrary'},{'candidate':'a'*24,'start':0,'end':31},{'candidate':'a'*24,'start':-1,'end':2}]:
   with self.assertRaises(ValueError):o.import_options(data)
 def test_parsers_dedupe_and_never_accept_foreign_url(self):
  text=f'<a href="//www.bilibili.com/video/BV161QiYzENZ/">01:30</a><a href="{BILI}">DANK1NG 看书</a><a href="https://evil.test/video/BV161QiYzENZ/">evil</a>'
  self.assertEqual(o.parse_results(text,'bilibili'),[{'platform':'bilibili','url':BILI,'title':'DANK1NG 看书'}])
  from urllib.parse import quote
  text=f'<div data-url="{DOUYIN}" data-title="{quote(chr(0xe00a)+"DANK1NG 看书"+chr(0xe00b))}"></div>'
  self.assertEqual(o.parse_results(text,'douyin')[0]['title'],'DANK1NG 看书')
 def test_rank_is_title_evidence_not_watch_or_emotion_verification(self):
  c=o.criteria({'person':'DANK1NG','keywords':'看书','emotion':'疑惑'})
  r=o.ranked([{'url':BILI,'platform':'bilibili','title':'Danking 看书 疑惑'},{'url':DOUYIN,'platform':'douyin','title':'无关视频'}],c)
  self.assertEqual(len(r),1);self.assertFalse(r[0]['quote_verified']);self.assertFalse(r[0]['emotion_verified']);self.assertEqual(r[0]['verification'],'title_only')
 def transcript(self,text):return {'segments':[{'words':[{'word':c,'start':i*.2,'end':(i+1)*.2} for i,c in enumerate(text)]}]}
 def test_quote_unique_exact_and_timed(self):
  a,b=o.locate(self.transcript('每次来都在玩游戏'),'每次来都在玩游戏');self.assertEqual(a,0);self.assertAlmostEqual(b,1.78)
  a,b=o.locate(self.transcript('要亲亲'),'要亲亲');self.assertEqual(a,0);self.assertGreater(b,a)
  with self.assertRaises(o.QuoteLocationError) as error:o.locate(self.transcript('要亲亲要亲亲'),'要亲亲')
  self.assertEqual(len(error.exception.matches),2);self.assertGreater(error.exception.matches[1]['start'],error.exception.matches[0]['start'])
  for text,quote in [('每次来都在玩游戏每次来都在玩游戏','每次来都在玩游戏'),('另一句内容','每次来都在玩游戏'),('短句','短句')]:
   with self.assertRaises(ValueError):o.locate(self.transcript(text),quote)

class WorkspaceOpeningTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  (self.root/'config').mkdir();write_json(self.root/'config/local.json',{'ffmpeg':'/opt/homebrew/bin/ffmpeg','ffprobe':'/opt/homebrew/bin/ffprobe'});shutil.copy2(ROOT/'config/project.example.json',self.root/'config/project.example.json')
  self.ws=Workspace(self.root);self.doc=self.ws.create({'source':'# 本课\n验证片头。','title':'片头验证'});self.pid=self.doc['id']
 def tearDown(self):self.tmp.cleanup()
 def report(self):
  r={'criteria':o.criteria({'person':'DANK1NG','keywords':'看书'}),'candidates':o.ranked([{'title':'DANK1NG 看书','url':BILI,'platform':'bilibili'}],o.criteria({'person':'DANK1NG'}))}
  write_json(o.folder(self.ws,self.pid)/'latest.json',r);return r
 def test_search_cache_does_not_mutate_project_or_call_model(self):
  html=f'<a href="{BILI}">DANK1NG 看书</a>'
  c={'person':'DANK1NG','keywords':'看书','platforms':['bilibili']}
  with patch.object(o,'fetch_search',return_value=html) as fetch:
   first=o.search(self.ws,self.pid,c);second=o.search(self.ws,self.pid,c)
   self.assertEqual(fetch.call_count,1)
  self.assertEqual(first['candidates'],second['candidates']);self.assertTrue(second['cache_hit']);self.assertEqual(first['new_api_calls'],0);self.assertEqual(self.ws.get(self.pid),self.doc)
 def test_empty_and_blocked_sources_return_real_failure_no_placeholder(self):
  with patch.object(o,'fetch_search',side_effect=ValueError('HTTP 412')):
   r=o.search(self.ws,self.pid,{'person':'DANK1NG'})
  self.assertEqual(r['candidates'],[]);self.assertTrue(any('412' in x for x in r['warnings']));self.assertEqual(self.ws.get(self.pid)['revision'],1)
 def test_custom_opening_survives_plain_style_save(self):
  doc=self.ws.get(self.pid);doc['config']['opening']={'asset':'opening_custom','captions':[]};self.ws._write(self.ws.path(self.pid),doc)
  brief={**doc['brief'],'style':'plain'};changed=self.ws.update(self.pid,1,{'brief':brief})
  self.assertEqual(changed['config']['opening']['asset'],'opening_custom')
 def test_disabled_custom_opening_can_be_enabled_again(self):
  doc=self.ws.get(self.pid);doc['config']['opening']={'asset':'opening_custom','captions':[]};self.ws._write(self.ws.path(self.pid),doc)
  off=self.ws.update(self.pid,1,{'media':{'opening':False,'bgm':False}});self.assertNotIn('opening',off['config'])
  on=self.ws.update(self.pid,2,{'media':{'opening':True,'bgm':False}});self.assertEqual(on['config']['opening']['asset'],'opening_custom')
 def test_search_job_has_no_model_requirement_and_snapshots_revision(self):
  with patch('video_agent.jobs.subprocess.Popen') as popen:
   popen.return_value.pid=9999
   job=Jobs(self.ws).create('opening',self.pid,1,opening={'mode':'search','data':{'person':'DANK1NG'}})
  self.assertEqual(read_json(Jobs(self.ws).folder(job['id'])/'input.json')['revision'],1)
  with patch.object(o,'search',return_value={'candidates':[],'new_api_calls':0}):work(self.root,job['id'])
  self.assertEqual(Jobs(self.ws).get(job['id'])['status'],'completed');self.assertEqual(self.ws.get(self.pid)['revision'],1)
 def test_failed_import_keeps_old_opening_revision_and_approval(self):
  r=self.report();before=self.ws.get(self.pid);folder=self.root/'job';folder.mkdir()
  with patch.object(o,'download',side_effect=ValueError('blocked')):
   with self.assertRaises(ValueError):o.import_clip(self.ws,before,{'candidate':r['candidates'][0]['id'],'start':0,'end':2},folder)
  self.assertEqual(self.ws.get(self.pid),before)
 def test_new_project_never_inherits_private_course_opening(self):
  config=read_json(self.root/'config/project.example.json');config['opening']={'asset':'old_series','captions':[]};config['theme']={'series':'Transformer 基础'}
  write_json(self.root/'config/project.example.json',config)
  (self.root/'assets').mkdir(exist_ok=True);(self.root/'assets/series-opening.mp4').write_bytes(b'old media')
  d=self.ws.create({'source':'# 新题材\nAI资讯','title':'AI日报'})
  self.assertNotIn('opening',d['config']);self.assertNotEqual(d['config']['theme']['series'],'Transformer 基础')
  with self.assertRaisesRegex(ValueError,'尚未选择'):self.ws.update(d['id'],1,{'media':{'opening':True}})
  self.assertEqual(self.ws.get(d['id'])['revision'],1)
 def test_forget_old_series_cannot_be_silently_reenabled(self):
  d=self.ws.get(self.pid);d['config']['opening']={'asset':'series_opening','captions':[]};d['config']['theme']={'series':'Transformer 基础'};self.ws._write(self.ws.path(self.pid),d)
  d=self.ws.update(self.pid,1,{'media':{'opening':False,'forget_opening':True}})
  self.assertNotIn('opening',d['config']);self.assertNotIn('opening_selection',d);self.assertNotEqual(d['config']['theme']['series'],'Transformer 基础')
  with self.assertRaisesRegex(ValueError,'尚未选择'):self.ws.update(self.pid,2,{'media':{'opening':True}})
 def test_auto_apply_locates_quote_and_stops_after_two_failed_candidates(self):
  r=self.report();r['criteria']['quote']='每次来都在玩游戏';r['candidates']*=3
  before=self.ws.get(self.pid)
  with patch.object(o,'search',return_value=r),patch.object(o,'import_clip',side_effect=ValueError('无法定位原话')) as apply:
   result=o.search_apply(self.ws,before,r['criteria'],self.root/'auto')
  self.assertEqual(apply.call_count,2);self.assertFalse(result['applied']);self.assertEqual(len(result['attempts']),2)
  self.assertTrue(apply.call_args.args[2]['locate_quote']);self.assertFalse(apply.call_args.args[2]['append']);self.assertEqual(self.ws.get(self.pid),before)
 def test_auto_apply_stops_after_success_and_detects_revision_conflict(self):
  r=self.report();before=self.ws.get(self.pid)
  with patch.object(o,'search',return_value=r),patch.object(o,'import_clip',return_value={'revision':2,'preview_asset':'clip'}) as apply:
   result=o.search_apply(self.ws,before,r['criteria'],self.root/'auto')
  self.assertTrue(result['applied']);self.assertEqual(apply.call_count,1)
  self.ws.update(self.pid,1,{'brief':before['brief']})
  with patch.object(o,'search',return_value=r),patch.object(o,'import_clip') as apply:
   with self.assertRaises(Conflict):o.search_apply(self.ws,before,r['criteria'],self.root/'conflict')
  apply.assert_not_called()
 def test_auto_job_reports_needs_input_instead_of_import_success(self):
  with patch('video_agent.jobs.subprocess.Popen') as popen:
   popen.return_value.pid=9999
   job=Jobs(self.ws).create('opening',self.pid,1,opening={'mode':'search_apply','data':{'person':'DANK1NG'}})
  with patch.object(o,'search_apply',return_value={'applied':False,'candidates':[],'attempts':[]}):work(self.root,job['id'])
  final=Jobs(self.ws).get(job['id']);self.assertEqual(final['status'],'needs_input');self.assertIn('未接入',final['message']);self.assertEqual(self.ws.get(self.pid)['revision'],1)

@unittest.skipUnless(Path('/opt/homebrew/bin/ffmpeg').is_file(),'requires local FFmpeg')
class OriginalAudioTests(unittest.TestCase):
 report=WorkspaceOpeningTests.report
 tearDown=WorkspaceOpeningTests.tearDown
 def setUp(self):
  WorkspaceOpeningTests.setUp(self);write_json(self.root/'config/local.json',{'ffmpeg':'/opt/homebrew/bin/ffmpeg','ffprobe':'/opt/homebrew/bin/ffprobe'})
  self.clip=self.root/'source.mp4'
  subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=320x180:r=30:d=2','-f','lavfi','-i','sine=frequency=440:sample_rate=24000:duration=2','-c:v','libx264','-c:a','aac','-shortest',str(self.clip)],check=True)
 def test_preparation_cache_checks_hash_and_recovers_corruption(self):
  d=self.doc;messages=[]
  first=self.root/'prep1';first.mkdir();a,b,_,_=o.prepare_clip(self.ws,d,self.clip,0,2,False,first,messages.append)
  expected=file_hash(a)
  second=self.root/'prep2';second.mkdir()
  with patch.object(o,'run_command',side_effect=AssertionError('must use cached media')):
   a2,_,_,_=o.prepare_clip(self.ws,d,self.clip,0,2,False,second,messages.append)
  self.assertEqual(file_hash(a2),expected)
  cache=next((o.folder(self.ws,self.pid)/'prepared').glob('*/opening.mov'));cache.write_bytes(b'corrupted')
  third=self.root/'prep3';third.mkdir();a3,_,_,_=o.prepare_clip(self.ws,d,self.clip,0,2,False,third,messages.append)
  self.assertEqual(file_hash(a3),expected)
 def test_real_pcm_audio_import_append_and_conflict_preserve(self):
  report=self.report();options={'candidate':report['candidates'][0]['id'],'start':0,'end':2}
  with patch.object(o,'download',return_value=self.clip):
   work=self.root/'first';work.mkdir();r=o.import_clip(self.ws,self.doc,options,work)
   doc=self.ws.get(self.pid);opening=doc['config']['opening'];path=self.ws.path(self.pid)/doc['config']['assets'][opening['asset']]
   audio=next(s for s in probe('/opt/homebrew/bin/ffprobe',path)['streams'] if s['codec_type']=='audio');self.assertEqual(audio['codec_name'],'pcm_s16le');self.assertEqual(r['revision'],2)
   saved=o.latest(self.ws,self.pid);self.assertTrue(saved['applied']);self.assertEqual(saved['imported']['preview_asset'],opening['preview_asset'])
   work=self.root/'conflict';work.mkdir()
   with self.assertRaises(Conflict):o.import_clip(self.ws,self.doc,options,work)
   self.assertEqual(self.ws.get(self.pid)['revision'],2)
   work=self.root/'second';work.mkdir();r=o.import_clip(self.ws,doc,{**options,'append':True},work)
  new=self.ws.get(self.pid);self.assertEqual(len(new['config']['opening']['segments']),2);self.assertAlmostEqual(r['seconds'],4,places=1);self.assertFalse(new['approved']);self.assertTrue((self.ws.path(self.pid)/'revisions/r0002.json').is_file())
 def test_search_apply_really_connects_pcm_media_without_series_captions(self):
  report=self.report();report['criteria']['seconds']=2
  with patch.object(o,'search',return_value=report),patch.object(o,'download',return_value=self.clip):
   result=o.search_apply(self.ws,self.doc,report['criteria'],self.root/'auto')
  d=self.ws.get(self.pid);opening=d['config']['opening'];path=self.ws.path(self.pid)/d['config']['assets'][opening['asset']]
  self.assertTrue(result['applied']);self.assertEqual(result['revision'],2);self.assertEqual(opening['captions'],[]);self.assertEqual(len(opening['segments']),1)
  self.assertEqual(next(s for s in probe('/opt/homebrew/bin/ffprobe',path)['streams'] if s['codec_type']=='audio')['codec_name'],'pcm_s16le')

class OpeningHTTPTests(unittest.TestCase):
 def setUp(self):
  WorkspaceOpeningTests.setUp(self);self.server=Server(('127.0.0.1',0),self.root);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.url=f'http://127.0.0.1:{self.server.server_port}'
 def tearDown(self):self.server.shutdown();self.server.server_close();WorkspaceOpeningTests.tearDown(self)
 def request(self,path,data=None,token=True):
  headers={'Content-Type':'application/json'}
  if token:headers['X-Workspace-Token']=self.server.token
  return urllib.request.urlopen(urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers))
 def test_endpoints_token_and_script(self):
  prefix=f'/api/projects/{self.pid}/openings'
  with self.request(prefix) as response:self.assertEqual(json.load(response)['candidates'],[])
  with self.assertRaises(urllib.error.HTTPError) as exc:self.request(prefix,{'revision':1,'mode':'search','data':{'person':'DANK1NG'}},token=False)
  self.assertEqual(exc.exception.code,403)
  with patch('video_agent.jobs.subprocess.Popen') as popen:
   popen.return_value.pid=9999
   with self.request(prefix,{'revision':1,'mode':'search','data':{'person':'DANK1NG'}}) as response:self.assertEqual(response.status,202)
  with self.request('/opening.js') as response:self.assertIn(b'createOpeningJob',response.read())
