"""The distributable checkout must not need the author's private fixtures."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_agent.engine import ROOT
from video_agent.planning import validate_knowledge, validate_plan
from video_agent.workspace import Workspace


class PublicCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        for folder in ['config','examples']:
            (self.root/folder).mkdir()
        shutil.copy2(ROOT/'config/project.example.json',self.root/'config/project.example.json')
        for name in ['handoff.md','storyboard.json']:
            shutil.copy2(ROOT/'examples'/name,self.root/'examples'/name)
        self.ws=Workspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_and_reload_without_runtime_models_or_speaker_files(self):
        doc=self.ws.create({'title':'独立笔记','source':'# 笔记\n这是一份独立的学习笔记。'})
        self.assertEqual(self.ws.get(doc['id'])['source'],doc['source'])
        self.assertEqual(doc['config']['theme']['label'],'学习分享')
        self.assertEqual(doc['config']['scenes'],[])
        self.assertEqual(Path(doc['config']['voice']['reference_audio']),self.root/'assets/voice/reference.wav')
        self.assertFalse((self.root/'config/local.json').exists())

    def test_public_sample_has_exact_sources_and_no_network_calls(self):
        with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No network')):
            doc=self.ws.sample()
        self.assertEqual(doc['scene_count'],3)
        self.assertEqual(doc['planning']['new_api_calls'],0)
        validate_knowledge(doc['knowledge'],doc['source'])
        validate_plan(doc['storyboard'],doc['knowledge'])
        for scene in doc['storyboard']['scenes']:
            for event in scene['events']:
                self.assertEqual(scene['speech'].count(event['anchor']),1)
        self.assertFalse(doc['approved'])
        self.assertFalse(doc['config']['assets'])

    def test_local_voice_override_is_resolved_and_not_bundled(self):
        voice={'profile':'my-own-voice','reference_audio':str(self.root/'own.wav'),
               'reference_text':str(self.root/'own.txt')}
        (self.root/'config/default-voice.json').write_text(json.dumps(voice))
        doc=self.ws.create({'source':'# 我的笔记\n引用来自自己的学习内容。'})
        self.assertEqual(doc['config']['voice']['profile'],'my-own-voice')
        self.assertEqual(doc['config']['voice']['reference_audio'],str(self.root/'own.wav'))


if __name__=='__main__':
    unittest.main()
