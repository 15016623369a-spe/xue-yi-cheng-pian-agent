import React from 'react';
import {AbsoluteFill, Img, staticFile} from 'remotion';

function weight(text: string) {
  return [...text].reduce((sum, c) => sum + (/^[\x00-\x7f]$/.test(c) ? .56 : 1), 0);
}
function wrap(title: string, portrait: boolean) {
  if (title.includes('\n')) return title.split('\n');
  const natural = title.match(/^(.{2,12}?)((?:还能|也能|如何|为什么|怎么|到底).+)$/);
  if (natural && weight(natural[2]) <= 10) return [natural[1], natural[2]];
  const units = [...title], count = Math.min(4, Math.max(1, Math.ceil(weight(title) / (portrait ? 7 : 8))));
  const target = weight(title) / count, lines: string[] = [];
  let line = '';
  for (const c of units) {
    if (line && weight(line + c) > target + .5 && lines.length < count - 1 && !/[，。？！：；、,.?!]/.test(c)) {
      lines.push(line); line = '';
    }
    line += c;
  }
  if (line) lines.push(line);
  return lines;
}

export const CoverStudio: React.FC<any> = ({title, image, width, height}) => {
  const portrait = height > width;
  const lines = wrap(title, portrait);
  const regionWidth = width * (portrait ? .87 : .57);
  const regionHeight = height * (portrait ? .25 : .65);
  const fontSize = Math.floor(Math.min(width * (portrait ? .128 : .093),
    regionWidth / Math.max(...lines.map(weight)), regionHeight / (lines.length * 1.25)));
  return <AbsoluteFill style={{background: '#10191f', color: '#fff', overflow: 'hidden',
    fontFamily: 'PingFang SC,Hiragino Sans GB,sans-serif'}}>
    <div style={{position: 'absolute', top: 0, right: 0, width: portrait ? '100%' : '61%',
      height: portrait ? '80%' : '100%', background: '#9da3b2'}}>
      <Img src={staticFile(image)} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center top'}}/>
    </div>
    <AbsoluteFill style={{background: portrait
      ? 'linear-gradient(0deg,#10191f 2%,#10191f 23%,#10191fe8 35%,#10191f00 70%)'
      : 'linear-gradient(90deg,#10191f 0%,#10191f 36%,#10191fee 43%,#10191f00 69%)'}}/>
    <div style={{position: 'absolute', left: width * .062, top: portrait ? height * .66 : height * .24,
      width: regionWidth, fontSize, fontWeight: 900, letterSpacing: -fontSize * .035,
      lineHeight: 1.25, textShadow: '0 4px 20px #0009'}}>
      <div style={{width: width * .095, height: Math.max(8, width * .009), background: '#ee4549', marginBottom: height * .042}}/>
      {lines.map((line, i) => <div key={i} style={{whiteSpace: 'pre', color: i === lines.length - 1 && i > 0 ? '#ffe05e' : '#fff'}}>{line}</div>)}
    </div>
    <div style={{position: 'absolute', bottom: height * .045, left: width * .062,
      width: width * .17, height: 2, background: '#ffffff33'}}/>
  </AbsoluteFill>;
};
