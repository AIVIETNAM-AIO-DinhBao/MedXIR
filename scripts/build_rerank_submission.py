"""Build a submission from reranker scores: the top-150 dense chunks reordered by the reranker.

The chunk list is the same 150 candidates as the dense run; only their order changes. Documents are ranked by
their best reranked chunk (the same max rule as the dense submissions), and the submission keeps N documents.
Fusion mixes the per-query z-scores of the reranker and the dense score: score = z(rerank) + w * z(dense).

    uv run python scripts/build_rerank_submission.py --model Qwen3-Reranker-4B --fusion 0 --docs 200 --tag rr4b
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import retrieve_local_4b as dense

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / 'artifacts' / 'rerank' / 'results'


def z(values):
    return (values - values.mean(axis=1, keepdims=True)) / (values.std(axis=1, keepdims=True) + 1e-9)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', required=True)
    parser.add_argument('--fusion', type=float, default=0.0, help='weight of the dense z-score (0 = reranker only)')
    parser.add_argument('--docs', type=int, default=200)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts' / 'submission')
    args = parser.parse_args()

    cands = pd.read_parquet(ROOT / 'artifacts' / 'rerank' / 'candidates.parquet',
                            columns=['query_id', 'rank', 'chunk_id', 'dense_score'])
    scores = pd.read_parquet(RESULTS / f'scores_{args.model}.parquet')
    frame = cands.merge(scores, on=['query_id', 'rank'], how='left', validate='one_to_one')
    assert frame['score'].notna().all(), 'missing reranker scores'
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    qids = queries['id'].tolist()
    frame = frame.sort_values(['query_id', 'rank'])
    rerank = frame.pivot(index='query_id', columns='rank', values='score').loc[qids].to_numpy(dtype=np.float64)
    dense_scores = frame.pivot(index='query_id', columns='rank', values='dense_score').loc[qids].to_numpy(dtype=np.float64)
    chunk_ids = frame.pivot(index='query_id', columns='rank', values='chunk_id').loc[qids].to_numpy(dtype=object)
    fused = z(rerank) + args.fusion * z(dense_scores)
    order = np.argsort(-fused, axis=1, kind='stable')
    ids = np.take_along_axis(chunk_ids, order, axis=1)

    kaggle = ROOT / 'artifacts' / 'kaggle'
    chunk_files = sorted((kaggle / 'chunk-corpus-v1' / 'chunks').glob('*/part-*.parquet'))
    records = dense.chunk_records(chunk_files, set(ids.ravel().tolist()), set(ids.ravel().tolist()))
    assert not set(ids.ravel().tolist()) - set(records), 'candidate chunks without source text'
    lookup = dense.group_lookup(kaggle / 'chunk-corpus-v1' / 'hash_groups.parquet')
    corpus_ids = set(map(int, pd.read_parquet(ROOT / 'dataset' / 'links_corpus_vi_en_zh.parquet', columns=['id'])['id']))
    queries_frame = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    submission = dense.build_submission(queries_frame, ids, records, lookup, args.docs, ids.shape[1])
    dense.validate(submission, queries_frame, corpus_ids)
    name = f'{args.tag}-d{args.docs}-c{ids.shape[1]}'
    print(name, dense.write_submission(submission, args.out, f'medxir-{name}-submission'), flush=True)


if __name__ == '__main__':
    main()
