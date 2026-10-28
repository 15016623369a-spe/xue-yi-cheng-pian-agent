import React from 'react';
import {Cover} from './Covers';
import {AbsoluteFill,Composition,Img,Loop,OffthreadVideo,Sequence,interpolate,registerRoot,staticFile,useCurrentFrame} from 'remotion';

const C={ink:'#17263b',blue:'#3467de',green:'#138875',red:'#e44756',line:'#dce1e9',muted:'#6b7689'};
const card:React.CSSProperties={border:`2px solid ${C.line}`,borderRadius:17,padding:27,background:'white',boxSizing:'border-box'};
const fade=(f:number,i=0)=>interpolate(f,[i,i+10],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});

const Board:React.FC<any>=({v,f,assets})=>{
  if(v.template==='figure')return <><div style={{...card,padding:12,height:440,display:'flex',alignItems:'center',justifyContent:'center'}}><Img src={staticFile(assets[v.asset])} style={{width:'100%',height:'100%',objectFit:'contain'}}/></div><div style={{fontSize:27,color:C.muted,marginTop:12}}>{v.caption}</div><Note>{v.note}</Note></>;
  if(v.template==='flow')return <>
    <div style={{display:'flex',gap:16,alignItems:'center'}}>{v.steps.map((s:any,i:number)=><React.Fragment key={i}>{i>0&&<span style={{fontSize:43,color:C.muted}}>→</span>}<div style={{...card,flex:1,minWidth:0,borderColor:i===1?C.red:C.blue,textAlign:'center',opacity:fade(f,i*9)}}><div style={{fontSize:33,fontWeight:850,color:i===1?C.red:C.blue}}>{s.label}</div>{s.bars&&<div style={{height:150,display:'flex',gap:5,justifyContent:'center',alignItems:'center'}}>{Array.from({length:s.bars},(_,k)=><div key={k} style={{width:s.bars>10?12:24,height:78,background:i===1?'#ed8290':'#7c9ee8',borderRadius:4}}/>)}</div>}<div style={{fontSize:31,fontFamily:'Menlo,monospace',marginTop:20,whiteSpace:'pre-line'}}>{s.detail}</div></div></React.Fragment>)}</div>
    <Ribbon>{v.conclusion}</Ribbon><Note>{v.note}</Note>
  </>;
  if(v.template==='branch')return <>
    <div style={{height:230,position:'relative'}}>
      <svg width="1290" height="230" style={{position:'absolute'}}><defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill={C.blue}/></marker><marker id="b" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill={C.red}/></marker></defs><path d="M100 150 H1170" stroke={C.blue} strokeWidth="4" fill="none" markerEnd="url(#a)"/><path d="M145 150 V43 H1080 V117" fill="none" stroke={C.red} strokeWidth="4" markerEnd="url(#b)"/></svg>
      <div style={{position:'absolute',top:7,left:270,width:690,textAlign:'center',background:'#fdfdfe',fontSize:30,fontWeight:850,color:C.red}}>{v.bypass}</div>
      {[{x:0,w:110,text:v.input},...v.steps.map((text:string,i:number)=>({x:240+i*340,w:240,text})),{x:1170,w:110,text:v.output}].map((s:any,i:number)=><div key={i} style={{...card,position:'absolute',left:s.x,top:120,width:s.w,height:65,padding:10,textAlign:'center',fontSize:31,fontWeight:850,color:C.blue}}>{s.text}</div>)}
      <div style={{position:'absolute',left:1047,top:116,width:65,height:65,display:'grid',placeItems:'center',borderRadius:40,background:'#fff0f2',border:`3px solid ${C.red}`,fontSize:44,fontWeight:850,color:C.red}}>+</div>
    </div>
    <div style={{...card,fontFamily:'Menlo,monospace',fontSize:37,background:'#edf3ff'}}>{v.formula}</div><Ribbon>{v.conclusion}</Ribbon><Note>{v.note}</Note>
  </>;
  return <>
    {v.lines.map((line:string,i:number)=><div key={i} style={{...card,fontSize:i===0?36:30,fontFamily:'Menlo,Hiragino Sans GB,monospace',lineHeight:1.65,whiteSpace:'pre-line',marginBottom:22,background:i===v.lines.length-1?'#eaf5ef':'white',opacity:fade(f,i*8)}}>{line}</div>)}
    <Note>{v.note}</Note>
  </>;
};
const Ribbon:React.FC<any>=({children})=>children?<div style={{fontSize:35,fontWeight:850,color:C.green,background:'#edf7f0',borderLeft:`7px solid ${C.green}`,padding:'18px 23px',borderRadius:8,marginTop:25}}>{children}</div>:null;
const Note:React.FC<any>=({children})=>children?<div style={{fontSize:25,color:C.muted,lineHeight:1.5,marginTop:22}}>{children}</div>:null;

const Scene:React.FC<any>=({scene,settings,theme,assets})=>{
  const f=useCurrentFrame();
  const cap=scene.captions.find((c:any)=>f>=c.start&&f<c.end);
  const event=scene.events.filter((e:any)=>f>=e.start&&f<e.start+e.duration).at(-1);
  const ef=event?f-event.start:0;
  const scale=interpolate(ef,[0,5,9],[.72,1.04,1],{extrapolateRight:'clamp'});
  const z=settings.width/1920;
  return <AbsoluteFill style={{background:'#fdfdfe'}}><div style={{position:'absolute',width:1920,height:1080,transform:`scale(${z})`,transformOrigin:'top left',fontFamily:'PingFang SC,Hiragino Sans GB,sans-serif',color:C.ink}}>
    <div style={{position:'absolute',left:62,right:62,top:36,borderBottom:`2px solid ${C.line}`,height:62,display:'flex',justifyContent:'space-between',fontSize:28,fontWeight:800}}><span>{theme.label??'学习内容'}</span><span style={{color:C.blue}}>{theme.series??''}</span></div>
    <div style={{position:'absolute',left:65,top:125,fontSize:25,fontWeight:800,color:C.red,background:'#fff0f4',padding:'9px 16px',borderRadius:7}}>{scene.tag}</div>
    <div style={{position:'absolute',left:65,top:181,fontSize:52,fontWeight:900}}>{scene.title}</div>
    <div style={{position:'absolute',left:65,top:325,width:1320}}><Board v={scene.visual} f={f} assets={assets}/></div>
    {event?.popup&&<div style={{position:'absolute',top:256,left:75,width:1310,textAlign:'center',transform:`scale(${scale})`,opacity:fade(ef),fontSize:35,fontWeight:900,zIndex:5}}><span style={{background:'#ffe15a',padding:'9px 24px',borderRadius:8,boxShadow:'5px 5px 0 #1c2534'}}>{event.popup}</span></div>}
    <div style={{position:'absolute',left:1432,top:180,width:413,paddingLeft:24,borderLeft:`2px solid ${C.line}`}}><div style={{fontSize:27,color:C.muted}}>{theme.side_label??'本节重点'}</div><div style={{fontSize:35,fontWeight:850,lineHeight:1.5,marginTop:13,whiteSpace:'pre-line'}}>{scene.visual.side??theme.side_text??''}</div></div>
    {event?.meme&&assets[event.meme]&&<div style={{position:'absolute',left:1445,top:350,width:375,height:260,transform:`scale(${scale})`,zIndex:6,borderRadius:14,overflow:'hidden'}}>{assets[event.meme].endsWith('.mp4')?<Sequence from={event.start} layout="none"><OffthreadVideo muted src={staticFile(assets[event.meme])} style={{width:'100%',height:'100%',objectFit:'contain'}}/></Sequence>:<Img src={staticFile(assets[event.meme])} style={{width:'100%',height:'100%',objectFit:'contain'}}/>}</div>}
    {assets.reaction&&<div style={{position:'absolute',left:1460,top:666,width:360,height:260,borderRadius:14,overflow:'hidden'}}><Loop durationInFrames={900}><OffthreadVideo muted src={staticFile(assets.reaction)} style={{width:'100%',height:'100%',objectFit:'cover'}}/></Loop></div>}
    <div style={{position:'absolute',left:55,right:55,bottom:32,height:103,borderRadius:15,background:'#17263b',display:'flex',justifyContent:'center',alignItems:'center',padding:'8px 30px',boxSizing:'border-box'}}><span style={{fontSize:40,fontWeight:800,color:'#ffe571',textAlign:'center',lineHeight:1.4}}>{cap?.text??''}</span></div>
    <div style={{position:'absolute',bottom:0,left:0,height:6,width:`${100*f/Math.max(1,scene.frames-1)}%`,background:C.red}}/>
  </div></AbsoluteFill>;
};
const Root=()=> <><Composition id="TeachingScene" component={Scene} durationInFrames={30} width={1280} height={720} fps={30} defaultProps={{scene:{frames:30,captions:[],events:[],visual:{template:'calculation',lines:['预览']}},settings:{width:1280,height:720,fps:30},theme:{},assets:{}}} calculateMetadata={({props})=>({durationInFrames:props.scene.frames,fps:props.settings.fps,width:props.settings.width,height:props.settings.height})}/><Composition id="StudyCoverPortrait" component={Cover} width={1080} height={1440} fps={30} durationInFrames={1} defaultProps={{landscape:false,heading:'论文分享',subtitle:'TAGNet',lines:['打好CS','还能上顶刊？'],image:'face.png'}}/><Composition id="StudyCoverLandscape" component={Cover} width={1440} height={1080} fps={30} durationInFrames={1} defaultProps={{landscape:true,heading:'论文分享',subtitle:'TAGNet',lines:['打好CS','还能上顶刊？'],image:'face.png'}}/></>;
registerRoot(Root);
