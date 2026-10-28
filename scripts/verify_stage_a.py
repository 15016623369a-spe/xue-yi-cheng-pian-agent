"""Real media acceptance matrix. Requires configured local browser/MLX access."""
from pathlib import Path
import copy
import json
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from video_agent.util import digest, file_hash, read_json, write_json

project=ROOT/'projects/lesson04-validation'
report_dir=ROOT/'outputs/stage-a-verification'
report_dir.mkdir(parents=True,exist_ok=True)
original=read_json(project/'project.json')
checks={}
runs={}


def execute(label,config_name,extra=(),expected=0):
    log=report_dir/(label+'.log')
    start=time.monotonic()
    with log.open('w') as f:
        result=subprocess.run([sys.executable,'-m','video_agent','run',str(project/config_name),*extra],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
    if result.returncode!=expected:
        raise RuntimeError(f'{label}: exit {result.returncode}; expected {expected}. See {log}')
    if expected:
        data=read_json(project/'.agent/last-run.json')
    else:
        data=read_json(project/'outputs'/f'{Path(config_name).stem}-latest.json')
    runs[label]={'seconds':round(time.monotonic()-start,3),'run_id':data['run_id'],
                 'counts':data.get('counts'),'stages':data['stages'],'log':str(log),
                 'output_directory':data.get('output_directory')}
    print(json.dumps({'case':label,'seconds':runs[label]['seconds'],'counts':data.get('counts')},ensure_ascii=False),flush=True)
    return data


baseline=execute('baseline','project.json')
repeat=execute('repeat','project.json')
checks['repeat_all_20_stages_cached']=repeat['counts']=={'built':0,'cached':20,'failed':0}

edited=execute('speech-edit','speech-edit.json')
for sid in ['S03','S08']:
    a=next(s for s in baseline['scene_artifacts'] if s['id']==sid)
    b=next(s for s in edited['scene_artifacts'] if s['id']==sid)
    checks[f'speech_edit_{sid}_all_stage_keys_unchanged']=a['keys']==b['keys']
    checks[f'speech_edit_{sid}_movie_sha_unchanged']=file_hash(Path(a['movie']))==file_hash(Path(b['movie']))
a=next(s for s in baseline['scene_artifacts'] if s['id']=='S05')
b=next(s for s in edited['scene_artifacts'] if s['id']=='S05')
checks['speech_edit_S05_all_six_keys_changed']=all(a['keys'][k]!=b['keys'][k] for k in a['keys'])
checks['speech_edit_S05_real_local_synthesis']=b['voice_method']=='local_qwen3_synthesis'
affected={f'{stage}:S05' for stage in ['voice','asr','prepare','render','mix','mux']}|{'export','audit'}
checks['speech_edit_rebuilds_only_affected_stages']=set(s['stage'] for s in edited['stages'] if s['status']=='built')<=affected
ta=read_json(Path(baseline['deliverables']['timeline.json']))
tb=read_json(Path(edited['deliverables']['timeline.json']))
checks['speech_edit_S08_start_retimed']=next(s for s in ta['scenes'] if s['id']=='S08')['start']!=next(s for s in tb['scenes'] if s['id']=='S08')['start']

visual=copy.deepcopy(original)
visual['scenes'][0]['visual']['conclusion']='先完成扩宽与收回，位置数量保持不变。'
write_json(project/'visual-edit.json',visual)
v=execute('visual-edit','visual-edit.json')
checks['visual_edit_only_render_mux_export_audit']=set(s['stage'] for s in v['stages'] if s['status']=='built')<={'render:S03','mux:S03','export','audit'}
checks['visual_edit_export_changed']=v['export_key']!=baseline['export_key']

music=copy.deepcopy(original)
music['bgm']['relative_db']=-28
write_json(project/'bgm-edit.json',music)
m=execute('bgm-edit','bgm-edit.json')
checks['bgm_edit_only_export_audit']=set(s['stage'] for s in m['stages'] if s['status']=='built')<={'export','audit'}
checks['bgm_edit_export_changed']=m['export_key']!=baseline['export_key']

fault=copy.deepcopy(original)
fault['scenes'][1]['visual']['note']+=' 故障恢复验收 '+str(time.time_ns())
write_json(project/'failure-recovery.json',fault)
marker=project/'.agent/injected'/digest('render:S05')
marker.unlink(missing_ok=True)
failure=execute('controlled-failure','failure-recovery.json',['--fail-once','render:S05'],expected=1)
checks['failure_stage_recorded']=failure['status']=='failed' and failure['stages'][-1]['stage']=='render:S05'
recovered=execute('failure-recovery','failure-recovery.json')
checks['failure_recovery_reuses_prior_stages']=all(s['status']=='cached' for s in recovered['stages'] if s['stage'] in ['voice:S03','asr:S03','prepare:S03','render:S03','mix:S03','mux:S03','voice:S05','asr:S05','prepare:S05'])
checks['failure_recovery_completed']=recovered['status']=='completed'

for label,data in [('baseline',baseline),('speech-edit',edited),('visual-edit',v),('bgm-edit',m),('failure-recovery',recovered)]:
    audit=read_json(Path(data['deliverables']['audit.json']))
    checks[f'{label}_technical_and_audio_sync_pass']=audit['passed']

report={'passed':all(checks.values()),'checks':checks,'runs':runs,
        'baseline_output':baseline['output_directory'],'speech_edit_output':edited['output_directory'],
        'voice_and_asr_cloud_calls':0,'controlled_failure_is_injected':True,
        'scope':'3 real Lesson04 scenes; no web UI or automatic knowledge adaptation; no continuous watch or direct listening'}
write_json(report_dir/'acceptance.json',report)
print(json.dumps({'passed':report['passed'],'checks':len(checks),'report':str(report_dir/'acceptance.json')},ensure_ascii=False),flush=True)
if not report['passed']:
    raise SystemExit('Acceptance failed: '+str([k for k,v in checks.items() if not v]))
