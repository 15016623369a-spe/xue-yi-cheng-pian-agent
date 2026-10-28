import unittest

from video_agent.alignment import check_phrase_transcript, voice_numbers


class VoicePhraseTests(unittest.TestCase):
    def test_reference_tail_is_rejected_before_joining(self):
        for prefix in ['打的','咋的','那','把的']:
            with self.subTest(prefix=prefix), self.assertRaisesRegex(ValueError,'多读'):
                check_phrase_transcript('别人复盘，这波怎么又掉人了？',
                                        {'text':prefix+'别人复盘这波怎么又掉人了'})

    def test_first_word_substitution_retries(self):
        with self.assertRaisesRegex(ValueError,'句首识别不符'):
            check_phrase_transcript('这回真有人把职业队怎么布防，写成了论文。',
                                    {'text':'大回真有人把职业队怎么布防写成了论文'})

    def test_legitimate_reference_words_are_preserved(self):
        self.assertEqual(check_phrase_transcript('打的就是这个位置。',
                         {'text':'打的就是这个位置'})['recognized_extra_prefix'],'')

    def test_short_technical_name_and_homophone_pronoun(self):
        check_phrase_transcript('这套方法叫TAGNet。',{'text':'这套方法叫TAGNET'})
        check_phrase_transcript('它复盘：这些开局到底有几种套路？',
                               {'text':'他复盘这些开局到底有几种套路'})

    def test_long_phrase_must_cover_ending(self):
        with self.assertRaisesRegex(ValueError,'末尾|覆盖不足'):
            check_phrase_transcript('先把所有的数据都整理出来，随后检查每个队员的区域分布。',
                                    {'text':'先把所有的数据都整理出来随后检查'})

    def test_internal_repeated_clause_retries(self):
        with self.assertRaisesRegex(ValueError,'重复讲解'):
            check_phrase_transcript('图注意力负责分配参考比重，距离也参与计算。',
                {'text':'图注意力负责分配参考比重距离负责分配参考比重距离也参与计算'})

    def test_missing_first_snapshot_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'数值识别不全'):
            check_phrase_transcript('每回合取十、十二、十五、十八、二十秒，连续五份状态。',
                {'text':'每回合取12 15 18 20秒连续五份状态'})
        check_phrase_transcript('每回合取十、十二、十五、十八、二十秒，连续五份状态。',
            {'text':'每回合取10 12 15 18 20秒连续五份状态'})

    def test_percentage_decimal_and_lexical_name(self):
        self.assertEqual(voice_numbers('二十二支战队，百分之三点一和百分之十一点六。'),[22,3.1,11.6])
        self.assertEqual(voice_numbers('重建误差零点零零八五八。'),[.00858])
        self.assertEqual(voice_numbers('沙二这张地图和CS2。'),[])


if __name__=='__main__':
    unittest.main()
