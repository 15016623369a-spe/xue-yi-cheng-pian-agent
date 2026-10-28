import copy
import json
import tempfile
import unittest
from pathlib import Path

from video_agent.alignment import align, srt_time
from video_agent.engine import ROOT, voice_signature_for
from video_agent.schema import load_project

FIXTURE = ROOT/'projects/lesson04-validation/project.json'


class ContractTests(unittest.TestCase):
    def test_actual_three_scene_fixture_valid(self):
        p = load_project(FIXTURE)
        self.assertEqual([s['visual']['template'] for s in p['scenes']],['flow','branch','calculation'])

    def invalid(self, change, message):
        p = load_project(FIXTURE)
        # Keep external resource paths valid while validating a temporary JSON.
        for k,v in p['assets'].items():
            p['assets'][k] = str(FIXTURE.parent/v)
        for key in ['reference_audio','reference_text']:
            p['voice'][key] = str(FIXTURE.parent/p['voice'][key])
        change(p)
        with tempfile.TemporaryDirectory() as t:
            f = Path(t)/'project.json'
            f.write_text(json.dumps(p,ensure_ascii=False))
            with self.assertRaisesRegex(ValueError,message):
                load_project(f)

    def test_missing_anchor_rejected_before_any_tool_runs(self):
        self.invalid(lambda p:p['scenes'][0]['events'][0].update(anchor='does not exist'),'anchor')

    def test_ambiguous_anchor_rejected(self):
        self.invalid(lambda p:p['scenes'][0].update(speech='先扩到十六维，先扩到十六维'),'anchor')

    def test_duplicate_scene_rejected(self):
        self.invalid(lambda p:p['scenes'].append(copy.deepcopy(p['scenes'][0])),'duplicate')

    def test_unknown_template_rejected(self):
        self.invalid(lambda p:p['scenes'][0]['visual'].update(template='unimplemented'),'unsupported')

    def test_branch_beyond_supported_layout_rejected(self):
        self.invalid(lambda p:p['scenes'][1]['visual'].update(steps=['one','two','three']),'supports 1–2')

    def test_speed_change_does_not_change_raw_voice_identity(self):
        p = load_project(FIXTURE)
        a = voice_signature_for(p['voice'],FIXTURE.parent)
        p['voice']['speed'] = 1.3
        self.assertEqual(a,voice_signature_for(p['voice'],FIXTURE.parent))
        p['voice']['profile'] = 'another-reference-profile'
        self.assertNotEqual(a,voice_signature_for(p['voice'],FIXTURE.parent))

    def test_word_timestamps_place_caption_and_highlight(self):
        text = '分母用四。结果不是概率。'
        words=[{'word':'分母','start':0,'end':.3},{'word':'用四','start':.3,'end':.7},{'word':'结果','start':1,'end':1.3},{'word':'不是概率','start':1.3,'end':2.1}]
        t=align(text,{'segments':[{'words':words}],'text':text},[{'id':'E01','anchor':'结果','seconds':1}],trim=0,speed=1,audio_seconds=2.2,frames=78,fps=30)
        self.assertEqual(t['events'][0]['start'],33)
        self.assertEqual(t['alignment']['matched_char_ratio'],1)
        for c in t['captions']:
            self.assertTrue(0<=c['start']<c['end']<=78)
        self.assertEqual(srt_time(31,30),'00:00:01,033')

    def test_bad_transcript_stops_instead_of_inventing_timing(self):
        with self.assertRaisesRegex(ValueError,'insufficient'):
            align('这是一句正确讲稿',{'segments':[]},[],trim=0,speed=1,audio_seconds=1,frames=40,fps=30)

    def test_missing_closing_sentence_rejected_despite_good_overall_ratio(self):
        spoken='先做摘要，再根据摘要还原地点分布、区域人数和队员之间的距离。还原偏差越大，就继续改。'
        text=spoken+'这就叫自监督学习，先把阵型信息保留下来。'
        transcript={'segments':[{'words':[{'word':c,'start':i*.1,'end':(i+1)*.1} for i,c in enumerate(spoken)]}],'text':spoken}
        with self.assertRaisesRegex(ValueError,'tail insufficient'):
            align(text,transcript,[],trim=0,speed=1,audio_seconds=7,frames=220,fps=30)


if __name__=='__main__':
    unittest.main()
