"""Narration gain must be independent of synthesis, music and effect controls."""
import array
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from video_agent.engine import ROOT, voice_signature_for
from video_agent.jobs import Jobs
from video_agent.media import export, mix_scene, read_pcm, write_pcm
from video_agent.util import read_json, run_command, write_json
from video_agent.workspace import Workspace


class VoiceVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'projects').mkdir()
        (self.root/'projects/lesson04-validation').symlink_to(ROOT/'projects/lesson04-validation', target_is_directory=True)
        self.ws = Workspace(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_volume_saves_and_job_keeps_its_original_value(self):
        doc = self.ws.sample()
        signature = voice_signature_for(doc['config']['voice'], self.ws.path(doc['id']))
        doc = self.ws.update(doc['id'], 1, {'media': {'speed': 1.18, 'voice_volume': .75, 'bgm_db': -24}})
        self.assertEqual(doc['config']['voice']['volume'], .75)
        self.assertEqual(signature, voice_signature_for(doc['config']['voice'], self.ws.path(doc['id'])))
        jobs = Jobs(self.ws)
        with patch('video_agent.jobs.subprocess.Popen', return_value=Mock(pid=12345)):
            job = jobs.create('render', doc['id'], doc['revision'], doc['storyboard']['scenes'][0]['id'])
        self.ws.update(doc['id'], doc['revision'], {'media': {'speed': 1.18, 'voice_volume': 1.25, 'bgm_db': -24}})
        self.assertEqual(jobs.get(job['id'])['render_settings']['voice_volume'], .75)
        self.assertEqual(read_json(Path(job['config_path']))['voice']['volume'], .75)

    def test_invalid_gain_cannot_modify_the_saved_project(self):
        doc = self.ws.sample()
        for gain in [-.01, 2.01, True, '1', None, float('nan'), float('inf')]:
            with self.subTest(gain=gain), self.assertRaises(ValueError):
                self.ws.update(doc['id'], 1, {'media': {'voice_volume': gain}})
            self.assertEqual(self.ws.get(doc['id'])['revision'], 1)

    def test_older_clients_preserve_saved_gain(self):
        doc = self.ws.sample()
        doc = self.ws.update(doc['id'], 1, {'media': {'voice_volume': 1.25}})
        doc = self.ws.update(doc['id'], doc['revision'], {'media': {'speed': 1.2}})
        self.assertEqual(doc['config']['voice']['volume'], 1.25)

    def test_pcm_gain_and_mute_leave_duration_and_effects_unchanged(self):
        narration = self.root/'voice.wav'
        write_pcm(narration, array.array('h', (round(7000*math.sin(i*.13)) for i in range(24000))), 24000)
        timing = self.root/'timing.json'
        write_json(timing, {'fps': 30, 'events': []})
        tracks = {}
        for volume in [0, .5, 1, 1.5]:
            folder = self.root/str(volume); folder.mkdir()
            mix_scene(folder, narration, timing, {'voice': {'volume': volume}})
            tracks[volume] = read_pcm(folder/'mixed.wav')[0]
            self.assertEqual(len(tracks[volume]), 24000)
        self.assertFalse(any(tracks[0]))
        rms = lambda values: math.sqrt(sum(x*x for x in values)/len(values))
        self.assertAlmostEqual(rms(tracks[.5])/rms(tracks[1]), .5, places=3)
        self.assertAlmostEqual(rms(tracks[1.5])/rms(tracks[1]), 1.5, places=3)
        write_json(timing, {'fps': 30, 'events': [{'id': 'E01', 'start': 3, 'sound': 'beep'}]})
        folder = self.root/'effects'; folder.mkdir()
        mix_scene(folder, narration, timing, {'voice': {'volume': 0}})
        muted_with_effects = read_pcm(folder/'mixed.wav')[0]
        self.assertFalse(any(muted_with_effects[:2400]))
        self.assertTrue(any(muted_with_effects[2400:8400]))

    def test_boost_retains_peak_protection(self):
        p = self.root/'loud.wav'
        write_pcm(p, array.array('h', [30000]*24000), 24000)
        t = self.root/'timing.json'; write_json(t, {'fps': 30, 'events': []})
        folder = self.root/'protected'; folder.mkdir()
        mix_scene(folder, p, t, {'voice': {'volume': 2}})
        self.assertLessEqual(max(read_pcm(folder/'mixed.wav')[0]), round(.85*32767))

    def test_real_export_changes_voice_but_preserves_music_level(self):
        runtime = read_json(ROOT/'config/local.json')
        ffmpeg = runtime['ffmpeg']
        source = self.root/'narration.wav'
        music = self.root/'music.wav'
        write_pcm(source, array.array('h', (round(7000*math.sin(2*math.pi*440*i/24000)) for i in range(72000))), 24000)
        write_pcm(music, array.array('h', (round(7000*math.sin(2*math.pi*2000*i/24000)) for i in range(72000))), 24000)
        timing = {'fps': 30, 'frames': 90, 'events': [], 'captions': [], 'id': 'S01'}
        t = self.root/'timing.json'; write_json(t, timing)
        video = self.root/'silent.mp4'
        run_command([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=640x360:r=30:d=3', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], self.root/'video.log')
        measured = {}
        gains = []
        for volume in [1, .5, 0]:
            folder = self.root/f'export-{volume}'; folder.mkdir()
            mix = folder/'scene'; mix.mkdir()
            mix_scene(mix, source, t, {'voice': {'volume': volume}})
            movie = folder/'scene.mov'
            run_command([ffmpeg, '-v', 'error', '-y', '-i', str(video), '-i', str(mix/'mixed.wav'), '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'pcm_s16le', str(movie)], folder/'mux.log')
            config = {'voice': {'volume': volume}, 'settings': {'fps': 30, 'maximum_seconds': 10, 'sample_rate': 24000}, 'bgm': {'relative_db': -18}}
            export(folder, [{'movie': movie, 'audio': mix/'mixed.wav', 'narration': source, 'timing': timing}], config, runtime, music)
            gains.append(read_json(folder/'mix.json')['bgm_gain'])
            decoded = folder/'decoded.wav'
            run_command([ffmpeg, '-v', 'error', '-y', '-i', str(folder/'video.mp4'), '-vn', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', str(decoded)], folder/'decode.log')
            pcm = read_pcm(decoded)[0][24000:48000]
            def amplitude(hz):
                a = sum(x*math.sin(2*math.pi*hz*i/24000) for i,x in enumerate(pcm))
                b = sum(x*math.cos(2*math.pi*hz*i/24000) for i,x in enumerate(pcm))
                return 2*math.hypot(a,b)/len(pcm)
            measured[volume] = [amplitude(440), amplitude(2000)]
        self.assertEqual(gains, [gains[0]]*3)
        self.assertAlmostEqual(measured[.5][0]/measured[1][0], .5, delta=.03)
        self.assertAlmostEqual(measured[.5][1]/measured[1][1], 1, delta=.03)
        self.assertLess(measured[0][0], measured[1][0]*.001)
        self.assertAlmostEqual(measured[0][1]/measured[1][1], 1, delta=.03)


if __name__ == '__main__':
    unittest.main()
