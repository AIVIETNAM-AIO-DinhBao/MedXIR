"""Paragraph-first chunking with exact source offsets.

Every chunk is text[start:end] of the exported document, so chunk_text is always
a verbatim extract (SUBMISSION.md). Size is counted in words, or in characters/2
for Chinese. The scorer divides overlap by the *reference* chunk length, so a
chunk shorter than 40% of the reference can never match; err on the large side.

    uv run python scripts/chunking.py stats artifacts/corpus_v1/en.parquet --limits 120 250 400
"""
import argparse
import re
from dataclasses import dataclass

PARAGRAPH = re.compile(r'\n\s*\n')
SENTENCE_END = re.compile(r'(?<=[.!?。！？；;])\s+|(?<=[。！？；])')


@dataclass
class Chunk:
    order: int
    start: int
    end: int
    text: str


def size(text, lang):
    return len(text) // 2 if lang == 'zh' else len(text.split())


def spans(text, pattern, start=0, end=None):
    """Non-empty (start, end) spans of text[start:end] split by pattern, whitespace-trimmed."""
    end = len(text) if end is None else end
    cursor = start
    for match in list(pattern.finditer(text, start, end)) + [None]:
        stop = match.start() if match else end
        piece = text[cursor:stop]
        if piece.strip():
            left = cursor + len(piece) - len(piece.lstrip())
            right = cursor + len(piece.rstrip())
            yield left, right
        if match:
            cursor = match.end()


def sentences(text, start, end, lang, limit):
    """Sentence spans; a sentence above the limit (tables, lists without punctuation)
    is cut at line breaks, then hard-cut at whitespace (or every 2*limit chars for zh)."""
    for s_start, s_end in spans(text, SENTENCE_END, start, end):
        if size(text[s_start:s_end], lang) <= limit:
            yield s_start, s_end
            continue
        for l_start, l_end in spans(text, re.compile(r'\n'), s_start, s_end):
            if size(text[l_start:l_end], lang) <= limit:
                yield l_start, l_end
            elif lang == 'zh':
                for cut in range(l_start, l_end, 2 * limit):
                    yield cut, min(cut + 2 * limit, l_end)
            else:
                words = [m.span() for m in re.finditer(r'\S+', text[l_start:l_end])]
                for i in range(0, len(words), limit):
                    piece = words[i:i + limit]
                    yield l_start + piece[0][0], l_start + piece[-1][1]


def units(text, lang, limit):
    """Paragraphs, with paragraphs above the limit split into sentence groups."""
    for start, end in spans(text, PARAGRAPH):
        if size(text[start:end], lang) <= limit:
            yield start, end
            continue
        group = None
        for s_start, s_end in sentences(text, start, end, lang, limit):
            if group and size(text[group[0]:s_end], lang) > limit:
                yield group
                group = None
            group = (group[0], s_end) if group else (s_start, s_end)
        if group:
            yield group


def is_heading(text, lang):
    """Short line without closing punctuation: a section title or list label."""
    text = text.strip()
    short = len(text) <= 30 if lang == 'zh' else len(text.split()) <= 12
    return short and not text.endswith(('.', '!', '?', ':', ';', '。', '！', '？', '：', '；', '"', '”', ')'))


def chunk_document(text, lang, limit=350, minimum=40, overlap=50):
    """Greedily merge adjacent units up to `limit`; fold a tiny tail into the previous chunk.

    Headings never end a chunk: trailing headings move to the next chunk, so a title
    always stays with the text it introduces. A chunk that continues the same section
    also repeats the previous chunk's last whole sentences (up to `overlap` units);
    a chunk opening on a heading starts a new section and gets no overlap.
    """
    groups = []
    for unit in units(text, lang, limit):
        if groups and size(text[groups[-1][0][0]:unit[1]], lang) <= limit:
            groups[-1].append(unit)
            continue
        carried = []
        while groups and len(groups[-1]) > 1 and is_heading(text[groups[-1][-1][0]:groups[-1][-1][1]], lang):
            carried.insert(0, groups[-1].pop())
        groups.append(carried + [unit])
    merged = [(group[0][0], group[-1][1]) for group in groups]
    if len(merged) > 1 and size(text[merged[-1][0]:merged[-1][1]], lang) < minimum:
        tail = merged.pop()
        merged[-1] = (merged[-1][0], tail[1])
    chunks = []
    for order, (start, end) in enumerate(merged):
        if order and overlap and not is_heading(text[start:end].split('\n', 1)[0], lang):
            previous_start, previous_end = merged[order - 1]
            for s_start, _ in reversed(list(spans(text, SENTENCE_END, previous_start, previous_end))):
                if size(text[s_start:previous_end], lang) > overlap:
                    break
                start = s_start
        chunks.append(Chunk(order, start, end, text[start:end]))
    return chunks


def sample_rows(path, sample):
    """(lang, text) of a random sample, read one row group at a time: take() on the whole
    text column overflows the 2 GB string offsets of the 1 GB vi corpus."""
    import numpy as np
    import pyarrow.parquet as pq

    file = pq.ParquetFile(path)
    total = file.metadata.num_rows
    wanted = None
    if sample and total > sample:
        wanted = set(np.random.default_rng(0).choice(total, sample, replace=False).tolist())
    rows, offset = [], 0
    for group in range(file.metadata.num_row_groups):
        count = file.metadata.row_group(group).num_rows
        columns = file.read_row_group(group, columns=['lang', 'text']).to_pydict()
        rows += [(columns['lang'][i], columns['text'][i]) for i in range(count)
                 if wanted is None or offset + i in wanted]
        offset += count
    return rows


def stats(path, limits, sample):
    import numpy as np

    rows = sample_rows(path, sample)
    langs, texts = [lang for lang, _ in rows], [text for _, text in rows]
    lengths = np.array([size(t, l) for t, l in zip(texts, langs)])
    unit = 'chars/2' if langs and langs[0] == 'zh' else 'words'
    print(f'{path}: {len(texts):,} documents sampled, length in {unit}')
    print('  document length p10/p50/p90/p99:', *np.percentile(lengths, [10, 50, 90, 99]).astype(int))
    for limit in limits:
        counts, chunk_sizes = [], []
        for text, lang in zip(texts, langs):
            chunks = chunk_document(text, lang, limit)
            counts.append(len(chunks))
            chunk_sizes.extend(size(c.text, lang) for c in chunks)
            assert all(text[c.start:c.end] == c.text for c in chunks)
        scale = len(texts) if not sample else None
        print(f'  limit {limit:>4}: chunks/doc mean {np.mean(counts):.2f}, '
              f'chunk size p50/p90 {int(np.percentile(chunk_sizes, 50))}/{int(np.percentile(chunk_sizes, 90))}, '
              f'single-chunk docs {np.mean(np.array(counts) == 1):.0%}'
              + (f', total {sum(counts):,}' if scale else ''))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['stats'])
    parser.add_argument('parquet')
    parser.add_argument('--limits', type=int, nargs='+', default=[120, 250, 400])
    parser.add_argument('--sample', type=int, default=20000, help='0 = all documents')
    args = parser.parse_args()
    stats(args.parquet, args.limits, args.sample)


if __name__ == '__main__':
    main()
