import fs from 'node:fs';import path from 'node:path';import {bundle} from '@remotion/bundler';import {renderStill,selectComposition,openBrowser} from '@remotion/renderer';
const root=path.resolve(import.meta.dirname);const req=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
fs.mkdirSync(req.output,{recursive:true});const pub=path.join(req.output,'public');fs.mkdirSync(pub,{recursive:true});fs.copyFileSync(req.image,path.join(pub,'face.png'));
const serveUrl=await bundle({entryPoint:path.join(root,'src/index.tsx'),publicDir:pub,outDir:path.join(req.output,'bundle'),enableCaching:false});
const runtime=JSON.parse(fs.readFileSync(path.join(root,'../config/local.json'),'utf8'));const chromiumOptions={gl:'swangle'};const browserInstance=await openBrowser('chrome',{browserExecutable:runtime.browser,chromiumOptions});
try {for(const landscape of [false,true]){const inputProps={...req.props,landscape,image:'face.png'};const opts={serveUrl,inputProps,browserExecutable:runtime.browser,chromiumOptions,browserInstance,logLevel:'warn'};const composition=await selectComposition({...opts,id:landscape?'StudyCoverLandscape':'StudyCoverPortrait'});await renderStill({...opts,composition,frame:0,imageFormat:'png',output:path.join(req.output,landscape?'横封面4比3.png':'竖封面3比4.png')});}}
finally{await browserInstance.close({silent:true});}
