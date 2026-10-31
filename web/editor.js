// Per-episode editorial choices and reversible visual-asset processing.
function editorialControls(){
 const d=state.doc;
 return `<section class="panel"><h2>系列与本期标题</h2><label>系列名称<input id="series-name" maxlength="40" value="${esc(d.config.theme?.series||'')}"></label><div class="actions"><button class="small-button" id="remember-series">设为新项目默认系列</button></div><label>本期标题<input id="episode-title" maxlength="120" value="${esc(d.title)}"></label><p class="caption">系列名固定；每期标题按实际内容单独生成。已有讲稿时优先依据讲稿，无讲稿时依据导入笔记。</p><button class="primary" id="generate-titles" ${state.settings.configured?'':'disabled'}>✦ 根据本期内容起标题</button><div id="title-candidates"></div><label class="check"><input id="auto-clean-online" type="checkbox" ${state.preferences?.auto_clean_online!==false?'checked':''}>网上素材接入时自动识别并处理平台文字水印</label><p class="caption">本机抽帧识别角落的平台标记与账号文字；未识别或处理不确定时会提示，可在素材旁框选。原件保留，处理结果需预览。</p></section>`;
}
function bindEditorial(){
 bind('#series-name','input',()=>{state.doc.config.theme.series=$('#series-name').value;mark();});
 bind('#episode-title','input',()=>{state.doc.title=$('#episode-title').value;if(state.doc.storyboard)state.doc.storyboard.title=state.doc.title;mark();});
 bind('#remember-series','click',guarded(async()=>{await save();state.preferences=await api('/api/preferences',{series_name:state.doc.config.theme.series});toast('已记住系列名，之后新建项目会使用；既有项目保持各自设置。');}));
 bind('#auto-clean-online','change',guarded(async()=>{state.preferences=await api('/api/preferences',{auto_clean_online:$('#auto-clean-online').checked});toast('网上素材的自动水印处理偏好已保存。');}));
 bind('#generate-titles','click',guarded(async()=>{await save();await startJob('titles');}));
 const pid=state.doc.id;
 api(`/api/projects/${pid}/titles`).then(result=>{if(state.doc?.id===pid)showTitles(result);}).catch(()=>{});
}
function showTitles(result){
 const el=$('#title-candidates');if(!el)return;
 el.innerHTML=result.titles?.length?`<p class="caption">${result.stale?'内容或系列已修改，请重新起标题。':'第一条为模型推荐，可选择或手动修改。'}${result.cache_hit?' · 复用已检查的结果':''}</p>${result.titles.map((t,i)=>`<article class="title-candidate"><b>${i===0?'推荐 · ':''}${esc(t.title)}</b><p class="caption">${esc(t.reason)}</p><button class="small-button" data-choose-title="${t.id}" ${result.stale?'disabled':''}>采用这个标题</button></article>`).join('')}`:'';
 el.querySelectorAll('[data-choose-title]').forEach(button=>button.onclick=guarded(async()=>{await save();const d=state.doc;state.doc=await api(`/api/projects/${d.id}/titles/apply`,{revision:d.revision,candidate:button.dataset.chooseTitle});state.dirty=false;render();await refreshProjects();toast('本期标题已更新。已生成的封面和成片保留，重新生成时使用新标题。');}));
}
function cleanupLabel(report){return {processed:'已处理水印区域 · 请预览',not_detected:'抽样未识别到水印 · 可框选',needs_review:'水印待处理 · 可框选',restored:'已恢复原件',disabled:'自动清理已关闭'}[report?.status]||'可自动识别或框选水印';}
function cleanupButton(key){
 if(!key)return '';
 return `<button class="small-button" data-clean-asset="${esc(key)}">水印处理 / 恢复原件</button><span class="caption">${esc(cleanupLabel(state.doc.config.asset_metadata?.[key]?.cleanup))}</span>`;
}
function bindCleanupButtons(){document.querySelectorAll('[data-clean-asset]').forEach(button=>button.onclick=guarded(()=>openCleanup(button.dataset.cleanAsset)));}
async function openCleanup(key){
 await save();const d=state.doc,pid=d.id,meta=d.config.asset_metadata?.[key]||{},path=d.config.assets[key]||'';
 $('#cleanup-dialog')?.remove();const dialog=document.createElement('dialog');dialog.id='cleanup-dialog';dialog.className='cleanup-dialog';
 const opening=d.config.opening||d.opening_selection,viewKey=opening?.asset===key?opening.preview_asset||key:key;
 const video=meta.kind==='video'||/\.(mp4|mov|webm|gif)$/i.test(path);
 dialog.innerHTML=`<div class="panel-header"><h2>素材水印处理</h2><button id="close-cleanup" class="small-button">关闭</button></div><p class="muted">${esc(meta.name||key)} · ${esc(cleanupLabel(meta.cleanup))}</p><p class="caption">可先自动识别，或拖动框选水印。框选会处理选中区域；可清空重选或恢复原件。视频先暂停在看得清水印的位置。</p><div class="cleanup-preview"><div class="cleanup-frame">${video?`<video id="cleanup-media" src="${assetUrl(viewKey)}" controls preload="metadata"></video>`:`<img id="cleanup-media" src="${assetUrl(viewKey)}" alt="待处理素材">`}<canvas id="cleanup-canvas" class="hidden" aria-label="拖动框选水印"></canvas></div></div><div class="actions"><button class="secondary" id="draw-cleanup">开始框选</button><button class="small-button" id="clear-cleanup-boxes">清空框选</button><span id="cleanup-count" class="caption">已框选 0 处</span></div><div class="actions"><button class="primary" id="auto-clean-asset">自动识别并清理</button><button class="secondary" id="apply-cleanup-boxes">清理框选区域</button><button class="small-button" id="restore-clean-asset" ${meta.cleanup_original_asset?'':'disabled'}>恢复原件</button></div><p class="caption">角落文字水印采用周围像素修补；大型、居中或移动水印可能处理不完整，自动结果不能当作无水印保证。原声与原件分别保留。</p>`;
 document.body.append(dialog);dialog.showModal();
 const boxes=[],canvas=$('#cleanup-canvas'),ctx=canvas.getContext('2d'),media=$('#cleanup-media');let anchor=null;
 function draw(){const bounds=media.getBoundingClientRect();canvas.width=Math.round(bounds.width);canvas.height=Math.round(bounds.height);ctx.strokeStyle='#e55c27';ctx.lineWidth=2;boxes.forEach(b=>ctx.strokeRect(b[0]*canvas.width,b[1]*canvas.height,b[2]*canvas.width,b[3]*canvas.height));$('#cleanup-count').textContent=`已框选 ${boxes.length} 处`;}
 function point(e){const r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];}
 bind('#close-cleanup','click',()=>dialog.close());
 bind('#draw-cleanup','click',()=>{if(video)media.pause();canvas.classList.remove('hidden');draw();});
 bind('#clear-cleanup-boxes','click',()=>{boxes.length=0;draw();});
 canvas.addEventListener('pointerdown',e=>{if(boxes.length>=8)return;anchor=point(e);canvas.setPointerCapture(e.pointerId);});
 canvas.addEventListener('pointerup',e=>{if(!anchor)return;const p=point(e),box=[Math.min(anchor[0],p[0]),Math.min(anchor[1],p[1]),Math.abs(anchor[0]-p[0]),Math.abs(anchor[1]-p[1])];anchor=null;if(box[2]>=.003&&box[3]>=.003)boxes.push(box);draw();});
 canvas.addEventListener('pointercancel',()=>{anchor=null;});
 async function submit(mode){if(state.doc?.id!==pid||state.dirty)throw Error('项目已改变，请重新打开素材处理');await api(`/api/projects/${pid}/cleanup`,{revision:state.doc.revision,asset:key,mode,regions:boxes});dialog.close();toast('素材处理已开始，进度见制作动态。');await poll();}
 bind('#auto-clean-asset','click',guarded(()=>submit('auto')));bind('#apply-cleanup-boxes','click',guarded(()=>submit('manual')));bind('#restore-clean-asset','click',guarded(()=>submit('restore')));
}
