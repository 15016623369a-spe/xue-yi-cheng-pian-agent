import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {openBrowser,selectComposition,renderMedia,renderStill,makeCancelSignal} from '@remotion/renderer';

const request=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const inputProps=JSON.parse(fs.readFileSync(request.props,'utf8'));
const root=path.dirname(fileURLToPath(import.meta.url));
const serveUrl=await bundle({entryPoint:path.join(root,'src/index.tsx'),publicDir:request.public,outDir:request.bundle,enableCaching:false});
const chromiumOptions={gl:'swangle'};
const browserInstance=await openBrowser('chrome',{browserExecutable:request.browser,chromiumOptions});
const opts={serveUrl,browserExecutable:request.browser,chromiumOptions,browserInstance,inputProps,logLevel:'warn'};
try{
  const composition=await selectComposition({...opts,id:'TeachingScene'});
  await renderStill({...opts,composition,frame:Math.floor(composition.durationInFrames*.6),output:request.still});
  // A single page avoids multiple video decoders competing after a browser crash.
  // Cancel a stalled render rather than leaving its job permanently running.
  const {cancel,cancelSignal}=makeCancelSignal();
  let lastProgress=Date.now(),lastFrames='';
  const started=Date.now();
  const watchdog=setInterval(()=>{
    if(Date.now()-lastProgress>90000||Date.now()-started>600000){
      console.error('Render stopped: no frame progress for 90 seconds or scene deadline exceeded.');
      cancel();
    }
  },5000);
  try{
    await renderMedia({...opts,composition,codec:'h264',crf:18,x264Preset:'fast',pixelFormat:'yuv420p',muted:true,outputLocation:request.output,concurrency:1,cancelSignal,
      onProgress:({renderedFrames,encodedFrames})=>{
        const frames=`${renderedFrames}:${encodedFrames}`;
        if(frames!==lastFrames){lastFrames=frames;lastProgress=Date.now();}
      }});
  }finally{clearInterval(watchdog);}
  console.log(JSON.stringify({frames:composition.durationInFrames,output:request.output}));
}finally{
  await browserInstance.close({silent:true});
}
