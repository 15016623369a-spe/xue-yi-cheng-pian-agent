from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from video_agent import covers
from video_agent.engine import ROOT
from video_agent.jobs import Jobs
from video_agent.util import file_hash, run_command
from video_agent.web import Server
from video_agent.workspace import Conflict, Workspace

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'projects').mkdir()
        (self.root / 'projects/lesson04-validation').symlink_to(ROOT / 'projects/lesson04-validation', target_is_directory=True)
        self.ws = Workspace(self.root)
        self.doc = self.ws.sample()
        self.pid = self.doc['id']
        self.source = self.root / 'source.png'
        self.source.write_bytes(PNG)

    def tearDown(self):
        self.temp.cleanup()

    def upload(self):
        return covers.register(self.ws, self.pid, 0, self.source, '../本期人物.png')

    def test_cover_upload_retains_original_and_does_not_change_video(self):
        before = (self.ws.path(self.pid) / 'workspace.json').read_bytes()
        doc = self.upload()
        image_id = doc['settings']['image_id']
        self.assertEqual(doc['images'][image_id]['name'], '本期人物.png')
        self.assertEqual((covers.image_path(self.ws, self.pid, image_id).parent / 'original.png').read_bytes(), PNG)
        self.assertEqual(before, (self.ws.path(self.pid) / 'workspace.json').read_bytes())
        with self.assertRaises(Conflict):
            self.upload()

    def test_invalid_image_and_unknown_asset_cannot_create_cover(self):
        self.source.write_text('not an image')
        with self.assertRaises(ValueError):
            self.upload()
        self.assertEqual(covers.load(self.ws, self.pid)['revision'], 0)
        for settings in [dict(title='标题', aspect='16:9', image_id='a'*64),
                         dict(title='<script>', aspect='99:1', image_id='')]:
            with self.assertRaises(ValueError):
                covers.prepare(self.ws, self.pid, 0, settings)
        with self.assertRaises(ValueError):
            covers.image_path(self.ws, self.pid, '../../model.json')

    def test_job_uses_immutable_cover_snapshot_without_api_or_video_approval(self):
        doc = self.upload()
        settings = {**doc['settings'], 'title': '打好cs\n还能上顶刊？'}
        with patch('video_agent.jobs.subprocess.Popen'):
            job = Jobs(self.ws).create('cover', self.pid, cover={'revision': 1, 'settings': settings})
        settings['title'] = '稍后修改'
        self.assertEqual(job['cover_input']['settings']['title'], '打好cs\n还能上顶刊？')
        self.assertEqual(job['cover_input']['image_sha256'], file_hash(covers.image_path(self.ws, self.pid, doc['settings']['image_id'])))
        self.assertFalse(self.ws.get(self.pid)['approved'])
        self.assertEqual(self.ws.get(self.pid)['revision'], 1)
        self.assertEqual(covers.load(self.ws, self.pid)['settings']['title'], '打好cs\n还能上顶刊？')

    def test_cache_verifies_output_and_preserves_newer_settings(self):
        doc = self.upload()
        request = covers.prepare(self.ws, self.pid, 1, {**doc['settings'], 'aspect': '16:9'})
        local = covers.runtime(self.ws)
        calls = []

        def render(args, log, timeout):
            calls.append(args)
            req = json.loads(Path(args[-1]).read_text())
            run_command([local['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=1920x1080',
                         '-frames:v', '1', '-update', '1', str(Path(req['output'])/'cover.png')], log)

        newer = {**doc['settings'], 'title': '下一张封面的标题'}
        covers.prepare(self.ws, self.pid, 2, newer)
        with patch('video_agent.covers.run_command', side_effect=render):
            first = covers.generate(self.ws, self.pid, 'a'*32, request)
            second = covers.generate(self.ws, self.pid, 'b'*32, request)
            self.assertFalse(first['cache_hit'])
            self.assertTrue(second['cache_hit'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(covers.load(self.ws, self.pid)['settings']['title'], newer['title'])
            (Path(first['output_directory'])/'cover.png').write_bytes(b'broken')
            third = covers.generate(self.ws, self.pid, 'c'*32, request)
            self.assertFalse(third['cache_hit'])
            self.assertEqual(len(calls), 2)
        self.assertEqual(len(covers.load(self.ws, self.pid)['renders']), 3)

    def test_pair_is_complete_and_reuses_first_layout_after_second_fails(self):
        before = (self.ws.path(self.pid) / 'workspace.json').read_bytes()
        doc = self.upload()
        self.assertEqual(doc['settings']['aspect'], 'both')
        request = covers.prepare(self.ws, self.pid, 1, doc['settings'])
        local = covers.runtime(self.ws)
        calls = []

        def render(args, log, timeout):
            req = json.loads(Path(args[-1]).read_text())
            props = req['props']
            calls.append(props['aspect'])
            if len(calls) == 2:
                raise RuntimeError('second layout interrupted')
            run_command([local['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
                         f"color=c=red:s={props['width']}x{props['height']}", '-frames:v', '1',
                         '-update', '1', str(Path(req['output'])/'cover.png')], log)

        with patch('video_agent.covers.run_command', side_effect=render):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                covers.generate(self.ws, self.pid, 'a'*32, request)
            self.assertEqual(covers.load(self.ws, self.pid)['renders'], [])
            pair = covers.generate(self.ws, self.pid, 'b'*32, request)
            self.assertEqual(calls, ['4:3', '3:4', '3:4'])
            self.assertFalse(pair['cache_hit'])
            self.assertEqual([(v['file'], v['width'], v['height'], v['cache_hit']) for v in pair['covers']],
                             [('landscape.png', 1440, 1080, True), ('portrait.png', 1080, 1440, False)])
            for variant in pair['covers']:
                self.assertEqual(file_hash(Path(pair['output_directory'])/variant['file']), variant['sha256'])
            second = covers.generate(self.ws, self.pid, 'c'*32, request)
            self.assertTrue(second['cache_hit'])
            self.assertEqual(len(calls), 3)
            # Repair only the damaged delivery copy, reusing both verified layouts.
            (Path(pair['output_directory'])/'portrait.png').write_bytes(b'broken')
            repaired = covers.generate(self.ws, self.pid, 'd'*32, request)
            self.assertFalse(repaired['cache_hit'])
            self.assertEqual(len(calls), 3)
            self.assertEqual(file_hash(Path(repaired['output_directory'])/'portrait.png'), pair['covers'][1]['sha256'])
        self.assertEqual(before, (self.ws.path(self.pid)/'workspace.json').read_bytes())
        self.assertEqual(len(covers.load(self.ws, self.pid)['renders']), 3)


class CoverHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root/'projects').mkdir()
        (root/'projects/lesson04-validation').symlink_to(ROOT/'projects/lesson04-validation', target_is_directory=True)
        self.server = Server(('127.0.0.1', 0), root)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.doc = self.server.ws.sample()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        return self.opener.open(urllib.request.Request(self.url+path, data=data, headers=headers or {}))

    def test_upload_token_revision_and_registered_image_download(self):
        prefix = f"/api/projects/{self.doc['id']}/cover"
        query = urllib.parse.urlencode({'revision': 0, 'name': '本期封面.png'})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request(prefix+'/upload?'+query, PNG)
        self.assertEqual(ctx.exception.code, 403)
        with self.request(prefix+'/upload?'+query, PNG, {'X-Workspace-Token': self.server.token}) as r:
            doc = json.load(r)
        self.assertEqual(doc['revision'], 1)
        with self.request(prefix+'/images/'+doc['settings']['image_id'], headers={'Range':'bytes=0-7'}) as r:
            self.assertEqual(r.read(), b'\x89PNG\r\n\x1a\n')
            self.assertEqual(r.status, 206)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request(prefix+'/upload?'+query, PNG, {'X-Workspace-Token': self.server.token})
        self.assertEqual(ctx.exception.code, 409)
        with self.assertRaises(urllib.error.HTTPError):
            self.request(prefix+'/images/'+'a'*64)

    def test_generation_route_is_explicit_and_rejects_invalid_title(self):
        prefix = f"/api/projects/{self.doc['id']}/cover"
        with self.request(prefix) as r:
            self.assertEqual(json.load(r)['settings']['title'], self.doc['title'])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request(prefix, json.dumps({'revision': 0, 'settings': {'title': '', 'aspect': '16:9', 'image_id': ''}}).encode(),
                         {'Content-Type':'application/json', 'X-Workspace-Token':self.server.token})
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.server.jobs.list(self.doc['id']), [])
