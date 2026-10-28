"""Explicit per-scene spoken forms; original script and captions stay intact."""
from __future__ import annotations
import re


def validate(text, rules):
    if not isinstance(rules, dict) or len(rules)>64:
        raise ValueError("术语读法最多64项，需为原词与读法的对应表")
    for source, spoken in rules.items():
        if (not isinstance(source,str) or not source or len(source)>120
                or source not in text or re.search(r"[，。！？；：\n\r]",source)
                or not isinstance(spoken,str) or not spoken.strip() or len(spoken)>160
                or any(ord(c)<32 for c in source+spoken)):
            raise ValueError("术语读法的原词须出现在口播中，读法不能为空")
    return rules


def pattern(rules):
    parts=[]
    for key in sorted(rules,key=len,reverse=True):
        part=re.escape(key)
        if key[0].isascii() and (key[0].isalnum() or key[0]=='_'):part=r'(?<![A-Za-z0-9_])'+part
        if key[-1].isascii() and (key[-1].isalnum() or key[-1]=='_'):part+=r'(?![A-Za-z0-9_])'
        parts.append(part)
    return re.compile('|'.join(parts)) if parts else None


def spoken_text(text, rules=None):
    rules=rules or {}; p=pattern(rules)
    return p.sub(lambda m:rules[m.group()],text) if p else text


def caption_cut(text, limit, rules):
    """Avoid splitting a written term whose spoken form is mapped as one unit."""
    p=pattern(rules or {})
    if p:
        for m in p.finditer(text):
            if m.start()<limit<m.end():return m.start() or m.end()
    return limit
