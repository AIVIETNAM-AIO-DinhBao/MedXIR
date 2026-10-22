"""Source-text cleaning, applied before chunking. Every chunk row carries CLEAN_VERSION.

1. Line-break tags become line breaks: <br>, <br/>, <br />, &lt;br&gt;, and the fullwidth
   ＜br＞ / ＜br ／＞ found in about 1,077 zh documents. A run of tags (with the whitespace
   around it) becomes as many newlines as it had tags or existing newlines, capped at one blank
   line, so a break next to a paragraph break does not add a second blank line.
2. zh lead-in: a first paragraph starting with 核心提示 is dropped when the rest of the document
   repeats its text, because the site shows a summary before the article. A lead-in with no
   text is dropped too. A standalone summary that does not reappear is kept.

    核心提示：婴儿泪囊炎 婴儿泪囊炎眼睛有分泌物是一种眼睛轻度感染性疾病……\n\n婴儿泪囊炎眼睛有分泌物是一种眼睛轻度感染性疾病。一是可能……
    -> 婴儿泪囊炎眼睛有分泌物是一种眼睛轻度感染性疾病。一是可能……

Nothing else changes (no Unicode or whitespace normalisation).

    from text_clean import clean_text
"""
import re

CLEAN_VERSION = 'clean-v1'

BR_TAG = r'(?:<br\s*/?>|＜br\s*[/／]?\s*＞|&lt;br\s*/?&gt;)'
BR_RUN = re.compile(rf'[ \t　\n]*(?:{BR_TAG}[ \t　\n]*)+', re.IGNORECASE)
BR_ONE = re.compile(BR_TAG, re.IGNORECASE)
LEAD_IN = re.compile(r'^(?:核心提示[ \t　]*[:：][ \t　]*)+')
PARAGRAPH_BREAK = re.compile(r'\n\s*\n')


def line_break(match):
    run = match.group(0)
    return '\n' * min(max(len(BR_ONE.findall(run)), run.count('\n')), 2)


def drop_lead_in(text):
    """Drop a top 核心提示 paragraph whose first 40 characters (whitespace removed, trailing
    ellipsis removed) share a 10-character run with the rest of the document."""
    if not LEAD_IN.match(text):
        return text
    first, *rest = PARAGRAPH_BREAK.split(text, maxsplit=1)
    rest_text = rest[0] if rest else ''
    body = re.sub(r'\s+', '', LEAD_IN.sub('', first)).rstrip('….')
    if not body:
        return rest_text
    head, rest_plain = body[:40], re.sub(r'\s+', '', rest_text)
    if any(head[i:i + 10] in rest_plain for i in range(max(1, len(head) - 9))):
        return rest_text
    return text


def clean_text(text, lang):
    text = BR_RUN.sub(line_break, text)
    if lang == 'zh':
        text = drop_lead_in(text)
    return text
