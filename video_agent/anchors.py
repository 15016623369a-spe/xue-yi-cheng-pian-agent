"""Resolve timing anchors using unique verbatim overlap, never semantic guesses."""
from __future__ import annotations
import copy
import difflib
import re

VERSION='unique-verbatim-v1'


def indexed(text):
    chars=[];positions=[]
    for i,c in enumerate(text):
        if c.isalnum():
            for normalized in c.lower():chars.append(normalized);positions.append(i)
    return ''.join(chars),positions


def substantive(text):
    chinese=len(re.findall(r'[\u4e00-\u9fff]',text));letters=len(re.findall(r'[A-Za-z]',text))
    return (chinese>=4 or letters>=12) and chinese*2+letters>=12


def word_edge(text,start,end):
    ascii_word=lambda c:c.isascii() and c.isalnum()
    return not ((start and ascii_word(text[start-1]) and ascii_word(text[start])) or
                (end<len(text) and ascii_word(text[end-1]) and ascii_word(text[end])))


def resolve(speech,anchor):
    if not isinstance(speech,str) or not isinstance(anchor,str) or not anchor.strip():return None
    count=speech.count(anchor)
    if count==1:return anchor
    # Never choose one of multiple occurrences by trimming an ambiguous anchor.
    if count>1:return None
    source,source_indices=indexed(anchor);target,target_indices=indexed(speech)
    if not source or not target or len(source)>2000 or len(target)>10000:return None
    if target.count(source)>1:return None
    proposals=[]
    for a,b,size in difflib.SequenceMatcher(None,source,target,autojunk=False).get_matching_blocks():
        if not size:continue
        matched=source[a:a+size]
        if not substantive(matched) or target.count(matched)!=1:continue
        if not word_edge(anchor,source_indices[a],source_indices[a+size-1]+1) or not word_edge(speech,target_indices[b],target_indices[b+size-1]+1):continue
        fragment=speech[target_indices[b]:target_indices[b+size-1]+1]
        if speech.count(fragment)!=1:continue
        score=len(re.findall(r'[\u4e00-\u9fff]',matched))*2+len(re.findall(r'[A-Za-z]',matched))
        proposals.append((score,fragment))
    if not proposals:return None
    best=max(p[0] for p in proposals);choices={fragment for score,fragment in proposals if score==best}
    return next(iter(choices)) if len(choices)==1 else None


def repair(story):
    """Only generated timing data is changed; speech, evidence and content stay intact."""
    data=copy.deepcopy(story);repairs=[]
    for scene in data.get('scenes',[]) if isinstance(data.get('scenes'),list) else []:
        if not isinstance(scene,dict):continue
        for event in scene.get('events',[]) if isinstance(scene.get('events'),list) else []:
            if not isinstance(event,dict):continue
            event.pop('anchor_repair',None)  # Do not trust model-authored repair claims.
            original=event.get('anchor');resolved=resolve(scene.get('speech'),original)
            if resolved is not None and resolved!=original:
                record={'scene':scene.get('id'),'event':event.get('id'),'original':original,'resolved':resolved,
                        'method':VERSION,'reason':'取原触发句与本镜口播唯一重合的逐字片段，未改写口播'}
                event['anchor']=resolved;event['anchor_repair']=record;repairs.append(record)
    return data,repairs
