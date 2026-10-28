const video=document.querySelector('#preview-video');
const status=document.querySelector('#player-status');
const playButton=document.querySelector('#play-video');
const reloadButton=document.querySelector('#reload-video');
const download=document.querySelector('#download-video');
let loadingTimer;
function message(text,error=false){status.textContent=text;status.classList.toggle('error',error);}
function stopWaiting(){clearTimeout(loadingTimer);}
function watchLoading(){
  stopWaiting();
  loadingTimer=setTimeout(()=>message('加载时间较长，可以重新加载，或下载视频后播放。',true),15000);
}
function ready(){
  stopWaiting();playButton.disabled=false;
  const duration=Number.isFinite(video.duration)?`（${video.duration.toFixed(1)} 秒）`:'';
  message(`视频已就绪${duration}，点击“播放”开始观看。`);
}
video.addEventListener('loadedmetadata',ready);
video.addEventListener('canplay',()=>{stopWaiting();if(video.paused&&!video.error)ready();});
video.addEventListener('playing',()=>{stopWaiting();playButton.textContent='暂停';message('正在播放');});
video.addEventListener('pause',()=>{playButton.textContent=video.ended?'重播':'播放';if(!video.error)message(video.ended?'播放结束，可以再次播放。':'已暂停');});
video.addEventListener('ended',()=>{playButton.textContent='重播';message('播放结束，可以再次播放。');});
video.addEventListener('waiting',()=>{message('正在缓冲…');watchLoading();});
video.addEventListener('error',()=>{
  stopWaiting();playButton.disabled=true;
  const errors={2:'视频读取中断',3:'浏览器未能解码视频',4:'浏览器未能加载这个视频'};
  message(`${errors[video.error?.code]||'视频未能加载'}，请重新加载，或下载后播放。`,true);
});
playButton.addEventListener('click',async()=>{
  if(!video.paused){video.pause();return;}
  try{await video.play();}catch(error){
    message(error.name==='NotAllowedError'?'请点击视频画面里的播放按钮开始观看。':'播放未能开始，请重新加载，或下载视频后播放。',true);
  }
});
reloadButton.addEventListener('click',()=>{playButton.disabled=true;playButton.textContent='播放';message('正在重新加载视频…');video.load();watchLoading();});

async function showVersionContext(job){
  if(!/^web-[a-f0-9]{12}$/.test(job.project_id))return;
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),10000);
  try{
    const [doc,jobs]=await Promise.all([`/api/projects/${job.project_id}`,`/api/jobs?project=${job.project_id}`].map(async url=>{
      const response=await fetch(url,{signal:controller.signal});
      if(!response.ok)throw Error('版本信息暂不可用');
      return response.json();
    }));
    const latest=jobs.find(item=>item.kind==='render'&&item.scene_id===job.scene_id&&item.status==='completed'&&item.created>job.created&&item.result?.files?.includes('video.mp4'));
    const ownSpeed=job.render_settings?.voice_speed,currentSpeed=doc.config?.voice?.speed;
    let text='';
    if(job.revision!==doc.revision)text=`这是 r${job.revision} 的历史${job.scene_id?'试做':'成片'}，当前草稿为 r${doc.revision}。`;
    if(Number.isFinite(ownSpeed)&&Number.isFinite(currentSpeed)&&ownSpeed!==currentSpeed)text+=`本片语速 ${ownSpeed} 倍，当前设置 ${currentSpeed} 倍。`;
    if(text)text+=' 已生成的视频保留制作时的语速，保存修改后需重新试做。';
    if(latest){
      if(!text)text='这个镜头已有更新的试做。';
      const link=document.querySelector('#latest-preview');
      const speed=latest.render_settings?.voice_speed;
      link.href='/player.html?job='+latest.id;
      link.textContent=`打开最新${job.scene_id||'成片'}${job.scene_id?'试做':''}（r${latest.revision}${Number.isFinite(speed)?' · '+speed+' 倍':''}）`;
      link.classList.remove('hidden');
    }
    if(text){document.querySelector('#version-notice').textContent=text;document.querySelector('#version-context').classList.remove('hidden');}
  }catch(error){/* Version context must never prevent playback. */}
  finally{clearTimeout(timer);}
}

async function openPreview(){
  const jobId=new URLSearchParams(location.search).get('job');
  if(!/^[a-f0-9]{32}$/.test(jobId||''))throw Error('播放地址不完整，请返回工作台重新点击“播放”。');
  const controller=new AbortController();
  const requestTimer=setTimeout(()=>controller.abort(),10000);
  let job;
  try{
    const response=await fetch(`/api/jobs/${jobId}`,{signal:controller.signal});
    job=await response.json();
    if(!response.ok)throw Error(job.error||'未找到这个制作任务。');
  }finally{clearTimeout(requestTimer);}
  if(job.status!=='completed'||job.kind!=='render'||!job.result?.files?.includes('video.mp4'))throw Error('这个任务还没有可播放的视频，请返回工作台查看制作状态。');
  const title=job.scene_id?`${job.scene_id} · 试做预览`:'成片预览';
  document.querySelector('#player-title').textContent=title;
  document.title=title+' · 学以成片';
  const speed=job.render_settings?.voice_speed;
  document.querySelector('#player-version').textContent=`制作版本 r${job.revision}${Number.isFinite(speed)?' · 本片语速 '+speed+' 倍':''}`;
  if(/^web-[a-f0-9]{12}$/.test(job.project_id))document.querySelector('#back-link').href='/?project='+job.project_id;
  const source=`/api/jobs/${jobId}/files/video.mp4`;
  download.href=source;download.download=`${job.scene_id||'成片'}-r${job.revision}.mp4`;download.classList.remove('hidden');
  reloadButton.disabled=false;
  message('正在加载视频…');watchLoading();
  video.src=source;
  void showVersionContext(job);
}
openPreview().catch(error=>{stopWaiting();message(error.name==='AbortError'?'读取视频信息超时，请刷新播放页重试。':error.message,true);});
