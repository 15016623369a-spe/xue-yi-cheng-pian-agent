/* Shared catalogue and scene matching; drafts remain editable after automation. */
const libraryState={items:[],filter:'all',query:'',page:0,target:null};
const libraryNames={all:'全部资源',voice:'语音素材',md:'MD 文件',image:'图片素材',clip:'视频切片',sound:'音效 / 音乐',generated:'生成视频'};
function safeSource(url){try{const u=new URL(url);return u.protocol==='https:'?esc(u.href):'';}catch{return '';}}
function sourceInfo(source){if(!source?.page)return '本地素材';const url=safeSource(source.page);return `${esc(source.provider||'联网素材')}${source.license?' · '+esc(source.license):''}${url?` · <a href="${url}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>`:''}`;}
function materialControls(){return `<section class="panel material-controls" aria-label="自动匹配素材"><div><h2>让素材跟着讲解走</h2><p class="muted">先匹配资源库，再联网找相关图片。已选素材保留；匹配后仍可逐镜替换。</p></div><div class="material-actions"><label class="check"><input id="material-online" type="checkbox" checked>联网补充（Wikimedia）</label><button class="primary" id="match-materials" ${state.settings.configured?'':'disabled'}>✦ 自动找素材并匹配</button><button class="secondary" id="story-library">打开资源库</button></div><p class="caption">点击后将本次分镜摘要和资源名称／标签交给已配置模型；最多 2 次模型请求、6 组联网搜索。当前按文字描述匹配，尚不代表逐帧识别素材。找不到合适的会留空。</p></section>`;}
function bindMaterials(){
 bind('#match-materials','click',guarded(async()=>{const online=$('#material-online').checked;await save();const d=state.doc;await api(`/api/projects/${d.id}/materials`,{revision:d.revision,online});toast('开始匹配素材，进度见「制作动态」。已有素材会保留。');await poll();}));
 bind('#story-library','click',guarded(()=>openLibrary()));
 document.querySelectorAll('[data-library-event]').forEach(el=>el.onclick=guarded(()=>openLibrary({scene:state.doc.storyboard.scenes[state.scene].id,event:el.dataset.libraryEvent,kind:el.dataset.libraryKind})));
}
async function openLibrary(target=null){
 libraryState.target=target;libraryState.filter=target?.kind==='sound'?'sound':target?'image':'all';libraryState.query='';libraryState.page=0;
 if(!$('#library-dialog').open)$('#library-dialog').showModal();
 $('#library-body').innerHTML='<p class="muted">正在整理本机已登记资源…</p>';
 libraryState.items=(await api('/api/library')).items;renderLibrary();
}
function renderLibrary(){
 const l=libraryState;
 $('#library-body').innerHTML=`<p class="muted">跨项目复用的素材，放在同一个地方。已有文档、音色、上传素材与生成视频会自动归类。</p><div class="library-toolbar"><label>搜索资源<input id="library-query" type="search" value="${esc(l.query)}" placeholder="名称、标签、使用场景…"></label><label>上传到分类<select id="library-upload-kind">${Object.entries(libraryNames).filter(([k])=>!['all','generated'].includes(k)).map(([k,v])=>`<option value="${k}" ${k===(l.filter==='all'?'image':l.filter)?'selected':''}>${v}</option>`).join('')}</select></label><button class="secondary" id="refresh-library">刷新资源</button><label class="upload-control">↥ 上传资源<input id="library-upload" type="file" accept=".md,.txt,.png,.jpg,.jpeg,.webp,.gif,.mp4,.mov,.webm,.wav,.mp3,.m4a,.aac,.ogg,.flac"></label></div><div class="library-tabs" aria-label="资源分类">${Object.entries(libraryNames).map(([k,v])=>`<button class="${l.filter===k?'active':''}" data-library-filter="${k}">${v}<span>${k==='all'?l.items.length:l.items.filter(i=>i.category===k).length}</span></button>`).join('')}</div><p id="library-count" class="caption"></p><div id="library-grid" class="resource-grid"></div><div id="library-pages" class="actions"></div><p class="caption">上传单个文件 ≤100 MB；MD ≤5 万字；语音 ≤30 分钟，切片 ≤120 秒，音效 ≤30 秒。标签越具体，自动匹配越容易。语音资源当前用于管理、试听和下载，切换合成音色仍沿用现有设置。</p>`;
 bind('#refresh-library','click',guarded(async()=>{l.items=(await api('/api/library')).items;renderLibrary();}));
 bind('#library-query','input',()=>{l.query=$('#library-query').value;l.page=0;renderLibraryGrid();});
 document.querySelectorAll('[data-library-filter]').forEach(el=>el.onclick=()=>{l.filter=el.dataset.libraryFilter;l.page=0;renderLibrary();});
 bind('#library-upload','change',guarded(async()=>{
  const file=$('#library-upload').files[0];if(!file)return;
  const query=new URLSearchParams({name:file.name,kind:$('#library-upload-kind').value});$('#library-count').textContent='正在上传并校验资源…';
  const response=await fetch('/api/library/upload?'+query,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Workspace-Token':state.token},body:file});const result=await response.json();if(!response.ok)throw Error(result.error||'上传失败');
  l.items=(await api('/api/library')).items;l.filter=result.category;l.query='';renderLibrary();toast('资源已保存，可添加标签并在项目中复用。');
 }));renderLibraryGrid();
}
function renderLibraryGrid(){
 const l=libraryState,q=l.query.trim().toLocaleLowerCase(),found=l.items.filter(i=>(l.filter==='all'||l.filter===i.category)&&(!q||[i.name,i.context,...i.tags,...i.projects].join(' ').toLocaleLowerCase().includes(q))),page=found.slice(l.page*24,(l.page+1)*24);
 $('#library-count').textContent=`${found.length} 个资源${l.target?' · 选择后放入 '+l.target.scene+' / '+l.target.event:''}`;
 $('#library-grid').innerHTML=page.length?page.map(i=>{
  const preview=i.category==='image'?`<img src="${i.url}" loading="lazy" alt="${esc(i.name)}">`:['clip','generated'].includes(i.category)?`<video src="${i.url}" controls muted preload="none"></video>`:['voice','sound'].includes(i.category)?`<audio src="${i.url}" controls preload="none"></audio>`:'<div class="md-symbol">MD<span>学习笔记</span></div>';
  const canUse=state.doc&&['image','clip','sound'].includes(i.category)&&(!l.target||(l.target.kind==='sound'?i.category==='sound':i.category!=='sound'));
  return `<article class="resource-card"><div class="resource-preview">${preview}</div><b title="${esc(i.name)}">${esc(i.name)}</b><span class="caption">${libraryNames[i.category]} · ${(i.size/1024/1024).toFixed(1)} MB${i.seconds?' · '+i.seconds.toFixed(1)+' 秒':''}</span><p class="resource-tags">${i.tags.map(t=>'#'+esc(t)).join(' ')||'还没有标签'}</p><p class="caption resource-source">${sourceInfo(i.source)}</p><details><summary>名称与匹配标签</summary><label>资源名称<input data-resource-name="${i.id}" value="${esc(i.name)}" maxlength="180"></label><label>标签（逗号分隔）<input data-resource-tags="${i.id}" value="${esc(i.tags.join('，'))}" placeholder="疑惑，重点，队友，DANK1NG"></label><button class="small-button" data-save-resource="${i.id}">保存标签</button><p class="caption">${esc(i.context.slice(0,150))}</p></details><div class="file-links"><a href="${i.url}" download="${esc(i.name)}">下载</a>${i.category==='md'?`<button class="small-button" data-md-project="${i.id}">用于新建视频</button>`:''}${canUse?`<button class="small-button" data-use-resource="${i.id}">${l.target?'用于此处':'加入当前项目'}</button>`:''}</div></article>`;
 }).join(''):'<div class="placeholder">这里还没有匹配的资源。可以上传文件，或换个关键词。</div>';
 $('#library-pages').innerHTML=found.length>24?`<button class="secondary" id="library-prev" ${l.page===0?'disabled':''}>上一页</button><span>${l.page+1} / ${Math.ceil(found.length/24)}</span><button class="secondary" id="library-next" ${(l.page+1)*24>=found.length?'disabled':''}>下一页</button>`:'';
 bind('#library-prev','click',()=>{l.page--;renderLibraryGrid();});bind('#library-next','click',()=>{l.page++;renderLibraryGrid();});
 document.querySelectorAll('[data-save-resource]').forEach(el=>el.onclick=guarded(async()=>{const id=el.dataset.saveResource;await api(`/api/library/${id}/label`,{name:document.querySelector(`[data-resource-name="${id}"]`).value,tags:document.querySelector(`[data-resource-tags="${id}"]`).value.split(/[,，]/).map(t=>t.trim()).filter(Boolean)});l.items=(await api('/api/library')).items;renderLibraryGrid();toast('名称与标签已保存。');}));
 document.querySelectorAll('[data-use-resource]').forEach(el=>el.onclick=guarded(async()=>{
  await save();const pid=state.doc.id,target=l.target;const result=await api(`/api/projects/${pid}/library`,{revision:state.doc.revision,ids:[el.dataset.useResource]});state.doc=result.project;
  if(target){const e=state.doc.storyboard.scenes.find(s=>s.id===target.scene)?.events.find(e=>e.id===target.event);if(!e)throw Error('事件已改变，请重新选择');if(target.kind==='sound'){e.sound='custom';e.sound_asset=result.mapping[el.dataset.useResource];}else{e.meme=result.mapping[el.dataset.useResource];delete e.material_match;}mark();await save();}
  $('#library-dialog').close();render();await refreshProjects();toast(target?'已放入此处，可以试做检查。':'已加入当前项目，可在素材下拉框里选择。');
 }));
 document.querySelectorAll('[data-md-project]').forEach(el=>el.onclick=guarded(async()=>{await save();const doc=await api(`/api/library/${el.dataset.mdProject}/project`,{});$('#library-dialog').close();await openProject(doc.id);}));
}
function initLibrary(){bind('#library-button','click',guarded(()=>openLibrary()));bind('#close-library','click',()=>$('#library-dialog').close());}
