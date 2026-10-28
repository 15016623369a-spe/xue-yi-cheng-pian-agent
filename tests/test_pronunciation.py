import copy
import unittest
from video_agent.alignment import align, check_phrase_transcript
from video_agent.pronunciation import spoken_text,validate,caption_cut

class PronunciationTests(unittest.TestCase):
 def test_longest_single_pass_and_identifier_boundaries(self):
  rules={'[CLS]':'句首标记','BERT':'伯特模型','B':'批量','Token':'词元','Tokenizer':'分词器','词元':'不应连锁替换'}
  self.assertEqual(spoken_text('BERT B [CLS] Token Tokenizer',rules),'伯特模型 批量 句首标记 词元 分词器')
  self.assertEqual(spoken_text('BERT Tokenizer',{'B':'批量','Token':'词元'}),'BERT Tokenizer')
 def test_invalid_or_unused_rules_rejected(self):
  for rules in [{'[CLS]':''},{'不存在':'读法'},['bad'],{'[CLS]':'坏\n换行'}]:
   with self.subTest(rules=rules),self.assertRaises(ValueError):validate('这里 [CLS]',rules)
 def test_original_captions_and_event_follow_spoken_timestamps(self):
  text='先看[CLS]，再看[MASK]。两个词元功能不同。';rules={'[CLS]':'句首标记','[MASK]':'掩码词元'};spoken=spoken_text(text,rules)
  words=[{'word':c,'start':i*.15,'end':(i+1)*.15} for i,c in enumerate(spoken)]
  result=align(text,{'text':spoken,'segments':[{'words':words}]},[{'id':'E01','anchor':'[MASK]','seconds':1}],trim=0,speed=1,audio_seconds=len(spoken)*.15,frames=180,fps=30,pronunciations=rules)
  self.assertEqual(result['alignment']['matched_char_ratio'],1)
  self.assertEqual(''.join(c['text'] for c in result['captions']),text)
  expected=round(spoken.index('掩码词元')*.15*30)+3
  self.assertEqual(result['events'][0]['start'],expected)
 def test_terms_are_not_split_across_caption_chunks(self):
  text='前面的说明文字已经比较长了现在是[LONG_TERM]';cut=text.index('[LONG_TERM]')+4
  self.assertEqual(caption_cut(text,cut,{'[LONG_TERM]':'术语'}),text.index('[LONG_TERM]'))
  self.assertEqual(caption_cut('[LONG_TERM]',3,{'[LONG_TERM]':'术语'}),11)
 def test_reading_map_does_not_accept_omitted_content(self):
  text='[CLS]、[SEP]、[PAD]、[UNK]、[MASK]，各自有重要分工。';rules={'[CLS]':'句首标记','[SEP]':'分隔标记','[PAD]':'填充标记','[UNK]':'未知词标记','[MASK]':'掩码词元'}
  actual='各自有重要分工'
  with self.assertRaisesRegex(ValueError,'insufficient'):
   align(text,{'text':actual,'segments':[{'words':[{'word':actual,'start':0,'end':2}]}]},[],trim=0,speed=1,audio_seconds=2,frames=80,fps=30,pronunciations=rules)
 def test_observed_bad_phrase_is_rejected_before_voice_cache_commit(self):
  with self.assertRaisesRegex(ValueError,'覆盖不足'):
   check_phrase_transcript('BERT 常用 [CLS]、[SEP]、[PAD]、[UNK]、[MASK]，各有分工，比如 [CLS] 标记句首。',{'text':'被RT常用CRS SPPi的ERNCK各有分工比如CRS标记聚首'})
