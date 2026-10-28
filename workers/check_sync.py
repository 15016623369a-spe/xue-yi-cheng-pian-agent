"""Check decoded final audio against scene PCM, without loading an AI model."""
from pathlib import Path
import json
import sys
import wave
import numpy as np


def samples(path):
    with wave.open(str(path),'rb') as f:
        assert (f.getnchannels(),f.getsampwidth(),f.getframerate())==(1,2,24000)
        a=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float32)/32768
    # Low-pass before decimation. Keep the full-rate origin until each scene's
    # search window is selected: a video frame need not contain an integer
    # number of 4 kHz samples, so global decimation can create phase errors.
    n=np.arange(-32,33)
    kernel=2*.07*np.sinc(2*.07*n)*np.hamming(65)
    kernel/=kernel.sum()
    return np.convolve(a,kernel,'same')


request=json.loads(Path(sys.argv[1]).read_text())
actual=samples(request['decoded'])
position=0
results=[]
for scene in request['scenes']:
    expected=samples(scene['audio'])
    if scene.get('allow_silent') and not np.any(expected):
        results.append({'scene':scene['id'],'lag_seconds':None,'normalized_correlation':None,
                        'skipped':'intentional_narration_mute_no_effects'})
        position+=scene['seconds']
        continue
    # Find a high-energy window away from scene edges and leading silence.
    window=min(19200,len(expected)-4800)
    if window<2400:
        raise ValueError('Scene too short for sync check')
    choices=range(2400,max(2401,len(expected)-window-2400),1200)
    offset=max(choices,key=lambda p:float(np.mean(expected[p:p+window]**2)))
    reference=expected[offset:offset+window:6]
    center=round(position*24000)+offset
    begin=max(0,center-4800)
    region=actual[begin:min(len(actual),center+window+4800):6]
    correlation=np.correlate(region,reference,'valid')
    best=int(np.argmax(correlation))
    found=region[best:best+len(reference)]
    similarity=float(np.dot(reference,found)/(np.linalg.norm(reference)*np.linalg.norm(found)+1e-12))
    lag=(begin+best*6-center)/24000
    results.append({'scene':scene['id'],'lag_seconds':lag,'normalized_correlation':similarity})
    position+=scene['seconds']
passed=all(r.get('skipped')=='intentional_narration_mute_no_effects' or
           (abs(r['lag_seconds'])<.04 and r['normalized_correlation']>.94) for r in results)
Path(request['output']).write_text(json.dumps({'passed':passed,'scenes':results},ensure_ascii=False,indent=2)+'\n')
if not passed:
    raise SystemExit('Decoded audio alignment failed')
