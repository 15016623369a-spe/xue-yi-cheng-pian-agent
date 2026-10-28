import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from video_agent.state import Store


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.calls = 0

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def action(self, directory):
        self.calls += 1
        (directory/'result.txt').write_text(f'build {self.calls}')
        return {'result':'result.txt'}

    def test_repeat_and_input_change_keep_separate_artifacts(self):
        with self.store.locked():
            self.store.begin({})
            first = self.store.step('voice:A',{'speech':'one'},self.action)
            repeated = self.store.step('voice:A',{'speech':'one'},self.action)
            edited = self.store.step('voice:A',{'speech':'two'},self.action)
        self.assertEqual(self.calls,2)
        self.assertEqual(first['key'],repeated['key'])
        self.assertNotEqual(first['key'],edited['key'])
        self.assertEqual(first['files']['result'].read_text(),'build 1')

    def test_corrupted_artifact_is_not_a_cache_hit(self):
        with self.store.locked():
            self.store.begin({})
            first = self.store.step('render:A',{},self.action)
            first['files']['result'].write_text('corrupt')
            second = self.store.step('render:A',{},self.action)
        self.assertEqual(self.calls,2)
        self.assertEqual(second['files']['result'].read_text(),'build 2')

    def test_failure_never_publishes_partial_artifact_and_other_stage_reuses(self):
        def broken(directory):
            (directory/'result.txt').write_text('incomplete')
            raise RuntimeError('tool failed')
        with self.store.locked():
            self.store.begin({})
            self.store.step('voice:A',{},self.action)
            with self.assertRaisesRegex(RuntimeError,'tool failed'):
                self.store.step('render:A',{},broken)
            self.store.finish('failed','tool failed')
            self.store.begin({})
            self.store.step('voice:A',{},self.action)
            self.store.step('render:A',{},self.action)
        self.assertEqual(self.calls,2)
        self.assertEqual(self.store.events[-2]['status'],'cached')
        failed = self.store.db.execute("SELECT COUNT(*) FROM attempts WHERE status='failed'").fetchone()[0]
        self.assertEqual(failed,1)

    def test_abandoned_run_marked_interrupted(self):
        self.store.begin({})
        previous = self.store.run_id
        self.store.db.execute("INSERT INTO attempts(run_id,stage,key,status,started) VALUES(?,?,?,'running',0)",(previous,'render:A','x'))
        self.store.db.commit()
        self.store.begin({})
        self.assertEqual(self.store.db.execute('SELECT status FROM runs WHERE id=?',(previous,)).fetchone()[0],'interrupted')
        self.assertEqual(self.store.db.execute('SELECT status FROM attempts').fetchone()[0],'interrupted')

    def test_concurrent_process_cannot_start_same_project(self):
        with self.store.locked():
            script = "from pathlib import Path; from video_agent.state import Store; s=Store(Path(__import__('sys').argv[1]));\nwith s.locked(): pass"
            p = subprocess.run([sys.executable,'-c',script,str(self.root)],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn('Another process',p.stderr)

    def test_output_cannot_escape_stage_directory(self):
        outside = self.root/'outside.txt'
        outside.write_text('other data')
        def bad(directory):
            (directory/'escape.txt').symlink_to(outside)
            return {'result':'escape.txt'}
        self.store.begin({})
        with self.assertRaisesRegex(RuntimeError,'invalid output'):
            self.store.step('render:A',{},bad)
        self.assertEqual(outside.read_text(),'other data')


if __name__=='__main__':
    unittest.main()
