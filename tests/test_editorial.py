"""Title provenance, stable branding, bounded jobs and reversible media cleanup."""
import copy
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from unittest.mock import patch

from video_agent import cleanup, titles
from video_agent.engine import ROOT
from video_agent.jobs import Jobs, work
from video_agent.util import digest, file_hash, read_json, write_json
from video_agent.workspace import Conflict, Workspace
from video_agent.web import Server


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'config').mkdir();(self.root/'examples').mkdir()
        shutil.copy2(ROOT/'config/project.example.json',self.root/'config/project.example.json')
        for name in ['handoff.md','storyboard.json']:
            shutil.copy2(ROOT/'examples'/name,self.root/'examples'/name)
        voice=self.root/'assets/voice';voice.mkdir(parents=True)
        with wave.open(str(voice/'reference.wav'),'wb') as audio:
            audio.setparams((1,2,24000,0,'NONE','not compressed'));audio.writeframes(b'\0\0'*24000)
        (voice/'reference.txt').write_text('This is a silent test fixture, not speech.')
        self.ws=Workspace(self.root);self.doc=self.ws.sample();self.pid=self.doc['id']
        self.folder=self.root/'job';self.folder.mkdir()
        self.ws.save_settings({'base_url':'https://example.com/v1','model':'test','api_key':'TEST_NOT_A_REAL_SECRET'})

    def tearDown(self):self.tmp.cleanup()

    def response(self):
        return {'titles':[{'title':t,'reason':'依据本期缓存机制与修改范围的解释'} for t in ['改一段为什么不用全重做？','视频制作怎样只重做改过的地方？','缓存如何减少重复生成？']]}

    def generate(self):
        with patch.object(titles,'call_model',return_value=(self.response(),{'usage':{'total_tokens':200},'http_attempts':1})) as model:
            result=titles.generate(self.ws,self.doc,self.folder)
        return result,model

    def test_preferences_apply_only_to_new_projects_without_hardcoded_brand(self):
        old=copy.deepcopy(self.doc)
        self.ws.save_preferences({'series_name':'我的每日报告','auto_clean_online':True})
        new=self.ws.create({'source':'# 新笔记\n当前主题。'})
        self.assertEqual(new['config']['theme']['series'],'我的每日报告')
        self.assertEqual(self.ws.get(self.pid),old)
        self.assertNotIn('opening',new['config'])
        with self.assertRaises(ValueError):self.ws.save_preferences({'series_name':'坏\n名字'})
        with self.assertRaises(ValueError):self.ws.save_preferences({'auto_clean_online':'yes'})

    def test_title_and_series_update_keep_speech_and_opening_and_clear_review(self):
        self.ws.approve(self.pid,1)
        changed=self.ws.update(self.pid,1,{'episode_title':'每期独立的内容标题','series_name':'我的系列'})
        self.assertEqual(changed['storyboard']['title'],'每期独立的内容标题')
        self.assertEqual(changed['config']['title'],changed['title'])
        self.assertEqual(changed['config']['theme']['series'],'我的系列')
        self.assertEqual(changed['storyboard']['scenes'],self.doc['storyboard']['scenes'])
        self.assertFalse(changed['approved']);self.assertIn('我的系列',self.ws.export_md(changed))

    def test_titles_use_current_speech_not_filename_and_cache_without_model(self):
        result,model=self.generate()
        payload=json.loads(model.call_args.args[1][-1]['content'])
        self.assertEqual(payload['scenes'][0]['speech'],self.doc['storyboard']['scenes'][0]['speech'])
        self.assertEqual(payload['notes'],'')
        self.assertEqual(self.ws.get(self.pid),self.doc)
        # Changing an editorial title alone does not alter the content cache key.
        copy_doc=copy.deepcopy(self.doc);copy_doc['title']='文件名变化'
        with patch.object(titles,'call_model',side_effect=AssertionError('cached')):
            cached=titles.generate(self.ws,copy_doc,self.folder)
        self.assertTrue(cached['cache_hit']);self.assertEqual(cached['new_api_calls'],0)
        self.assertEqual(len(result['titles']),3)

    def test_title_apply_and_stale_suggestions_cannot_overwrite_new_draft(self):
        result,_=self.generate()
        chosen=titles.apply(self.ws,self.pid,1,result['titles'][0]['id'])
        self.assertEqual(chosen['title'],result['titles'][0]['title'])
        changed=self.ws.update(self.pid,chosen['revision'],{'series_name':'新系列'})
        self.assertTrue(titles.latest(self.ws,self.pid)['stale'])
        with self.assertRaises(Conflict):titles.apply(self.ws,self.pid,changed['revision'],result['titles'][1]['id'])
        self.assertEqual(self.ws.get(self.pid)['title'],chosen['title'])

    def test_corrupt_title_cache_recomputed_and_changed_speech_invalidates(self):
        self.generate()
        cache=next((self.ws.path(self.pid)/'title-cache').glob('*.json'));cache.write_text('broken')
        _,model=self.generate();self.assertEqual(model.call_count,1)
        doc=copy.deepcopy(self.doc);doc['storyboard']['scenes'][0]['speech']+=' 本期追加说明。'
        with patch.object(titles,'call_model',return_value=(self.response(),{'usage':{}})) as model:
            titles.generate(self.ws,doc,self.folder)
        self.assertEqual(model.call_count,1)

    def test_title_invalid_model_output_does_not_apply_and_records_usage(self):
        with patch.object(titles,'call_model',return_value=({'titles':[{'title':'坏','reason':'x'}]},{'usage':{'total_tokens':32}})):
            with self.assertRaises(ValueError):titles.generate(self.ws,self.doc,self.folder)
        self.assertEqual(self.ws.get(self.pid),self.doc)
        self.assertEqual(read_json(self.folder/'title-usage.json')['calls'][0]['usage']['total_tokens'],32)

    def test_title_job_is_persistent_and_saves_candidates_without_applying(self):
        with patch('video_agent.jobs.subprocess.Popen') as popen:
            popen.return_value.pid=9999;job=Jobs(self.ws).create('titles',self.pid,1)
        with patch.object(titles,'call_model',return_value=(self.response(),{'usage':{'total_tokens':200}})):
            work(self.root,job['id'])
        self.assertEqual(Jobs(self.ws).get(job['id'])['status'],'completed')
        self.assertEqual(self.ws.get(self.pid)['revision'],1)
        self.assertEqual(len(titles.latest(self.ws,self.pid)['titles']),3)

    def test_title_format_repair_is_bounded_and_records_all_usage(self):
        invalid=self.response();invalid['titles'][0]['title']='x'*60
        with patch.object(titles,'call_model',side_effect=[(invalid,{'usage':{'total_tokens':30},'http_attempts':2}),(self.response(),{'usage':{'total_tokens':20},'http_attempts':1})]) as model:
            result=titles.generate(self.ws,self.doc,self.folder)
        self.assertEqual(result['new_api_calls'],3);self.assertEqual(len(result['calls']),2)
        self.assertEqual(model.call_args.args[0]['_max_http_attempts'],1)
        self.assertIn('当前标题长度：60',model.call_args.args[1][-1]['content'])
        self.assertEqual(self.ws.get(self.pid),self.doc)

    def test_watermark_regions_and_detection_reject_body_captions(self):
        items=[{'text':'bilibili','confidence':.9,'box':[.79,.03,.15,.04]},
               {'text':'bilibili','confidence':.9,'box':[.79,.032,.15,.04]},
               {'text':'讲解抖音的功能','confidence':.9,'box':[.3,.5,.2,.04]},
               {'text':'普通字幕','confidence':.9,'box':[.8,.04,.1,.04]}]
        boxes=cleanup.detected(items);self.assertEqual(len(boxes),1);self.assertGreater(boxes[0][2],.15)
        for raw in [[[0,0,1,1]],[[0,0,.1,float('nan')]],[[.95,0,.1,.1]],[[True,0,.1,.1]]]:
            with self.assertRaises(ValueError):cleanup.regions(raw)
        with self.assertRaises(ValueError):cleanup.options({'mode':'manual','regions':[]})

    def test_cleanup_job_rejects_unregistered_path_and_needs_no_text_model(self):
        with self.assertRaises(ValueError):Jobs(self.ws).create('cleanup',self.pid,1,cleanup={'asset':'/etc/passwd','mode':'auto'})
        self.ws.save_settings({'base_url':'https://example.com/v1','model':'test','clear_key':True})
        d=self.ws.get(self.pid);d['config']['assets']['meme_test']='fake.png';self.ws._write(self.ws.path(self.pid),d)
        with patch('video_agent.jobs.subprocess.Popen') as popen:
            popen.return_value.pid=9999;job=Jobs(self.ws).create('cleanup',self.pid,1,cleanup={'asset':'meme_test','mode':'auto'})
        self.assertEqual(job['cleanup_input']['mode'],'auto')


@unittest.skipUnless(Path('/opt/homebrew/bin/ffmpeg').is_file(),'requires local FFmpeg')
class CleanupMediaTests(unittest.TestCase):
    def setUp(self):
        EditorialTests.setUp(self)
        write_json(self.root/'config/local.json',{'ffmpeg':'/opt/homebrew/bin/ffmpeg','ffprobe':'/opt/homebrew/bin/ffprobe'})
        self.clip=self.ws.path(self.pid)/'sample.mov'
        subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=320x180:r=30:d=1,drawbox=x=260:y=12:w=40:h=15:color=white:t=fill',
                        '-f','lavfi','-i','sine=frequency=600:sample_rate=24000:duration=1','-c:v','libx264','-c:a','pcm_s16le',str(self.clip)],check=True)
        d=self.ws.get(self.pid);d['config']['assets']['opening_test']='sample.mov';d['config']['opening']={'asset':'opening_test','captions':[]};self.ws._write(self.ws.path(self.pid),d)
        self.doc=d

    tearDown=EditorialTests.tearDown

    def pcm(self,path):
        return subprocess.check_output(['/opt/homebrew/bin/ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-f','s16le','-'])

    def test_manual_opening_cleanup_preserves_pcm_and_restore_uses_original(self):
        before=file_hash(self.clip);original_pcm=self.pcm(self.clip)
        result=cleanup.apply(self.ws,self.doc,{'asset':'opening_test','mode':'manual','regions':[[.80,.06,.16,.12]]},self.folder)
        self.assertTrue(result['applied']);current=self.ws.get(self.pid)
        cleaned=self.ws.path(self.pid)/current['config']['assets']['opening_test']
        self.assertEqual(original_pcm,self.pcm(cleaned));self.assertEqual(file_hash(self.clip),before)
        self.assertIn('cleanup_original_asset',current['config']['asset_metadata']['opening_test'])
        again=self.root/'restore';again.mkdir()
        restored=cleanup.apply(self.ws,current,{'asset':'opening_test','mode':'restore'},again)
        self.assertEqual(restored['report']['status'],'restored')
        current=self.ws.get(self.pid);restored_path=self.ws.path(self.pid)/current['config']['assets']['opening_test']
        self.assertEqual(file_hash(restored_path),before);self.assertFalse(current['approved'])

    def test_no_detected_watermark_leaves_project_unchanged(self):
        with patch.object(cleanup,'inspect',return_value=([],1)):
            result=cleanup.apply(self.ws,self.doc,{'asset':'opening_test','mode':'auto'},self.folder)
        self.assertFalse(result['applied']);self.assertEqual(result['report']['status'],'not_detected')
        self.assertEqual(self.ws.get(self.pid),self.doc)

    def test_cleanup_cache_hash_and_audio_integrity_and_recovery(self):
        selected={'mode':'manual','regions':[[.80,.06,.16,.12]]}
        out,report=cleanup.process(self.ws,self.clip,self.folder,selected)
        expected=file_hash(out);self.assertEqual(self.pcm(out),self.pcm(self.clip))
        with patch.object(cleanup,'run_command',side_effect=AssertionError('use cache')):
            same,r=cleanup.process(self.ws,self.clip,self.folder,selected)
        self.assertTrue(r['cache_hit']);self.assertEqual(file_hash(same),expected)
        out.write_bytes(b'corrupt')
        out,_=cleanup.process(self.ws,self.clip,self.folder,selected)
        self.assertEqual(file_hash(out),expected)

    def test_online_opening_detects_before_padding_and_retains_original(self):
        from video_agent import openings
        url='https://www.bilibili.com/video/BV161QiYzENZ/'
        c=openings.criteria({'keywords':'test','seconds':1})
        candidates=openings.ranked([{'title':'test video','url':url,'platform':'bilibili'}],c)
        write_json(openings.folder(self.ws,self.pid)/'latest.json',{'criteria':c,'candidates':candidates})
        seen=[]
        def inspect(ws,source,work,info,runtime):
            view=next(s for s in info['streams'] if s['codec_type']=='video')
            seen.append((view['width'],view['height']))
            return [[.80,.06,.16,.12]],1
        with patch.object(openings,'download',return_value=self.clip),patch.object(cleanup,'inspect',side_effect=inspect):
            result=openings.import_clip(self.ws,self.doc,{'candidate':candidates[0]['id'],'end':1},self.folder)
        self.assertEqual(seen,[(320,180)])
        doc=self.ws.get(self.pid);opening=doc['config']['opening']
        self.assertEqual(opening['cleanup']['status'],'processed')
        key=opening['asset'];meta=doc['config']['asset_metadata'][key]
        self.assertIn(meta['cleanup_original_asset'],doc['config']['assets'])
        self.assertEqual(self.pcm(self.ws.path(self.pid)/doc['config']['assets'][key]),self.pcm(self.clip))


class EditorialHTTPTests(unittest.TestCase):
    setUp_base=EditorialTests.setUp
    def setUp(self):
        self.setUp_base();self.server=Server(('127.0.0.1',0),self.root)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.tmp.cleanup()
    def request(self,path,data=None,token=True):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Workspace-Token']=self.server.token
        return urllib.request.urlopen(urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers))
    def test_preferences_and_title_routes_require_local_token(self):
        with self.request('/api/bootstrap') as response:
            self.assertIn('preferences',json.load(response))
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.request('/api/preferences',{'series_name':'测试系列'},token=False)
        self.assertEqual(exc.exception.code,403)
        with self.request('/api/preferences',{'series_name':'测试系列'}) as response:
            self.assertEqual(json.load(response)['series_name'],'测试系列')
        with self.request('/editor.js') as response:self.assertIn(b'openCleanup',response.read())
        with self.request(f'/api/projects/{self.pid}/titles') as response:self.assertEqual(json.load(response)['titles'],[])
        with patch('video_agent.jobs.subprocess.Popen') as popen:
            popen.return_value.pid=9999
            with self.request(f'/api/projects/{self.pid}/titles',{'revision':1}) as response:self.assertEqual(response.status,202)
