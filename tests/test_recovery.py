"""Safety bounds for local timing repair, planning checkpoints and tool recovery."""
import copy
import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from video_agent.anchors import repair,resolve
from video_agent.engine import ROOT
from video_agent.jobs import Jobs,work
from video_agent.planning import plan,validate_plan,call_model,ModelOutputError,ModelRequestError
from video_agent.recovery import decision
from video_agent.util import digest,read_json,write_json
from video_agent.workspace import Workspace

SOURCE='示例只讲一个知识点。\n这是一条明确的边界。'
KNOWLEDGE={'title':'示例','facts':[{'id':'K01','claim':'示例只讲一个知识点。','boundaries':['只用于示例'],'evidence':[{'source_id':'L1'}]}],'questions':[]}
STORY={'title':'示例','scenes':[{'id':'S01','title':'例子','tag':'重点','speech':'先看这句明确的口播内容，其他文字不动。','knowledge_ids':['K01'],'analogy':{'mapping':'直接讲解','boundary':'只讨论原文'},'visual':{'template':'calculation','lines':['只讲一个知识点']},'events':[{'id':'E01','anchor':'先看这句明确的口播内容','popup':'重点','seconds':1.5,'sound':'beep'}]}]}

def validate(story,knowledge):
    data=validate_plan(story,knowledge)
    for s in data['scenes']:
        for e in s.get('events',[]):
            if s['speech'].count(e['anchor'])!=1:raise ValueError(f"{s['id']}/{e['id']}: anchor must occur exactly once in speech: {e['anchor']}")
    return data

class AnchorRecoveryTests(unittest.TestCase):
 def test_original_failure_cases_become_unique_verbatim_without_changing_speech(self):
  cases=[('OpenAI 和 ServiceNow 扩大合作。但开放时间不同：MCP 插件当天已上架，语音和安全等要到 2027 年。','ServiceNow MCP 插件当天已在市场提供','MCP 插件当天已'),('语音和安全等要到 2027 年。','语音和安全等能力安排在 2027 年','语音和安全等'),('Anthropic 公布与六家药物研发机构合作。','Anthropic 公布了与六家药物研发相关机构的合作进展','与六家药物研发')]
  for speech,anchor,expected in cases:
   with self.subTest(anchor=anchor):self.assertEqual(resolve(speech,anchor),expected);self.assertEqual(speech.count(expected),1)
  original=copy.deepcopy(STORY);original['scenes'][0]['events'][0]['anchor']='资料开头说先看这句明确的口播内容然后再解释'
  before=copy.deepcopy(original);fixed,records=repair(original)
  self.assertEqual(original,before);self.assertEqual(fixed['scenes'][0]['speech'],before['scenes'][0]['speech']);self.assertEqual(fixed['scenes'][0]['visual'],before['scenes'][0]['visual']);self.assertEqual(len(records),1)
 def test_whitespace_punctuation_can_resolve_but_repeat_never_picks_first(self):
  self.assertEqual(resolve('这句重点，说明条件边界。','这句重点说明条件边界'),'这句重点，说明条件边界')
  self.assertIsNone(resolve('这句明确的口播内容。这句明确的口播内容。','这句明确的口播内容'))
 def test_short_generic_numeric_and_midword_matches_are_not_enough(self):
  for speech,anchor in [('2027年上线','未来安排2027年'),('重点来了','问题的重点来了'),('abcdefghijklmnop-other','zabcdefghijklmnop!'),('另一条完全不同的叙述','这句明确的口播内容')]:
   with self.subTest(anchor=anchor):self.assertIsNone(resolve(speech,anchor))
 def test_valid_anchor_no_model_authored_repair_claim(self):
  s=copy.deepcopy(STORY);s['scenes'][0]['events'][0]['anchor_repair']={'reason':'pretend verified'}
  fixed,records=repair(s);self.assertFalse(records);self.assertNotIn('anchor_repair',fixed['scenes'][0]['events'][0])

class PlanningRecoveryTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.cache=Path(self.tmp.name)/'cache'
 def tearDown(self):self.tmp.cleanup()
 def answer(self,data,attempts=1):return copy.deepcopy(data),{'http_attempts':attempts,'usage':{'total_tokens':12}}
 def test_local_repair_avoids_extra_model_request(self):
  s=copy.deepcopy(STORY);s['scenes'][0]['events'][0]['anchor']='资料说先看这句明确的口播内容之后讲解'
  with patch('video_agent.planning.call_model',side_effect=[self.answer(KNOWLEDGE),self.answer(s)]) as model:
   result=plan(SOURCE,{}, {},self.cache,validate)
  self.assertEqual(model.call_count,2);self.assertEqual(result['new_api_calls'],2);self.assertEqual(len(result['repairs']),1)
 def test_failed_adaptation_reuses_knowledge_on_retry(self):
  invalid=copy.deepcopy(STORY);invalid['scenes'][0]['events'][0]['anchor']='毫无对应的另一种想法'
  with patch('video_agent.planning.call_model',side_effect=[self.answer(KNOWLEDGE),self.answer(invalid),self.answer(invalid)]):
   with self.assertRaisesRegex(ValueError,'一次修正'):plan(SOURCE,{}, {},self.cache,validate)
  with patch('video_agent.planning.call_model',return_value=self.answer(STORY)) as model:
   result=plan(SOURCE,{}, {},self.cache,validate)
  self.assertEqual(model.call_count,1);self.assertEqual(result['reused_stages'],['knowledge']);self.assertEqual(result['new_api_calls'],1)
 def test_valid_stage_checkpoints_restore_damaged_full_cache_without_api(self):
  with patch('video_agent.planning.call_model',side_effect=[self.answer(KNOWLEDGE),self.answer(STORY)]):plan(SOURCE,{}, {},self.cache,validate)
  target=next(p for p in self.cache.glob('*.json') if not p.name.endswith('-usage.json'));target.write_text('{broken')
  with patch('video_agent.planning.call_model',side_effect=AssertionError('no API')):
   result=plan(SOURCE,{}, {},self.cache,validate)
  self.assertEqual(result['new_api_calls'],0);self.assertEqual(result['reused_stages'],['knowledge','storyboard'])
 def test_semantically_invalid_full_cache_restores_valid_checkpoints(self):
  with patch('video_agent.planning.call_model',side_effect=[self.answer(KNOWLEDGE),self.answer(STORY)]):plan(SOURCE,{}, {},self.cache,validate)
  target=next(p for p in self.cache.glob('*.json') if not p.name.endswith('-usage.json'))
  stored=read_json(target);stored['result']['knowledge']['facts'][0]['evidence'][0]['quote']='不存在的引文';stored['sha256']=digest(stored['result']);write_json(target,stored)
  with patch('video_agent.planning.call_model',side_effect=AssertionError('no API')):result=plan(SOURCE,{}, {},self.cache,validate)
  self.assertEqual(result['new_api_calls'],0);self.assertEqual(result['knowledge']['facts'][0]['evidence'][0]['quote'],'示例只讲一个知识点。')
 def test_http_attempts_share_four_request_limit(self):
  bad=copy.deepcopy(STORY);bad['scenes'][0]['events'][0]['anchor']='完全不同不能定位'
  with patch('video_agent.planning.call_model',side_effect=[self.answer(KNOWLEDGE,2),self.answer(bad,2)]) as model:
   with self.assertRaisesRegex(ValueError,'4次请求上限'):plan(SOURCE,{}, {},self.cache,validate)
  self.assertEqual(model.call_count,2)
 def test_invalid_json_uses_bounded_format_repair_and_keeps_usage(self):
  exc=ModelOutputError('bad json');exc.record={'usage':{'total_tokens':3},'http_attempts':1};exc.repairable_json=True
  with patch('video_agent.planning.call_model',side_effect=[exc,self.answer(KNOWLEDGE),self.answer(STORY)]) as model:
   r=plan(SOURCE,{}, {},self.cache,validate)
  self.assertEqual(model.call_count,3);self.assertEqual(r['calls'][0]['status'],'failed');self.assertEqual(r['new_api_calls'],3)

class TransportRecoveryTests(unittest.TestCase):
 def response(self,statuses):
  counter=[];sequence=list(statuses)
  class Handler(BaseHTTPRequestHandler):
   def do_POST(self):
    self.rfile.read(int(self.headers.get('Content-Length',0)));counter.append(1);code=sequence.pop(0)
    body=json.dumps({'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}],'usage':{'total_tokens':5}}).encode()
    self.send_response(code);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
   def log_message(self,*args):pass
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
  return server,counter,{'base_url':f'http://127.0.0.1:{server.server_port}','api_key':'TEST_NOT_REAL','model':'mock'}
 def test_502_then_success_records_two_attempts_and_unknown_usage(self):
  server,counter,settings=self.response([502,200])
  try:
   with patch('video_agent.planning.time.sleep'):data,record=call_model(settings,[])
   self.assertTrue(data['ok']);self.assertEqual(len(counter),2);self.assertEqual(record['http_attempts'],2);self.assertTrue(record['usage_unknown'])
  finally:server.shutdown();server.server_close()
 def test_401_is_never_retried(self):
  server,counter,settings=self.response([401])
  try:
   with self.assertRaises(ModelRequestError) as e:call_model(settings,[])
   self.assertEqual(len(counter),1);self.assertFalse(e.exception.retryable)
  finally:server.shutdown();server.server_close()
 def test_repeated_503_stops_after_two_attempts(self):
  server,counter,settings=self.response([503,503])
  try:
   with patch('video_agent.planning.time.sleep'),self.assertRaises(ModelRequestError) as e:call_model(settings,[])
   self.assertEqual(len(counter),2);self.assertEqual(e.exception.record['http_attempts'],2)
  finally:server.shutdown();server.server_close()

class WorkerRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'config').mkdir();shutil.copy2(ROOT/'config/project.example.json',self.root/'config/project.example.json');self.ws=Workspace(self.root);self.doc=self.ws.create({'source':SOURCE});self.jobs=Jobs(self.ws)
 def tearDown(self):self.tmp.cleanup()
 def job(self):
  with patch('video_agent.jobs.subprocess.Popen') as spawn:
   spawn.return_value.pid=9999
   return self.jobs.create('opening',self.doc['id'],1,opening={'mode':'search','data':{'keywords':'检索例子'}})
 def test_transient_tool_failure_recovers_once(self):
  job=self.job()
  with patch('video_agent.openings.search',side_effect=[TimeoutError('网络连接失败'),{'candidates':[]}]) as search,patch('video_agent.jobs.time.sleep'):
   work(self.root,job['id'])
  result=self.jobs.get(job['id']);self.assertEqual(search.call_count,2);self.assertEqual(result['status'],'completed');self.assertEqual(len(result['recovery_history']),1);self.assertEqual(self.ws.get(self.doc['id'])['revision'],1)
 def test_permanent_access_failure_never_retries(self):
  job=self.job()
  with patch('video_agent.openings.search',side_effect=ValueError('需要验证码')) as search:work(self.root,job['id'])
  result=self.jobs.get(job['id']);self.assertEqual(search.call_count,1);self.assertEqual(result['status'],'failed');self.assertEqual(result['recovery']['code'],'access_or_settings')
 def test_repeated_transient_failure_is_bounded_and_actionable(self):
  job=self.job()
  with patch('video_agent.openings.search',side_effect=TimeoutError('网络连接失败')) as search,patch('video_agent.jobs.time.sleep'):work(self.root,job['id'])
  result=self.jobs.get(job['id']);self.assertEqual(search.call_count,2);self.assertTrue(result['recovery']['exhausted']);self.assertEqual(result['status'],'failed')
 def test_model_and_speech_failures_are_not_replayed_wholesale(self):
  self.assertFalse(decision('API连接超时','plan')['retry']);self.assertFalse(decision('Speech phrase coverage insufficient','render')['retry']);self.assertTrue(decision('Session closed','render')['retry'])
