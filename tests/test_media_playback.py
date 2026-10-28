"""HTTP media previews remain playable without relaxing document previews."""
from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from video_agent.web import Handler, Server


class MediaPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.content = bytes(range(256)) * 8
        for name in ("video.mp4", "sound.wav", "image.svg"):
            (root / name).write_bytes(self.content)

        class FileHandler(Handler):
            def do_GET(self):
                if not self.headers_ok():
                    return
                name = self.path.lstrip("/")
                if name not in {"video.mp4", "sound.wav", "image.svg"}:
                    return self.send_json({"error": "not found"}, 404)
                return self.stream_file(root / name, name)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FileHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, name, headers=None):
        request = urllib.request.Request(self.url + "/" + name, headers=headers or {})
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request)

    def test_direct_video_can_load_its_own_media(self):
        with self.request("video.mp4") as response:
            self.assertEqual(response.headers["Content-Type"], "video/mp4")
            policy = response.headers["Content-Security-Policy"]
            self.assertIn("media-src 'self'", policy)
            self.assertIn("sandbox allow-same-origin", policy)
            self.assertNotIn("allow-scripts", policy)
            self.assertEqual(response.read(), self.content)

    def test_direct_audio_can_load_its_own_media(self):
        with self.request("sound.wav") as response:
            self.assertIn("media-src 'self'", response.headers["Content-Security-Policy"])
            self.assertEqual(response.read(), self.content)

    def test_svg_keeps_the_restrictive_sandbox(self):
        with self.request("image.svg") as response:
            self.assertEqual(response.headers["Content-Security-Policy"], "default-src 'none'; sandbox")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_media_byte_ranges_preserve_headers_and_content(self):
        for value, start, end in (("bytes=0-1", 0, 1), ("bytes=2000-", 2000, 2047), ("bytes=-17", 2031, 2047)):
            with self.subTest(value=value):
                with self.request("video.mp4", {"Range": value}) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Content-Range"], f"bytes {start}-{end}/2048")
                    self.assertIn("media-src 'self'", response.headers["Content-Security-Policy"])
                    self.assertEqual(response.read(), self.content[start:end + 1])

    def test_unsatisfiable_range_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("video.mp4", {"Range": "bytes=2048-"})
        self.assertEqual(error.exception.code, 416)
        self.assertEqual(error.exception.headers["Content-Range"], "bytes */2048")


class PlayerPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = Server(("127.0.0.1", 0), Path(self.tmp.name))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_player_document_can_load_scripts_and_embedded_media(self):
        with self.opener.open(self.url + "/player.html?job=" + "a" * 32) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers["Content-Type"])
            policy = response.headers["Content-Security-Policy"]
            self.assertIn("script-src 'self'", policy)
            self.assertIn("media-src 'self'", policy)
            self.assertNotIn("sandbox", policy)
            body = response.read().decode()
            self.assertIn('src="/player.js"', body)
            self.assertIn('<video id="preview-video" controls playsinline', body)

    def test_player_resources_are_served_with_valid_content_types(self):
        for name, kind in (("player.js", "javascript"), ("player.css", "text/css")):
            with self.subTest(name=name):
                with self.opener.open(self.url + "/" + name) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(kind, response.headers["Content-Type"])
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                    self.assertTrue(response.read())


if __name__ == "__main__":
    unittest.main()
