import React from 'react';
import {AbsoluteFill,Img,staticFile} from 'remotion';
export const Cover:React.FC<any>=({landscape=false,heading,subtitle,lines,image})=>{
 const width=landscape?1440:1080;
 return <AbsoluteFill style={{background:'#fff',color:'#111',fontFamily:'PingFang SC,Hiragino Sans GB,sans-serif'}}>
 <div style={{position:'absolute',left:35,right:35,top:landscape?45:85,textAlign:'center',fontFamily:'Songti SC,STSong,serif',fontSize:landscape?46:51,fontWeight:650,lineHeight:1.5}}>{heading}</div>
 <div style={{position:'absolute',top:landscape?125:240,left:35,right:35,textAlign:'center',fontSize:landscape?32:34,color:'#555'}}>{subtitle}</div>
 <div style={{position:'absolute',top:landscape?196:370,left:20,right:20,zIndex:2,textAlign:'center'}}>{lines.map((line:string)=><div key={line} style={{fontSize:landscape?108:103,fontWeight:900,lineHeight:1.23,color:'#ffe246',WebkitTextStroke:'9px #111',paintOrder:'stroke fill',letterSpacing:-3,filter:'drop-shadow(0px 2px 0px white)',whiteSpace:'nowrap'}}>{line}</div>)}</div>
 <div style={{position:'absolute',left:landscape?126:0,right:landscape?126:0,top:landscape?466:690,bottom:0,overflow:'hidden',borderRadius:'45% 45% 0 0 / 22% 22% 0 0'}}><Img src={staticFile(image)} style={{position:'absolute',width:landscape?1188:1458,height:landscape?1188:1458,left:landscape?0:-189,top:landscape?-335:-430}}/></div>
 </AbsoluteFill>
};
