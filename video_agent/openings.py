"""Online opening discovery, bounded quote location and original-audio imports."""
from __future__ import annotations

import concurrent.futures
import copy
import fcntl
import html
from html.parser import HTMLParser
import math
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from .alignment import normalize
from .planning import tls_context
from .util import digest, file_hash, probe, read_json, resolve_path, run_command, write_json
from .workspace import Conflict

VERSION='online-openings-v2'
PLATFORMS={'bilibili':'B站','douyin':'抖音'}
SEARCH_HOSTS={'search.bilibili.com','www.sogou.com','www.douyin.com'}
MAX_BYTES=160*1024*1024


def cache_record(path):
    try:
        value=read_json(path)
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def canonical(url):
    if not isinstance(url,str):raise ValueError('请输入抖音或 B站的视频链接')
    p=urllib.parse.urlsplit(html.unescape(url))
    if p.scheme!='https' or p.username or p.password or p.port not in {None,443}:raise ValueError('只支持 HTTPS 公开视频链接')
    if p.hostname in {'www.bilibili.com','bilibili.com'}:
        m=re.fullmatch(r'/video/(BV[A-Za-z0-9]{10}|av\d{1,16})/?',p.path)
        if m:return 'bilibili','https://www.bilibili.com/video/'+m[1]+'/'
    if p.hostname in {'www.douyin.com','douyin.com','m.douyin.com'}:
        m=re.fullmatch(r'/(?:share/)?video/(\d{12,24})/?',p.path)
        if m:return 'douyin','https://www.douyin.com/video/'+m[1]
    raise ValueError('需要具体的视频页；不支持主页、搜索页或任意下载地址')


def criteria(data):
    if not isinstance(data,dict):raise ValueError('请填写片头要求')
    result={}
    for k in ['person','keywords','quote','emotion']:
        v=data.get(k,'')
        if not isinstance(v,str) or len(v)>120 or any(ord(c)<32 for c in v):raise ValueError('片头检索信息每项最多120字，不含换行')
        result[k]=v.strip()
    if not any(result[k] for k in ['person','keywords','quote']):raise ValueError('至少填写人物、关键词或一句原话')
    seconds=data.get('seconds',7.3)
    if type(seconds) not in {int,float} or not math.isfinite(seconds) or not .5<=seconds<=30:raise ValueError('目标片段时长需在0.5–30秒')
    platforms=data.get('platforms',['bilibili','douyin'])
    if not isinstance(platforms,list) or not platforms or any(not isinstance(p,str) or p not in PLATFORMS for p in platforms):raise ValueError('请选择 B站或抖音')
    result.update(seconds=seconds,platforms=list(dict.fromkeys(platforms)))
    return result


def search_links(c):
    query=' '.join(c[k] for k in ['person','keywords','quote','emotion'] if c[k])[:180]
    return {'bilibili':'https://search.bilibili.com/all?'+urllib.parse.urlencode({'keyword':query}),
            'douyin':'https://www.douyin.com/search/'+urllib.parse.quote(query)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def fetch_search(url):
    opener=urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=tls_context()))
    for _ in range(3):
        p=urllib.parse.urlsplit(url)
        if p.scheme!='https' or p.hostname not in SEARCH_HOSTS or p.username or p.password or p.port not in {None,443}:raise ValueError('不支持的检索来源')
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'zh-CN,zh;q=0.9'})
        try:
            with opener.open(req,timeout=15) as r:
                body=r.read(3_000_001)
                if len(body)>3_000_000:raise ValueError('搜索响应过大')
                return body.decode('utf-8',errors='replace')
        except urllib.error.HTTPError as exc:
            if exc.code in {301,302,303,307,308} and exc.headers.get('Location'):
                url=urllib.parse.urljoin(url,exc.headers['Location'])
                if 'antispider' in urllib.parse.urlsplit(url).path:raise ValueError('检索源要求验证码，请打开平台搜索人工核对') from None
                continue
            raise ValueError(f'检索源暂不可访问（HTTP {exc.code}），没有绕过登录或验证码') from None
        except urllib.error.URLError:raise ValueError('检索源网络连接失败，请稍后重试') from None
    raise ValueError('检索重定向次数超限')


class VideoLinks(HTMLParser):
    def __init__(self):
        super().__init__();self.items=[];self.current=None;self.skip=0
    def add(self,url,title):
        try:platform,url=canonical(urllib.parse.urljoin('https://www.bilibili.com',url))
        except (ValueError,TypeError):return
        title=re.sub(r'[\ue000-\uf8ff]','',html.unescape(title));title=' '.join(title.split())[:250]
        if not title:return
        self.items.append({'platform':platform,'url':url,'title':title})
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if tag in {'script','style'}:self.skip+=1
        if d.get('data-url') and d.get('data-title'):
            self.add(d['data-url'],urllib.parse.unquote(d['data-title']))
        if tag=='a':self.current={'url':d.get('href',''),'title':d.get('title',''),'text':[]}
        if tag=='img' and self.current and d.get('alt'):self.current['title']=d['alt']
    def handle_data(self,text):
        if self.current and not self.skip:self.current['text'].append(text)
    def handle_endtag(self,tag):
        if tag in {'script','style'}:self.skip=max(0,self.skip-1)
        if tag=='a' and self.current:
            self.add(self.current['url'],self.current['title'] or ' '.join(self.current['text']));self.current=None


def parse_results(text,platform):
    parser=VideoLinks();parser.feed(text);found={}
    for item in parser.items:
        if item['platform']!=platform:continue
        # Numerical view-count/duration links and ad titles are not video descriptions.
        if not re.search(r'[A-Za-z\u4e00-\u9fff]',item['title']):continue
        old=found.get(item['url'])
        if not old or len(item['title'])>len(old['title']):found[item['url']]=item
    return list(found.values())


def ranked(items,c):
    found={}
    alias=lambda s:normalize(s).replace('dank1ng','danking')
    for item in items:
        title=alias(item['title']);score=0;reasons=[]
        actor=alias(c['person'])
        if actor:
            if actor not in title:continue
            score+=35;reasons.append('标题包含人物')
        tokens=re.findall(r'[A-Za-z0-9]+|[\u4e00-\u9fff]{1,}',c['keywords'])
        keyword_matches=[token for token in tokens if alias(token) in title]
        for token in keyword_matches:score+=15;reasons.append('关键词：'+token)
        quote=alias(c['quote'])
        quote_match=bool(quote and quote in title)
        if tokens and not keyword_matches and not quote_match:continue
        if not actor and not tokens and quote and not quote_match:continue
        if quote_match:score+=40;reasons.append('标题包含原话；仍需核对视频')
        if c['emotion'] and alias(c['emotion']) in title:score+=8;reasons.append('标题包含情绪词')
        if not score:continue
        result={**item,'id':digest(item['url'])[:24],'score':min(100,score),'reasons':reasons,
                'verification':'title_only','quote_verified':False,'emotion_verified':False,'duration_verified':False,
                'note':'仅根据公开标题匹配；未观看或听辨，不代表已定位原话或确认情绪。'}
        if item['url'] not in found or score>found[item['url']]['score']:found[item['url']]=result
    return sorted(found.values(),key=lambda x:(-x['score'],x['url']))[:16]


def folder(ws,pid):
    ws.get(pid)  # Access check even when there is no report yet.
    p=ws.web/'opening-search'/pid;p.mkdir(parents=True,exist_ok=True);return p


def latest(ws,pid):
    p=folder(ws,pid)/'latest.json'
    return read_json(p) if p.is_file() else {'candidates':[],'warnings':[],'criteria':None}


def search(ws,pid,data,notify=lambda *_:None):
    c=criteria(data);cache=folder(ws,pid)/(digest({'version':VERSION,'criteria':c})+'.json')
    if cache.is_file():
        old=cache_record(cache)
        if type(old.get('created')) in {int,float} and 0<=time.time()-old['created']<600 and old.get('sha256')==digest(old.get('report')) and isinstance(old.get('report'),dict):
            result={**old['report'],'cache_hit':True};write_json(folder(ws,pid)/'latest.json',result);return result
    broad=' '.join(c[k] for k in ['person','keywords'] if c[k])
    precise=' '.join(c[k] for k in ['person','keywords','quote','emotion'] if c[k])
    queries=list(dict.fromkeys(q[:180] for q in [precise,broad] if q.strip()))[:2]
    tasks=[]
    for platform in c['platforms']:
        for query in queries:
            url=('https://search.bilibili.com/all?'+urllib.parse.urlencode({'keyword':query}) if platform=='bilibili' else
                 'https://www.sogou.com/web?'+urllib.parse.urlencode({'query':'site:douyin.com/video/ '+query}))
            tasks.append((platform,query,url))
    def lookup(task):
        platform,query,url=task
        try:
            items=parse_results(fetch_search(url),platform)
            return items,{'platform':platform,'query':query,'url':url,'count':len(items)},None
        except Exception as exc:return [],{'platform':platform,'query':query,'url':url,'count':0},f'{PLATFORMS[platform]}：{str(exc)[:200]}'
    notify('正在检索 B站公开视频页和搜索引擎收录的抖音视频')
    items=[];sources=[];warnings=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for found,source,warning in pool.map(lookup,tasks):
            items.extend(found);sources.append(source)
            if warning:warnings.append(warning)
    candidates=ranked(items,c)
    if 'douyin' in c['platforms']:warnings.append('抖音候选来自搜索引擎公开收录，可能不完整；原视频可能要求登录。')
    if not candidates:warnings.append('本次没有找到符合要求的具体视频，未生成占位或虚构链接。可减少原话限制，或打开平台搜索继续核对。')
    result={'version':VERSION,'criteria':c,'created':time.time(),'candidates':candidates,'warnings':warnings,'sources':sources,'platform_search':search_links(c),'cache_hit':False,'new_api_calls':0}
    write_json(cache,{'created':time.time(),'report':result,'sha256':digest(result)})
    write_json(folder(ws,pid)/'latest.json',result)
    return result


def import_options(data):
    if not isinstance(data,dict) or not re.fullmatch(r'[a-f0-9]{24}',str(data.get('candidate',''))):raise ValueError('请选择检索到的视频候选')
    start,end=data.get('start',0),data.get('end',7.3)
    if any(type(x) not in {int,float} or not math.isfinite(x) for x in [start,end]) or not 0<=start<end<=36000 or not .5<=end-start<=30:raise ValueError('截取区间需为0.5–30秒，起点不能为负数')
    for key in ['append','locate_quote']:
        if type(data.get(key,False))!=bool:raise ValueError('片头选项格式不正确')
    return {'candidate':data['candidate'],'start':start,'end':end,'append':data.get('append',False),'locate_quote':data.get('locate_quote',False)}


def search_apply(ws,doc,data,work,notify=lambda *_:None):
    """Try at most two real candidates; a search alone never counts as an import."""
    report=search(ws,doc['id'],data,notify);attempts=[]
    if ws.get(doc['id'])['revision']!=doc['revision']:
        raise Conflict('检索期间项目已修改，请重新检查候选后接入；未覆盖当前版本')
    result={**report,'applied':False,'attempts':attempts}
    for candidate in report.get('candidates',[])[:2]:
        notify('正在尝试接入：'+candidate['title'][:80])
        options={'candidate':candidate['id'],'start':0,'end':report['criteria']['seconds'],
                 'locate_quote':bool(report['criteria']['quote']),'append':False}
        try:
            imported=import_clip(ws,doc,options,work/candidate['id'],notify)
        except Conflict:raise
        except (OSError,ValueError,TimeoutError) as exc:
            attempts.append({'candidate':candidate['id'],'title':candidate['title'],'status':'failed','error':str(exc)[:400],
                             'matches':getattr(exc,'matches',[])})
            continue
        attempts.append({'candidate':candidate['id'],'title':candidate['title'],'status':'applied'})
        result.update(applied=True,imported=imported,revision=imported['revision'])
        break
    # Persist both discovery and application state, so closing the dialog loses nothing.
    write_json(folder(ws,doc['id'])/'latest.json',result)
    return result


def download(ws,url,start,end,target,notify=lambda *_:None):
    canonical(url)
    runtime=read_json(ws.root/'config/local.json')
    exe=runtime.get('yt_dlp') or str(Path(runtime['voice_python']).parent/'yt-dlp')
    if not Path(exe).is_file():raise ValueError('本机未配置 yt-dlp；请在 config/local.json 填写 yt_dlp 可执行文件路径')
    target.mkdir(parents=True,exist_ok=True)
    key=digest({'version':VERSION,'url':url,'start':start,'end':end,'downloader':file_hash(Path(exe)),'ffmpeg':file_hash(Path(runtime['ffmpeg']))})
    cached=target/(key+'.mp4');record=target/(key+'.json')
    if cached.is_file() and record.is_file() and cache_record(record).get('sha256')==file_hash(cached):return cached
    notify('正在获取公开视频的指定区间；不读取浏览器登录信息')
    with tempfile.TemporaryDirectory(prefix='opening-download-',dir=ws.web) as t:
        temp=Path(t);environment={**os.environ}
        if Path('/etc/ssl/cert.pem').is_file():environment['SSL_CERT_FILE']='/etc/ssl/cert.pem'
        command=[exe,'--ignore-config','--no-playlist','--no-warnings','--no-progress','--socket-timeout','15','--retries','1','--fragment-retries','1',
                 '--max-filesize','150M','--ffmpeg-location',runtime['ffmpeg'],'-f','bestvideo[height<=720]+bestaudio/best[height<=720]/best',
                 '--download-sections',f'*{start}-{end}','--force-keyframes-at-cuts','--merge-output-format','mp4','-o',str(temp/'clip.%(ext)s'),url]
        # Do not pass cookies, proxy credentials, arbitrary user flags or disable TLS.
        import subprocess
        with (target/(key+'.log')).open('wb') as log:
            process=subprocess.Popen(command,env=environment,stdout=log,stderr=log,start_new_session=True)
            try:code=process.wait(timeout=180)
            except BaseException as exc:
                try:
                    os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=5)
                except (ProcessLookupError,subprocess.TimeoutExpired):
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                if isinstance(exc,subprocess.TimeoutExpired):raise ValueError('片头获取超时；没有替换当前片头') from None
                raise
        files=[p for p in temp.glob('clip.*') if p.suffix in {'.mp4','.mkv','.webm','.flv'}]
        if code or not files:raise ValueError('该视频暂时无法直接获取（可能需要登录、已下架或平台限制）。可打开原视频核对；当前片头未改变。')
        path=max(files,key=lambda p:p.stat().st_size)
        if not 0<path.stat().st_size<=MAX_BYTES:raise ValueError('片段文件过大，已停止导入')
        info=probe(runtime['ffprobe'],path);duration=float(info['format'].get('duration',0))
        if not 0<duration<=end-start+3:raise ValueError('下载结果没有遵守指定时长，已停止导入')
        temp_out=cached.with_suffix('.tmp.mp4');shutil.copy2(path,temp_out);os.replace(temp_out,cached)
        write_json(record,{'url':url,'start':start,'end':end,'sha256':file_hash(cached)})
    return cached


class QuoteLocationError(ValueError):
    def __init__(self,matches):
        self.matches=matches
        super().__init__(f'原话出现{len(matches)}次，请选择下方具体时间点后接入' if matches else '转写中未找到这句原话；请核对原视频或填写手动区间')


def quote_matches(transcript,quote):
    wanted=normalize(quote)
    if len(wanted)<3:raise ValueError('自动定位的原话至少需要3个有效字符')
    chars=[];times=[]
    for segment in transcript.get('segments',[]):
        for word in segment.get('words',[]):
            text=normalize(word.get('word',word.get('text','')))
            start,end=word.get('start'),word.get('end')
            if type(start) not in {int,float} or type(end) not in {int,float} or not 0<=start<=end:continue
            chars.extend(text);times.extend([(start,end)]*len(text))
    text=''.join(chars);positions=[m.start() for m in re.finditer(re.escape(wanted),text)]
    return [{'start':max(0,times[i][0]-.12),'end':times[i+len(wanted)-1][1]+.18} for i in positions][:20]


def locate(transcript,quote):
    matches=quote_matches(transcript,quote)
    if len(matches)!=1:raise QuoteLocationError(matches)
    return matches[0]['start'],matches[0]['end']


def locate_in_clip(ws,path,quote,work,notify):
    from .engine import runtime_identity
    runtime=read_json(ws.root/'config/local.json');notify('等待本机语音识别，定位指定原话（仅检查选定起点后90秒）')
    work.mkdir(parents=True,exist_ok=True)
    identity={'audio_source':file_hash(path),'asr':runtime_identity(runtime,'asr'),'implementation':file_hash(Path(__file__))}
    cache=ws.web/'opening-asr'/digest(identity);cache.mkdir(parents=True,exist_ok=True)
    with (ws.web/'media.lock').open('a+') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        record=cache/'record.json';transcript=cache/'transcript.json'
        if record.is_file() and transcript.is_file() and cache_record(record).get('sha256')==file_hash(transcript):
            notify('复用已核验的本机转写，正在定位原话')
        else:
            notify('正在本机转写并核对原话')
            audio=work/'locate.wav'
            run_command([runtime['ffmpeg'],'-v','error','-y','-i',str(path),'-vn','-ac','1','-ar','24000','-c:a','pcm_s16le',str(audio)],work/'extract-audio.log')
            request={'task':'asr','hf_home':runtime['hf_home'],'model':runtime['asr_model'],'audio':str(audio),'output_prefix':str(work/'transcript')}
            write_json(work/'asr-request.json',request)
            run_command([runtime['voice_python'],str(ws.root/'workers/mlx_tool.py'),str(work/'asr-request.json')],work/'asr.log',timeout=300)
            read_json(work/'transcript.json')  # Reject malformed output before publishing cache.
            temp=cache/'transcript.tmp';shutil.copy2(work/'transcript.json',temp);os.replace(temp,transcript)
            write_json(record,{'sha256':file_hash(transcript),'identity':identity})
        return locate(read_json(transcript),quote)


def prepare_clip(ws,doc,original,start,end,append,work,notify):
    runtime=read_json(ws.root/'config/local.json')
    old=doc['config'].get('opening') or doc.get('opening_selection')
    previous=resolve_path(ws.path(doc['id']),doc['config']['assets'][old['asset']]) if append and old else None
    key=digest({'source':file_hash(original),'start':start,'end':end,'previous':file_hash(previous) if previous else None,
                'implementation':file_hash(Path(__file__)),'ffmpeg':file_hash(Path(runtime['ffmpeg']))})
    cache=folder(ws,doc['id'])/'prepared'/key;record=cache/'record.json'
    try:
        saved=read_json(record)
        valid=all((cache/name).is_file() and file_hash(cache/name)==sha for name,sha in saved['files'].items()) and set(saved['files'])=={'opening.mov','preview.mp4'}
    except (OSError,ValueError,KeyError,TypeError):valid=False
    if valid:
        notify('复用已核验的原声片头媒体')
        for name in saved['files']:shutil.copy2(cache/name,work/name)
        return work/'opening.mov',work/'preview.mp4',saved['piece_seconds'],saved['seconds']
    notify('正在制作保留原声的片头，并核对时长和音轨')
    clip=work/'piece.mov'
    vf='scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30,setpts=N/(30*TB)'
    run_command([runtime['ffmpeg'],'-v','error','-y','-ss',str(start),'-i',str(original),'-t',str(end-start),'-map','0:v:0','-map','0:a:0','-vf',vf,
                 '-af','aresample=24000,asetpts=N/SR/TB','-map_metadata','-1','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','pcm_s16le','-ac','1','-ar','24000',str(clip)],work/'prepare.log')
    piece_seconds=float(probe(runtime['ffprobe'],clip)['format']['duration'])
    if not .5<=piece_seconds<=30.2:raise ValueError('实际片段时长不符合要求，未替换片头')
    pieces=[clip];old=doc['config'].get('opening') or doc.get('opening_selection')
    if append and old:
        old_path=resolve_path(ws.path(doc['id']),doc['config']['assets'][old['asset']])
        old_seconds=float(probe(runtime['ffprobe'],old_path)['format']['duration'])
        if old_seconds+piece_seconds>60:raise ValueError('串联片头总时长超过60秒，请减少片段')
        previous=work/'previous.mov'
        run_command([runtime['ffmpeg'],'-v','error','-y','-i',str(old_path),'-map','0:v:0','-map','0:a:0','-vf',vf,'-af','aresample=24000,asetpts=N/SR/TB',
                     '-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','pcm_s16le','-ac','1','-ar','24000',str(previous)],work/'previous.log')
        pieces=[previous,clip]
    result=work/'opening.mov'
    if len(pieces)==1:shutil.copy2(clip,result)
    else:
        run_command([runtime['ffmpeg'],'-v','error','-y','-i',str(pieces[0]),'-i',str(pieces[1]),'-filter_complex','[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]',
                     '-map','[v]','-map','[a]','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','pcm_s16le',str(result)],work/'concat.log')
    seconds=float(probe(runtime['ffprobe'],result)['format']['duration'])
    preview=work/'preview.mp4'
    run_command([runtime['ffmpeg'],'-v','error','-y','-i',str(result),'-c:v','copy','-c:a','aac','-b:a','128k','-movflags','+faststart',str(preview)],work/'preview.log')
    run_command([runtime['ffmpeg'],'-v','error','-i',str(result),'-f','null','-'],work/'decode.log')
    cache.mkdir(parents=True,exist_ok=True)
    for path in [result,preview]:
        temp=cache/(path.name+'.tmp');shutil.copy2(path,temp);os.replace(temp,cache/path.name)
    write_json(record,{'files':{p.name:file_hash(p) for p in [result,preview]},'piece_seconds':piece_seconds,'seconds':seconds})
    return result,preview,piece_seconds,seconds


def import_clip(ws,doc,options,work,notify=lambda *_:None):
    work.mkdir(parents=True,exist_ok=True)
    o=import_options(options);report=latest(ws,doc['id'])
    candidate=next((x for x in report['candidates'] if x['id']==o['candidate']),None)
    if not candidate:raise ValueError('候选已改变或不存在，请重新检索')
    _,url=canonical(candidate['url']);runtime=read_json(ws.root/'config/local.json')
    start,end=o['start'],o['end'];source_start=start;quote_verified=False
    if o['locate_quote']:
        quote=(report.get('criteria') or {}).get('quote','')
        if len(normalize(quote))<3:raise ValueError('请填写至少3字的原话，或关闭自动定位后自行设置剪点')
        original=download(ws,url,start,min(start+90,36000),folder(ws,doc['id'])/'downloads',notify)
        a,b=locate_in_clip(ws,original,quote,work,notify)
        # The requested target length includes the whole quote; never cut its last word.
        end=min(90,max(b,a+(report.get('criteria') or {}).get('seconds',7.3)))
        if end-a>30:raise ValueError('原话超过30秒，请缩短定位原话')
        start=a;source_start=o['start']+a;quote_verified=True
    else:original=download(ws,url,start,end,folder(ws,doc['id'])/'downloads',notify);start=0;end=o['end']-o['start']
    info=probe(runtime['ffprobe'],original)
    if not any(s.get('codec_type')=='audio' for s in info.get('streams',[])):raise ValueError('该片段没有音轨，不能作为原声片头')
    result,preview,piece_seconds,seconds=prepare_clip(ws,doc,original,start,end,o['append'],work,notify)
    old=doc['config'].get('opening') or doc.get('opening_selection')
    sha=file_hash(result);key='opening_'+sha[:16];preview_key=key+'_preview'
    with ws.locked_project(doc['id']):
        current=ws.get(doc['id'])
        if current['revision']!=doc['revision']:raise Conflict('制作期间项目已修改，片头结果已保留，未覆盖当前项目')
        dest=ws.path(doc['id'])/'assets/openings'/sha;dest.mkdir(parents=True,exist_ok=True)
        for path in [result,preview]:
            temp=dest/(path.name+'.tmp');shutil.copy2(path,temp);os.replace(temp,dest/path.name)
        segment={'url':url,'platform':candidate['platform'],'title':candidate['title'],'start':round(source_start,3),'seconds':round(piece_seconds,3),'quote_verified':quote_verified,'quote':(report.get('criteria') or {}).get('quote','') if quote_verified else ''}
        segments=copy.deepcopy(old.get('segments',[])) if o['append'] and old else []
        if o['append'] and old and not segments:segments=[{'title':old.get('title','原有片头'),'asset':old['asset']}]
        segments.append(segment)
        for asset,path in [(key,result),(preview_key,preview)]:
            current['config']['assets'][asset]=str((dest/path.name).relative_to(ws.path(doc['id'])))
            current['config'].setdefault('asset_metadata',{})[asset]={'name':candidate['title'],'kind':'video','seconds':seconds,'sha256':file_hash(path),'source':{'provider':PLATFORMS[candidate['platform']],'page':url,'license':'由使用者确认素材使用范围'}}
        current['config']['opening']={'asset':key,'preview_asset':preview_key,'title':('多切片片头 · ' if len(segments)>1 else '')+candidate['title'],
                                      'description':'网上候选素材截取，保留原声；具体来源和剪点见 segments。','captions':old.get('captions',[]) if o['append'] and old else [],'segments':segments,'seconds':seconds}
        if current.get('storyboard'):current['config']=ws.compile(current)
        current.update(revision=current['revision']+1,updated=time.time(),approved=False,review={});ws._write(ws.path(doc['id']),current)
    imported={'revision':current['revision'],'candidate':candidate,'seconds':seconds,'quote_verified':quote_verified,'appended':bool(o['append'] and old),'preview_asset':preview_key,'source':segment}
    # Manual choice and automatic choice share the same durable application state.
    write_json(folder(ws,doc['id'])/'latest.json',{**report,'applied':True,'imported':imported,'revision':current['revision']})
    return imported
