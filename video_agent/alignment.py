from __future__ import annotations

import bisect
import difflib
import math
import re


def normalize(text: str) -> str:
    for a, b in zip("零一二三四五六七八九", "0123456789"):
        text = text.replace(a, b)
    return "".join(c for c in text.lower() if c.isalnum())


_VOICE_DIGITS={c:i for i,c in enumerate('零一二三四五六七八九')}
_VOICE_DIGITS['两']=2
_VOICE_UNITS={'十':10,'百':100,'千':1000,'万':10000}
def _voice_chinese_number(s):
 if '点' in s:
  a,b=s.split('点',1);return str(_voice_chinese_number(a))+'.'+''.join(str(_VOICE_DIGITS[c]) for c in b)
 if not any(c in _VOICE_UNITS for c in s):return int(''.join(str(_VOICE_DIGITS[c]) for c in s))
 total=0;n=0
 for c in s:
  if c in _VOICE_DIGITS:n=_VOICE_DIGITS[c]
  else:
   unit=_VOICE_UNITS[c]
   if unit==10000:total=(total+n)*unit;n=0
   else:total+=(n or 1)*unit;n=0
 return total+n
def voice_numbers(s):
    """Explicit positive counts/decimals; do not count 百分之 as 100.

    Single Chinese digits need a counting unit so lexical names/idioms do not
    trigger false numeric failures. This is a narrow ASR guard, not a math parser.
    """
    s=s.replace('百分之','')
    values=[]
    for m in re.finditer(r'(?<![a-zA-Z0-9])[0-9]+(?:\.[0-9]+)?|[零一二三四五六七八九两十百千万]+(?:点[零一二三四五六七八九]+)?',s):
        v=m.group()
        if len(v)==1 and v in _VOICE_DIGITS:
            following=s[m.end():m.end()+1]
            if following not in '秒分点个名份类场支回组维行列倍页次种套':
                continue
        values.append(float(v) if v[0].isdigit() else float(_voice_chinese_number(v)))
    return values



def check_phrase_transcript(text: str, transcript: dict) -> dict:
    """Reject an extra spoken prefix before joining independently cloned phrases.

    This checks recognized content, not perceived voice similarity. Technical
    terms may be transcribed phonetically; scene-level alignment remains required.
    """
    expected, actual = normalize(text), normalize(transcript.get("text", ""))
    matcher = difflib.SequenceMatcher(None, expected, actual, autojunk=False)
    blocks = matcher.get_matching_blocks()
    ratio = sum(b.size for b in blocks) / max(1, len(expected))
    ops = matcher.get_opcodes()
    for tag,a,b,c,d in ops:
        extra=actual[c:d]
        if tag=='insert' and len(extra)>=6 and any(extra[i:i+6] in expected for i in range(len(extra)-5)):
            raise ValueError("分句内重复讲解："+extra)
    wanted,heard=voice_numbers(text),voice_numbers(transcript.get("text",""))
    position=0
    for number in heard:
        if position<len(wanted) and abs(number-wanted[position])<1e-8:position+=1
    if position<len(wanted):
        raise ValueError("数值识别不全："+str(wanted[position:]))
    prefix = next((actual[c:d] for tag,a,b,c,d in ops
                   if tag == "insert" and a == 0 and b == 0), "")
    if prefix:
        raise ValueError("句首多读：" + prefix)
    if (expected and actual and '\u4e00' <= expected[0] <= '\u9fff'
            and '\u4e00' <= actual[0] <= '\u9fff'
            and expected[0] != actual[0]
            and not {expected[0],actual[0]} <= {'他','她','它'}):
        raise ValueError("句首识别不符：" + actual[:6])
    if not actual or ratio < .65:
        raise ValueError(f"分句识别覆盖不足：{ratio:.1%}")
    # Short English names can be rendered phonetically by ASR. For longer
    # phrases, also guard the closing clause rather than only total coverage.
    tail = max(0, len(expected)-10)
    matched = {b.a+i for b in blocks for i in range(b.size)}
    tail_ratio = sum(i in matched for i in range(tail,len(expected))) / max(1,len(expected)-tail)
    if len(expected) >= 18 and tail_ratio < .4:
        raise ValueError(f"分句末尾识别覆盖不足：{tail_ratio:.1%}")
    return {"matched_char_ratio":ratio,"tail_matched_char_ratio":tail_ratio,
            "recognized_extra_prefix":"","asr_text":transcript.get("text", ""),
            "direct_listening":False}


def check_reference_bleed(text: str, transcript: dict, reference_text: str) -> dict:
    """Reject recognized reference-tail insertions, not arbitrary ASR differences."""
    expected,actual,reference=normalize(text),normalize(transcript.get("text", "")),normalize(reference_text)
    suspicious=[]
    for tag,a,b,c,d in difflib.SequenceMatcher(None,expected,actual,autojunk=False).get_opcodes():
        extra=actual[c:d]
        if tag=='insert' and len(extra)>=2 and any(reference.endswith(extra[i:j]) for i in range(len(extra)) for j in range(i+2,min(len(extra),i+6)+1)):
            suspicious.append({"text":extra,"asr_position":c})
    if suspicious:
        raise ValueError("配音疑似混入参考录音尾音，请调整种子重试或更换参考音频："+"、".join(x["text"] for x in suspicious))
    return {"recognized_reference_tail_insertions":0,"method":"conservative_text_alignment","direct_listening":False}


def interpolate(x: int, points: list[int], values: list[float]) -> float:
    if x <= points[0]:
        return values[0]
    if x >= points[-1]:
        return values[-1]
    i = bisect.bisect_right(points, x)
    a, b = points[i-1], points[i]
    return values[i-1] + (values[i]-values[i-1]) * (x-a)/(b-a)


def align(text: str, transcript: dict, events: list[dict], *, trim: float, speed: float,
          audio_seconds: float, frames: int, fps: int, lead: int = 3, pronunciations=None) -> dict:
    from .pronunciation import spoken_text, caption_cut
    def expected_form(value):return normalize(spoken_text(value,pronunciations))
    expected = expected_form(text)
    actual = ""
    times = []
    for segment in transcript.get("segments", []):
        for word in segment.get("words", []):
            if word["end"] <= trim:
                continue
            chars = normalize(word.get("word", ""))
            for i, char in enumerate(chars):
                actual += char
                times.append(max(0, word["start"]-trim + (word["end"]-word["start"])*i/max(1,len(chars)))/speed)
    matches: dict[int, float] = {}
    for block in difflib.SequenceMatcher(None, expected, actual, autojunk=False).get_matching_blocks():
        for j in range(block.size):
            matches[block.a+j] = times[block.b+j]
    ratio = len(matches)/max(1,len(expected))
    if ratio < .65 or not matches:
        raise ValueError(f"ASR/script alignment insufficient ({ratio:.1%}); inspect transcript before continuing")
    # A good overall ratio can hide a completely omitted closing sentence.
    tail_start = max(0, len(expected)-16)
    tail_ratio = sum(i in matches for i in range(tail_start,len(expected)))/max(1,len(expected)-tail_start)
    if len(expected)>=16 and tail_ratio < .5:
        raise ValueError(f"ASR/script tail insufficient ({tail_ratio:.1%}); closing words may be missing")
    points = sorted(matches)
    values = [matches[p] for p in points]

    def at(i: int) -> int:
        sec = audio_seconds if i == len(expected) else interpolate(i, points, values)
        return max(lead, min(frames-1, round(sec*fps)+lead))

    chunks, current = [], ""
    for part in re.findall(r"[^，。！？；：]+[，。！？；：]?", text):
        if current and len(current)+len(part)>23:
            chunks.append(current)
            current = ""
        # Long clauses without punctuation still have bounded caption width.
        while len(part)>23:
            if current:
                chunks.append(current)
                current = ""
            cut=caption_cut(part,23,pronunciations)
            chunks.append(part[:cut])
            part = part[cut:]
        current += part
        if len(current)>=11 or current.endswith(("。","？","！")):
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    captions, position = [], 0
    for chunk in chunks:
        start = at(position)
        position += len(expected_form(chunk))
        end = min(frames, at(position)+5)
        if captions and start <= captions[-1]["start"]:
            start = captions[-1]["start"]+1
        if start >= frames:
            raise ValueError("Caption timing collapses; transcript needs review")
        captions.append({"text": chunk, "start": start, "end": max(start+1,end)})
    for a, b in zip(captions, captions[1:]):
        a["end"] = b["start"]
    captions[-1]["end"] = min(frames,captions[-1]["end"])
    placed = []
    for event in events:
        anchor = expected_form(event["anchor"])
        position = expected.find(anchor)
        if position < 0:
            raise ValueError(f"Event anchor not found: {event['anchor']}")
        start = at(position)
        placed.append({**event, "start": start,
                       "duration": min(frames-start, max(1,round(event.get('seconds',1.5)*fps))),
                       "anchor_matched": True})
    return {"captions": captions, "events": placed, "alignment": {
        "matched_char_ratio": ratio, "tail_matched_char_ratio": tail_ratio, "asr_text": transcript.get("text",""),
        "method": "word_timestamp_text_alignment", "interpolation_used": True,
        "pronunciations":pronunciations or {}}}


def srt_time(frame: int, fps: int) -> str:
    total_ms = round(frame*1000/fps)
    seconds, ms = divmod(total_ms,1000)
    minutes, seconds = divmod(seconds,60)
    hours, minutes = divmod(minutes,60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"
