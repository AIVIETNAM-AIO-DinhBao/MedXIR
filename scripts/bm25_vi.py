"""BM25 over the Vietnamese chunks, for the Vietnamese queries.

Tokens are Vietnamese syllables plus adjacent-syllable bigrams (a word segmenter is not needed for
compounds such as "dạ dày" or "viêm gan"). Statistics (document frequency, average length) are global over all
2.1M Vietnamese chunks, so scores are comparable across parts. Only terms that occur in the queries are
indexed, which keeps the postings small; stopwords and very frequent unigrams are dropped from the query side.

Inputs:  dataset/query.parquet, artifacts/kaggle/chunk-corpus-v1/chunks/vi/part-*.parquet
Outputs: artifacts/bm25/vi-postings.npz            postings for the query terms (reused on rerun)
         artifacts/bm25/vi-candidates-k<K>.npz     top-K chunk ids and scores per query

    uv run python scripts/bm25_vi.py --top-k 500
"""
import argparse
import json
import re
import time
import unicodedata
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import scipy.sparse as sp

ROOT = Path(__file__).resolve().parent.parent
CHUNK_DIR = ROOT / 'artifacts' / 'kaggle' / 'chunk-corpus-v1' / 'chunks' / 'vi'
OUT_DIR = ROOT / 'artifacts' / 'bm25'
QUERY_PATH = ROOT / 'dataset' / 'query.parquet'

K1, B = 1.2, 0.75
TOKEN = re.compile(r'\d+(?:[.,]\d+)?|\w+')

# Old-style tone placement (hoà, thuý) -> new style (hòa, thúy), so both spellings index the same.
_OLD_NEW = []
for vowel_pair, (old, new) in {
    'oa': ('oàoáoảoãoạ', 'òaóaỏaõaọa'), 'oe': ('oèoéoẻoẽoẹ', 'òeóeỏeõeọe'), 'uy': ('uỳuýuỷuỹuỵ', 'ùyúyủyũyụy'),
}.items():
    olds = [old[i:i + 2] for i in range(0, len(old), 2)]
    news = [new[i:i + 2] for i in range(0, len(new), 2)]
    _OLD_NEW.extend(zip(olds, news))
TONE_FIX = {unicodedata.normalize('NFC', o): unicodedata.normalize('NFC', n) for o, n in _OLD_NEW}
TONE_RE = re.compile('|'.join(sorted(TONE_FIX, key=len, reverse=True)))

# Politeness and filler that carry no medical signal. "không", "bị", "có" are kept out of this list on purpose:
# negation matters, and the frequency cap below handles their weight.
CHITCHAT = [
    'cho em hỏi', 'cho tôi hỏi', 'cho mình hỏi', 'cho cháu hỏi', 'cho con hỏi', 'xin hỏi', 'em xin hỏi',
    'bác sĩ ơi', 'bác sỹ ơi', 'bác sĩ cho', 'xin cảm ơn', 'em cảm ơn', 'cảm ơn bác sĩ', 'cảm ơn bác sỹ',
    'không biết', 'có phải', 'là sao', 'như thế nào', 'ra sao', 'thế nào ạ',
]
STOPWORDS = set('''
ạ ơi nhé nha nhỉ ư à ừ ừm vâng dạ ơ
em anh chị cháu mình tôi bác sĩ sỹ ông bà cô chú con
và hoặc hay thì là mà của cho với để tại bởi vì nên nhưng song rồi đã đang sẽ vẫn còn cũng
này kia đó ấy nọ đây những các một mọi
ở trong trên dưới ngoài sau trước khi lúc từ đến tới
rất khá hơi quá lắm nhiều ít thật
được bởi do nếu thì thế vậy sao gì nào đâu ai bao nhiêu
xin hỏi muốn cần nên phải
'''.split())
MAX_UNIGRAM_DF = 0.15


def normalize(text):
    text = unicodedata.normalize('NFKC', text).lower()
    text = unicodedata.normalize('NFC', text)
    return TONE_RE.sub(lambda m: TONE_FIX[m.group(0)], text)


def syllables(text):
    return TOKEN.findall(normalize(text))


def query_terms(text):
    """Unigrams and bigrams of the query, with chit-chat and stopwords removed. Returns (unigrams, bigrams)."""
    text = normalize(text)
    for phrase in CHITCHAT:
        text = text.replace(phrase, ' ')
    toks = TOKEN.findall(text)
    uni = sorted({t for t in toks if t not in STOPWORDS and len(t) > 1 or t.isdigit()})
    bi = sorted({f'{a} {b}' for a, b in zip(toks, toks[1:]) if a not in STOPWORDS and b not in STOPWORDS})
    return uni, bi


VOCAB = {}


def index_part(args):
    """Postings of the query terms for one part, streamed in batches: (doc indices, term ids, tf) and lengths."""
    path, base = args
    docs, terms, tfs, lengths = [], [], [], []
    offset = base
    for batch in pq.ParquetFile(path).iter_batches(batch_size=5000, columns=['chunk_text']):
        for text in batch.column('chunk_text').to_pylist():
            toks = syllables(text)
            lengths.append(len(toks))
            counts = Counter(toks)
            counts.update(f'{a} {b}' for a, b in zip(toks, toks[1:]))
            for term, tf in counts.items():
                tid = VOCAB.get(term)
                if tid is not None:
                    docs.append(offset)
                    terms.append(tid)
                    tfs.append(min(tf, 255))
            offset += 1
    return (np.array(docs, dtype=np.int32), np.array(terms, dtype=np.int32), np.array(tfs, dtype=np.uint8),
            np.array(lengths, dtype=np.int32))


def build_postings(vocab_terms, cache_path, workers):
    parts = sorted(CHUNK_DIR.glob('part-*.parquet'))
    jobs, base, chunk_ids = [], 0, []
    for path in parts:
        ids = pq.read_table(path, columns=['chunk_id']).column('chunk_id').to_pylist()
        chunk_ids.extend(ids)
        jobs.append((path, base))
        base += len(ids)
    chunk_ids = np.array(chunk_ids, dtype=object)
    VOCAB.clear()
    VOCAB.update({term: i for i, term in enumerate(vocab_terms)})
    started = time.perf_counter()
    results = []
    with Pool(min(workers, len(jobs))) as pool:
        for n, result in enumerate(pool.imap(index_part, jobs), 1):
            results.append(result)
            print(f'indexed part {n}/{len(jobs)}, {time.perf_counter() - started:.0f}s', flush=True)
    docs, terms, tfs, lengths = (np.concatenate([r[i] for r in results]) for i in range(4))
    if cache_path is not None:
        np.savez(cache_path, docs=docs, terms=terms, tfs=tfs, lengths=lengths, chunk_ids=chunk_ids,
                 vocab=np.array(vocab_terms, dtype=object))
    return docs, terms, tfs, lengths, chunk_ids


def load_postings(cache_path):
    z = np.load(cache_path, allow_pickle=True)
    return z['docs'], z['terms'], z['tfs'], z['lengths'], z['chunk_ids'], z['vocab'].tolist()


def search(docs, terms, tfs, lengths, query_term_ids, n_queries, vocab_size, bigram_weight, top_k, block=150000):
    n_docs = len(lengths)
    df = np.bincount(terms, minlength=vocab_size)
    idf = np.log1p((n_docs - df + 0.5) / (df + 0.5)).astype(np.float32)
    avgdl = lengths.mean()
    # Postings are already ordered by document, so the CSR matrix is built directly (no COO copy).
    indptr = np.zeros(n_docs + 1, dtype=np.int64)
    np.cumsum(np.bincount(docs, minlength=n_docs), out=indptr[1:])
    weights = tfs.astype(np.float32)
    weights *= (K1 + 1) / (weights + K1 * (1 - B + B * (lengths[docs] / avgdl)).astype(np.float32))
    weights *= idf[terms]
    matrix = sp.csr_matrix((weights, terms, indptr), shape=(n_docs, vocab_size))
    del weights
    rows, cols, vals = [], [], []
    for qi, (uni, bi) in enumerate(query_term_ids):
        for t in uni:
            rows.append(t), cols.append(qi), vals.append(1.0)
        for t in bi:
            rows.append(t), cols.append(qi), vals.append(bigram_weight)
    q = sp.csr_matrix((vals, (rows, cols)), shape=(vocab_size, n_queries), dtype=np.float32)
    best_scores = np.zeros((n_queries, 0), dtype=np.float32)
    best_idx = np.zeros((n_queries, 0), dtype=np.int64)
    for start in range(0, n_docs, block):
        scores = (matrix[start:start + block] @ q).toarray().T
        k = min(top_k, scores.shape[1])
        top = np.argpartition(-scores, k - 1, axis=1)[:, :k]
        best_scores = np.concatenate([best_scores, np.take_along_axis(scores, top, axis=1)], axis=1)
        best_idx = np.concatenate([best_idx, top + start], axis=1)
        order = np.argsort(-best_scores, axis=1)[:, :top_k]
        best_scores, best_idx = np.take_along_axis(best_scores, order, axis=1), np.take_along_axis(best_idx, order, axis=1)
    return best_scores, best_idx, df


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--top-k', type=int, default=500)
    parser.add_argument('--bigram-weight', type=float, default=1.0)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--rebuild', action='store_true', help='tokenize the corpus again')
    parser.add_argument('--tag', default='', help='suffix for the candidates file')
    parser.add_argument('--no-cache', action='store_true', help='do not write the 3 GB postings cache')
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    queries = pd.read_parquet(QUERY_PATH)
    per_query = [query_terms(text) for text in queries['query']]
    vocab_terms = sorted({t for uni, bi in per_query for t in uni + bi})
    print(f'{len(queries)} queries, {len(vocab_terms):,} query terms', flush=True)
    cache = OUT_DIR / 'vi-postings.npz'
    started = time.perf_counter()
    if cache.exists() and not args.rebuild:
        docs, terms, tfs, lengths, chunk_ids, cached_vocab = load_postings(cache)
        assert cached_vocab == vocab_terms, 'query terms changed; rerun with --rebuild'
    else:
        docs, terms, tfs, lengths, chunk_ids = build_postings(vocab_terms, None if args.no_cache else cache, args.workers)
    print(f'postings {len(docs):,}, chunks {len(lengths):,}, {time.perf_counter() - started:.0f}s', flush=True)

    index = {t: i for i, t in enumerate(vocab_terms)}
    n_docs = len(lengths)
    df = np.bincount(terms, minlength=len(vocab_terms))
    query_term_ids = []
    for uni, bi in per_query:
        keep_uni = [index[t] for t in uni if df[index[t]] <= MAX_UNIGRAM_DF * n_docs]
        query_term_ids.append((keep_uni, [index[t] for t in bi]))
    scores, idx, _ = search(docs, terms, tfs, lengths, query_term_ids, len(queries), len(vocab_terms),
                            args.bigram_weight, args.top_k)
    out = OUT_DIR / f'vi-candidates-k{args.top_k}{args.tag}.npz'
    np.savez(out, scores=scores, chunk_ids=chunk_ids[idx], query_ids=queries['id'].to_numpy())
    empty = int((scores[:, 0] <= 0).sum())
    print(json.dumps(dict(out=out.name, queries_without_match=empty, seconds=round(time.perf_counter() - started, 1))))


if __name__ == '__main__':
    main()
