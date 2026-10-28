"""Displayed render settings must describe the video, not the latest draft."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from video_agent.engine import ROOT
from video_agent.jobs import Jobs
from video_agent.util import read_json, write_json
from video_agent.workspace import Workspace


class RenderSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "projects").mkdir()
        (self.root / "projects/lesson04-validation").symlink_to(ROOT / "projects/lesson04-validation", target_is_directory=True)
        self.ws = Workspace(self.root)
        self.jobs = Jobs(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def legacy_job(self, speed=None):
        jid = "a" * 32
        data = {"id": jid, "kind": "render", "project_id": "web-123456abcdef", "revision": 1,
                "scene_id": "S01", "created": time.time(), "status": "completed", "message": "done"}
        write_json(self.jobs.folder(jid) / "job.json", data)
        if speed is not None:
            write_json(self.jobs.folder(jid) / "input.json", {"config": {"voice": {"speed": speed}}})
        return jid

    def test_old_video_speed_comes_from_its_input_snapshot_without_rewriting(self):
        jid = self.legacy_job(1.39)
        path = self.jobs.folder(jid) / "job.json"
        original = path.read_bytes()
        with patch.object(self.ws, "get", side_effect=AssertionError("Do not use the current draft")):
            self.assertEqual(self.jobs.get(jid)["render_settings"]["voice_speed"], 1.39)
            self.assertEqual(self.jobs.list()[0]["render_settings"]["voice_speed"], 1.39)
        self.assertEqual(path.read_bytes(), original)

    def test_missing_old_snapshot_does_not_hide_a_completed_video(self):
        jid = self.legacy_job()
        result = self.jobs.get(jid)
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("render_settings", result)

    def test_saved_speed_reaches_scene_config_and_remains_bound_to_that_task(self):
        doc = self.ws.sample()
        doc = self.ws.update(doc["id"], doc["revision"], {"media": {
            "speed": 1.2, "bgm_db": -24, "bgm": True, "opening": False}})
        scene_id = doc["storyboard"]["scenes"][0]["id"]
        with patch("video_agent.jobs.subprocess.Popen", return_value=Mock(pid=12345)):
            job = self.jobs.create("render", doc["id"], doc["revision"], scene_id)
        config = read_json(Path(job["config_path"]))
        self.assertEqual(config["voice"]["speed"], 1.2)
        self.assertEqual(len(config["scenes"]), 1)
        self.assertEqual(job["render_settings"]["voice_speed"], 1.2)
        self.ws.update(doc["id"], doc["revision"], {"media": {
            "speed": .9, "bgm_db": -24, "bgm": True, "opening": False}})
        self.assertEqual(self.jobs.get(job["id"])["render_settings"]["voice_speed"], 1.2)
        self.assertEqual(read_json(Path(job["config_path"]))["voice"]["speed"], 1.2)


if __name__ == "__main__":
    unittest.main()
