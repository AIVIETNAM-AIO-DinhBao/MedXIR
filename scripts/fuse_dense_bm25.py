"""Fuse the dense (Qwen3-Embedding-4B) and Vietnamese BM25 candidates with weighted reciprocal rank fusion,
then write submissions the same way as scripts/retrieve_local_4b.py (top 20 chunks, N documents).

Inputs:  artifacts/submission/candidates-4b-k500.npz     dense candidates (retrieve_local_4b.py)
         artifacts/bm25/vi-candidates-k500.npz           BM25 candidates (bm25_vi.py)
Outputs: artifacts/submission/medxir-<tag>-d<N>-submission.json/.zip

    uv run python scripts/fuse_dense_bm25.py --tag rrf --w-bm25 1.0 --doc-counts 100,150,200
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import retrieve_local_4b as dense

ROOT = dense.ROOT


def rrf(dense_ids, bm25_ids, bm25_scores, rrf_k, w_dense, w_bm25, depth):
    fused = np.empty((len(dense_ids), depth), dtype=object)
    for qi in range(len(dense_ids)):
        score = {}
        for rank, cid in enumerate(dense_ids[qi]):
            score[cid] = score.get(cid, 0.0) + w_dense / (rrf_k + rank + 1)
        for rank, cid in enumerate(bm25_ids[qi]):
            if bm25_scores[qi, rank] <= 0:
                break
            score[cid] = score.get(cid, 0.0) + w_bm25 / (rrf_k + rank + 1)
        fused[qi] = [cid for cid, _ in sorted(score.items(), key=lambda item: -item[1])[:depth]]
    return fused


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--rrf-k', type=int, default=60)
    parser.add_argument('--w-dense', type=float, default=1.0)
    parser.add_argument('--w-bm25', type=float, default=1.0)
    parser.add_argument('--bm25-file', type=Path, default=ROOT / 'artifacts' / 'bm25' / 'vi-candidates-k500.npz')
    parser.add_argument('--depth', type=int, default=500)
    parser.add_argument('--doc-counts', default='100,150,200')
    parser.add_argument('--chunk-counts', default='20')
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts' / 'submission')
    args = parser.parse_args()
    kaggle = ROOT / 'artifacts' / 'kaggle'
    vector_paths = dense.part_paths(kaggle)
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    cached = np.load(args.out / 'candidates-4b-k500.npz')
    assert cached['keys'].tolist() == [p.stem for p in vector_paths]
    dense_ids = dense.chunk_ids(cached['parts'], cached['rows'], vector_paths)
    bm25 = np.load(args.bm25_file, allow_pickle=True)
    assert bm25['query_ids'].tolist() == queries['id'].tolist()
    ids = rrf(dense_ids, bm25['chunk_ids'], bm25['scores'], args.rrf_k, args.w_dense, args.w_bm25, args.depth)

    chunk_counts = [int(x) for x in args.chunk_counts.split(',')]
    wanted = set(ids.ravel().tolist())
    chunk_files = sorted((kaggle / 'chunk-corpus-v1' / 'chunks').glob('*/part-*.parquet'))
    records = dense.chunk_records(chunk_files, wanted, set(ids[:, :max(chunk_counts)].ravel().tolist()))
    assert not wanted - set(records)
    lookup = dense.group_lookup(kaggle / 'chunk-corpus-v1' / 'hash_groups.parquet')
    corpus_ids = set(map(int, pd.read_parquet(ROOT / 'dataset' / 'links_corpus_vi_en_zh.parquet', columns=['id'])['id']))
    for n in (int(x) for x in args.doc_counts.split(',')):
        for c in chunk_counts:
            submission = dense.build_submission(queries, ids, records, lookup, n, c)
            dense.validate(submission, queries, corpus_ids)
            name = f'd{n}' + ('' if c == dense.TOP_CHUNKS else f'-c{c}')
            print(name, dense.write_submission(submission, args.out, f'medxir-{args.tag}-{name}-submission'), flush=True)
    top_overlap = np.mean([len(set(ids[i, :20]) & set(dense_ids[i, :20])) for i in range(len(ids))])
    print(json.dumps(dict(tag=args.tag, rrf_k=args.rrf_k, w_dense=args.w_dense, w_bm25=args.w_bm25,
                          mean_top20_overlap_with_dense=round(float(top_overlap), 2))))


if __name__ == '__main__':
    main()
