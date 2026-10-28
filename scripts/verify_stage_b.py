"""Read-only real integration audit; never calls the configured model."""
from pathlib import Path
import json,os,sys,urllib.request,urllib.error
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from video_agent.workspace import Workspace
from video_agent.planning import plan,validate_plan,validate_knowledge
from video_agent.jobs import Jobs
from video_agent.util import write_json
w=Workspace();jobs=Jobs(w);checks={};details={}
def check(k,v):checks[k]=bool(v)
d=w.get('web-894fa67419eb');validate_knowledge(d['knowledge'],d['source']);w.compile(d)
check('real_md_grounded_knowledge_and_compiled_plan',bool(d['knowledge']['facts']) and bool(d['storyboard']['scenes']))
actual=jobs.get('368e2b2b7d3344c2a8fb1dae666e624c')
check('actual_configured_provider_adaptation',actual['status']=='completed' and actual['result']['new_api_calls']==2)
def verify(story,k):
 clean=validate_plan(story,k);w.compile(d,clean,k);return clean
with patch('video_agent.planning.call_model',side_effect=AssertionError('Audit must not call model')):
 cached=plan(d['source'],d['brief'],w.settings(True),w.path(d['id'])/'planning-cache',verify)
check('real_adaptation_cache_has_zero_new_requests',cached['cache_hit'] and cached['new_api_calls']==0)
for key,jid in [('fixture_single','646fe81b7f86431bb158a6ca560307c8'),('fixture_edited_full','f1f1016797eb4bc9bab94c49ffdfa228'),('real_new_md_single','e48ddadfcee34b0d9b5e8d5ddc5951ae')]:
 j=jobs.get(jid);check(key,j['status']=='completed' and j['result']['audit']['passed']);details[key]={'job':jid,'counts':j['result'].get('counts'),'seconds':j['result']['audit']['duration_seconds']}
op=urllib.request.build_opener(urllib.request.ProxyHandler({}));url='http://127.0.0.1:8765'
def req(path,**kw):return op.open(urllib.request.Request(url+path,**kw),timeout=10)
with req('/api/bootstrap') as r:b=json.load(r)
check('settings_response_has_no_key','api_key' not in b['settings'])
check('secret_file_owner_only',os.stat(w.web/'model.json').st_mode&0o777==0o600)
with req('/api/jobs/e48ddadfcee34b0d9b5e8d5ddc5951ae/files/video.mp4',headers={'Range':'bytes=0-1023'}) as r:
 check('actual_video_http_range',r.status==206 and len(r.read())==1024 and r.headers['Content-Range'].startswith('bytes 0-1023/'))
with req('/api/projects/web-894fa67419eb/worksheet.md') as r:
 check('real_md_worksheet_download',r.status==200 and 'S01' in r.read().decode())
try:
 req('/api/projects',data=b'{}',headers={'Content-Type':'application/json','X-Workspace-Token':b['token'],'Origin':'https://untrusted.example'})
 check('live_cross_origin_write_rejected',False)
except urllib.error.HTTPError as e:
 check('live_cross_origin_write_rejected',e.code==403);e.close()
key=w.settings(True).get('api_key','')
leaks=[]
for folder in [w.web/'jobs',w.path('web-894fa67419eb')/'planning-cache',w.path('web-e153c2673188')/'planning-cache']:
 for f in folder.rglob('*'):
  if f.is_file() and f.suffix in {'.json','.log','.jsonl'} and key and key in f.read_text(errors='replace'):leaks.append(str(f))
check('actual_jobs_and_planning_cache_do_not_contain_key',not leaks)
if '--include-paper' in sys.argv:
 doc=w.get('web-e153c2673188');renders=[j for j in jobs.list(doc['id']) if j['kind']=='render' and not j.get('scene_id') and j['status']=='completed']
 check('paper_full_new_md_video',bool(renders) and renders[0]['result']['audit']['passed'])
 if renders:details['paper']={k:renders[0].get(k) for k in ['id','revision','result']}
result={'passed':all(checks.values()),'checks':checks,'details':details,'provider_calls':actual['result']['calls'],'scope':'Actual provider adaptation, compiled sources, real media jobs and live loopback HTTP; no model requests made by this audit.','direct_listening':False}
out=w.root/'outputs/stage-b-verification';write_json(out/'acceptance.json',result)
print(json.dumps({'passed':result['passed'],'count':len(checks),'failed':[k for k,v in checks.items() if not v]},ensure_ascii=False))
if not result['passed']:raise SystemExit(1)
