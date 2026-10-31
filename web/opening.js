// Online clips keep their audio; reaction/meme uploads remain a separate path.
let openingSearch={project:null,report:null,criteria:null,activeJob:null};
function resetOpeningSearch(){document.querySelector('#opening-dialog')?.remove();openingSearch={project:null,report:null,criteria:null,activeJob:null};}
function openingControls(){
 const o=state.doc.config.opening||state.doc.opening_selection,r=openingSearch.report;
 return `<section class="panel opening-panel"><div class="panel-header"><h2>本期片头</h2><span class="pill">检索 → 接入 → 导出</span></div><p class="muted">按人物、关键词或原话找网上素材，可自动接入推荐片段，也可自行选择。不会自动加入课程系列说明。</p><div class="actions"><button id="find-opening" class="primary">⌕ 查找片头 / 查看候选</button>${o?'<button id="clear-opening" class="secondary">移除本期片头</button>':''}</div>${r?.criteria?`<p class="note">最近找到 ${r.candidates?.length||0} 条候选。${r.applied&&o?.preview_asset===r.imported?.preview_asset?'已接入，请预览后重新导出。':'搜索结果已保存，可随时打开查看和接入。'}</p>`:''}${o?`<p class="caption">${state.doc.config.opening?'当前片头':'已停用的片头'}：${esc(o.title||(o.asset==='series_opening'?'旧 Transformer 系列片头':'本期片头'))}${o.seconds?' · '+Number(o.seconds).toFixed(2)+' 秒':''}</p>${o.preview_asset?`<video class="opening-preview" controls preload="metadata" src="${assetUrl(o.preview_asset)}"></video>`:''}${o.segments?.length?`<details><summary>片头来源与剪点 · ${o.segments.length} 段</summary>${o.segments.map(s=>`<p class="caption">${s.url?`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`:esc(s.title)}${s.seconds?' · '+Number(s.start??0).toFixed(2)+' 秒起 / '+Number(s.seconds).toFixed(2)+' 秒':''}${s.quote_verified?' · 原话经本机转写定位':''}</p>`).join('')}</details>`:''}`:'<p class="caption">当前未接入片头，导出将直接进入正文。</p>'}</section>`;
}
function bindOpeningControls(){bind('#find-opening','click',guarded(showOpeningSearch));bind('#clear-opening','click',guarded(clearOpening));}
async function showOpeningSearch(){
 if(!state.doc)throw Error('请先打开或新建视频项目，再查找片头。');
 const pid=state.doc.id;
 if(state.dirty)await save();
 if(state.doc?.id!==pid)return;
 const report=await api(`/api/projects/${pid}/openings`);
 if(state.doc?.id!==pid)return;
 openingSearch.project=pid;openingSearch.report=report;
 openingSearch.criteria??=report.criteria||{person:'',keywords:'',quote:'',emotion:'',seconds:7.3,platforms:['bilibili','douyin']};
 $('#opening-dialog')?.remove();
 const c=openingSearch.criteria,dialog=document.createElement('dialog');dialog.id='opening-dialog';dialog.className='opening-dialog';
 dialog.innerHTML=`<div class="panel-header"><div><span class="eyebrow">ONLINE OPENINGS</span><h2>查找、选择并接入片头</h2></div><button id="close-opening" class="small-button">关闭</button></div><p class="muted">“搜索并自动接入”最多尝试前两条候选。有原话先定位，否则试用视频开头；成功后可预览，再导出新成片。也可只查候选。</p><form id="opening-form"><div class="field-row"><label>人物 / 博主<input name="person" maxlength="120" value="${esc(c.person)}" placeholder="例如：DANK1NG"></label><label>内容关键词<input name="keywords" maxlength="120" value="${esc(c.keywords)}" placeholder="例如：看书、高数、打游戏"></label></div><div class="field-row"><label>想找的一句原话<input name="quote" maxlength="120" value="${esc(c.quote)}" placeholder="例如：每次来都在玩游戏"></label><label>情绪 / 场景<input name="emotion" maxlength="120" value="${esc(c.emotion)}" placeholder="例如：疑惑、震惊、催学习"></label></div><div class="field-row"><label>期望片段长度（秒）<input type="number" name="seconds" min=".5" max="30" step=".1" value="${esc(c.seconds)}" required></label><div class="opening-platforms"><label class="check"><input type="checkbox" name="platform" value="bilibili" ${c.platforms.includes('bilibili')?'checked':''}>B站</label><label class="check"><input type="checkbox" name="platform" value="douyin" ${c.platforms.includes('douyin')?'checked':''}>抖音</label></div></div><div class="actions"><button class="primary" id="search-apply-opening" value="search_apply" type="submit">搜索并自动接入片头 →</button><button class="secondary" id="search-opening" value="search" type="submit">只查候选</button></div></form><p id="opening-progress" class="note" role="status"></p><div id="opening-results"></div>`;
 document.body.append(dialog);dialog.showModal();$('#close-opening').onclick=()=>dialog.close();
 $('#opening-form').onsubmit=guarded(async e=>{e.preventDefault();const f=new FormData(e.target);const criteria={person:f.get('person'),keywords:f.get('keywords'),quote:f.get('quote'),emotion:f.get('emotion'),seconds:Number(f.get('seconds')),platforms:f.getAll('platform')};openingSearch.criteria=criteria;await createOpeningJob(e.submitter?.value==='search'?'search':'search_apply',criteria);});
 renderOpeningResults();openingProgress();
}
function openingProgress(){
 const active=state.jobs.find(j=>j.project_id===openingSearch.project&&j.kind==='opening'&&['queued','running','waiting'].includes(j.status));
 const p=$('#opening-progress');if(p)p.textContent=active?.message||'候选是检索线索；原话定位检查起点后最多90秒。成功接入后，已有成片仍需重新导出。';
 document.querySelectorAll('#opening-dialog button:not(#close-opening)').forEach(el=>el.disabled=!!active);
}
function renderOpeningResults(){
 const el=$('#opening-results'),report=openingSearch.report;if(!el||!report)return;
 const candidates=report.candidates||[],applied=!!(report.applied&&state.doc?.config.opening?.preview_asset===report.imported?.preview_asset);
 el.innerHTML=`${report.criteria?`<div class="opening-results-head"><h3>${candidates.length} 条候选 · ${applied?'已接入':'待选择 / 接入'}</h3><span class="caption">${report.cache_hit?'10 分钟内的检索缓存':'公开网页检索'} · 未消费文字模型额度</span></div>`:''}${(report.attempts||[]).filter(a=>!report.applied&&a.status==='failed').map(a=>`<p class="note warning">未接入「${esc(a.title)}」：${esc(a.error)}</p>`).join('')}${(report.warnings||[]).map(w=>`<p class="note warning">${esc(w)}</p>`).join('')}${report.platform_search?`<div class="file-links">${Object.entries(report.platform_search).filter(([k])=>report.criteria.platforms.includes(k)).map(([k,u])=>`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">打开${k==='bilibili'?'B站':'抖音'}搜索 ↗</a>`).join('')}</div>`:''}<div class="opening-candidates">${candidates.map(c=>`<article class="opening-candidate" data-opening="${c.id}"><div class="opening-results-head"><span class="pill">${c.platform==='bilibili'?'B站':'抖音 · 搜索引擎收录'}</span><span class="caption">标题匹配 · 尚未核对视频</span></div><h3><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.title)} ↗</a></h3><p class="caption">${c.reasons.map(esc).join('；')}</p><p class="muted">${esc(c.note)}</p>${(report.attempts||[]).filter(a=>a.candidate===c.id&&a.matches?.length).map(a=>`<div class="note"><b>原话出现多次，选择其中一处：</b><div class="actions">${a.matches.map((m,i)=>`<button type="button" class="secondary" data-import-match data-start="${esc(m.start)}" data-end="${esc(Math.min(90,Math.max(m.end,m.start+(report.criteria?.seconds||7.3))))}">接入第${i+1}处 · ${Number(m.start).toFixed(2)}秒</button>`).join('')}</div></div>`).join('')}<div class="field-row"><label>原视频起点（秒）<input data-cut="start" type="number" min="0" max="35999" step=".1" value="0"></label><label>原视频终点（秒）<input data-cut="end" type="number" min=".5" max="36000" step=".1" value="${esc(report.criteria?.seconds||7.3)}"></label></div><label class="check"><input data-cut="locate" type="checkbox" ${report.criteria?.quote?'checked':'disabled'}>自动定位检索原话（扫描起点后最多 90 秒，时长按检索要求；无需填写终点）</label><div class="actions"><button class="secondary" data-import-opening="replace">用作本期片头</button><button data-import-opening="append">追加为下一段切片</button></div></article>`).join('')}</div>`;
 document.querySelectorAll('[data-import-opening]').forEach(button=>button.onclick=guarded(async()=>{const card=button.closest('[data-opening]');const start=Number(card.querySelector('[data-cut="start"]').value),auto=card.querySelector('[data-cut="locate"]').checked;await createOpeningJob('import',{candidate:card.dataset.opening,start,end:auto?Math.min(36000,start+report.criteria.seconds):Number(card.querySelector('[data-cut="end"]').value),locate_quote:auto,append:button.dataset.importOpening==='append'});}));
 document.querySelectorAll('[data-import-match]').forEach(button=>button.onclick=guarded(()=>createOpeningJob('import',{candidate:button.closest('[data-opening]').dataset.opening,start:Number(button.dataset.start),end:Number(button.dataset.end),locate_quote:false,append:false})));
}
async function createOpeningJob(mode,data){
 if(state.doc?.id!==openingSearch.project)throw Error('项目已切换，请重新打开找片头');
 if(state.dirty)throw Error('请先保存页面修改后再操作片头');
 const j=await api(`/api/projects/${state.doc.id}/openings`,{revision:state.doc.revision,mode,data});openingSearch.activeJob=j.id;
 toast(mode==='search'?'已开始网上检索。':mode==='search_apply'?'已开始检索，找到候选后会尝试接入。':'已开始截取，当前片头暂时保留。');await poll();openingProgress();
}
async function openingJobFinished(job){
 if(state.doc?.id!==job.project_id)return;
 if(openingSearch.activeJob===job.id)openingSearch.activeJob=null;
 if(['completed','needs_input'].includes(job.status)){
  const applied=job.opening_input?.mode==='import'||!!job.result?.applied;
  openingSearch.project=job.project_id;openingSearch.report=await api(`/api/projects/${job.project_id}/openings`);
  if(applied&&!state.dirty){state.doc=await api('/api/projects/'+job.project_id);state.media=null;state.tab='source';render();await refreshProjects();$('#opening-dialog')?.close();toast('本期片头已接入。请播放预览，再审阅并重新导出成片。');}
  else if(applied)toast('片头已保存；页面有未保存修改，请先保留这些修改，再刷新查看。');
  else{await showOpeningSearch();renderOpeningResults();}
 }
 openingProgress();
}
function bindOpeningJobActions(){document.querySelectorAll('[data-view-openings]').forEach(button=>button.onclick=guarded(showOpeningSearch));}
async function clearOpening(){
 if(state.dirty)await save();
 const d=state.doc,v=d.config.voice;
 state.doc=await api(`/api/projects/${d.id}/save`,{revision:d.revision,media:{speed:v.speed,voice_volume:v.volume??1,bgm_db:d.config.bgm?.relative_db??-24,bgm:!!d.config.bgm?.asset,opening:false,forget_opening:true}});
 state.media=null;state.dirty=false;render();await refreshProjects();toast('本期片头已移除。重新导出将直接进入正文，已有成片保留为历史版本。');
}
