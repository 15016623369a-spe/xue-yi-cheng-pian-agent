import array
import json
import math
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from video_agent.engine import ROOT


class AudioSyncTests(unittest.TestCase):
    def test_only_explicitly_muted_silence_can_skip_correlation(self):
        runtime=json.loads((ROOT/'config/local.json').read_text())
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t)
            for name in ['decoded','silent']:
                with wave.open(str(folder/(name+'.wav')),'wb') as f:
                    f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000)
                    f.writeframes(array.array('h',[0]*24000).tobytes())
            request={'decoded':str(folder/'decoded.wav'),'output':str(folder/'sync.json'),
                     'scenes':[{'id':'S01','audio':str(folder/'silent.wav'),'seconds':1,'allow_silent':True}]}
            (folder/'request.json').write_text(json.dumps(request))
            command=[runtime['voice_python'],str(ROOT/'workers/check_sync.py'),str(folder/'request.json')]
            p=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            report=json.loads((folder/'sync.json').read_text())
            self.assertIsNone(report['scenes'][0]['lag_seconds'])
            self.assertEqual(report['scenes'][0]['skipped'],'intentional_narration_mute_no_effects')
            request['scenes'][0]['allow_silent']=False
            (folder/'request.json').write_text(json.dumps(request))
            p=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0)

    def test_fractional_downsample_origins_and_real_shift(self):
        runtime_path=ROOT/'config/local.json'
        if not runtime_path.exists():
            self.skipTest('Local NumPy runtime not configured')
        runtime=json.loads(runtime_path.read_text())
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t)
            tracks=[]
            request={'decoded':str(folder/'decoded.wav'),'output':str(folder/'sync.json'),'scenes':[]}
            def save(path,values):
                with wave.open(str(path),'wb') as f:
                    f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(values.tobytes())
            for i,frames in enumerate([31,34]):
                count=frames*800
                values=array.array('h',(round(9000*math.sin(2*math.pi*((180+130*i)*j/24000+330*(j/24000)**2))) for j in range(count)))
                path=folder/f'{i}.wav';save(path,values);tracks.extend(values)
                request['scenes'].append({'id':str(i),'audio':str(path),'seconds':frames/30})
            save(folder/'decoded.wav',array.array('h',tracks))
            (folder/'request.json').write_text(json.dumps(request))
            command=[runtime['voice_python'],str(ROOT/'workers/check_sync.py'),str(folder/'request.json')]
            p=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            report=json.loads((folder/'sync.json').read_text())
            self.assertTrue(all(r['lag_seconds']==0 for r in report['scenes']))
            # A real 66.7 ms delay must fail, rather than being normalized away.
            save(folder/'decoded.wav',array.array('h',[0]*1600+tracks))
            p=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0)
            self.assertTrue(all(r['lag_seconds']>.06 for r in json.loads((folder/'sync.json').read_text())['scenes']))
