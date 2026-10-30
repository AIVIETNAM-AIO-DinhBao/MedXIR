"""Pseudo-relevance feedback for the 8B dense lists: q' = normalize(q + alpha * mean(vectors of the top-N chunks)).

The feedback chunks are the top N of the fused ens4 list (4B 0.25, Octen 0.35, 0.6B 0.15, BM25 0.25), not of the
model's own list; their vectors are read from the model's stored corpus vectors, so every model drifts towards the
same ensemble-chosen passages. The new queries are searched exactly as retrieve_local_8b.py does.

Inputs:  artifacts/submission/candidates-{4b,octen8b,06b}-k500.npz, artifacts/bm25/vi-candidates-k500.npz,
         query and corpus vectors as in retrieve_local_8b.py
Output:  artifacts/submission/candidates-<model>-prf-k500.npz  (same format; use with fuse_scores.py --extra)

    uv run python scripts/prf_local_8b.py --model octen8b --model qwen8b
"""
import argparse
import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import fuse_scores as fs
import retrieve_local_4b as dense
import retrieve_local_8b as r8

ROOT = dense.ROOT
SUB = ROOT / 'artifacts' / 'submission'
ENS4 = dict(octen=0.35, m06=0.15, bm25=0.25)


def feedback_ids(top):
    """(queries, top) chunk ids of the ens4 fusion."""
    kaggle = ROOT / 'artifacts' / 'kaggle'
    cached = np.load(SUB / 'candidates-4b-k500.npz')
    dense_ids = dense.chunk_ids(cached['parts'], cached['rows'], dense.part_paths(kaggle))
    bm25 = np.load(ROOT / 'artifacts' / 'bm25' / 'vi-candidates-k500.npz', allow_pickle=True)
    octen = np.load(SUB / 'candidates-octen8b-k500.npz', allow_pickle=True)
    m06 = np.load(SUB / 'candidates-06b-k500.npz', allow_pickle=True)
    lists = [(dense_ids, cached['scores'], 1 - sum(ENS4.values())), (bm25['chunk_ids'], bm25['scores'], ENS4['bm25']),
             (octen['chunk_ids'], octen['scores'], ENS4['octen']), (m06['chunk_ids'], m06['scores'], ENS4['m06'])]
    ids, _ = fs.combine(lists, 'union', 500)
    return ids[:, :top]


def feedback_vectors(ids, vector_paths):
    """(queries, top, dim) float32 vectors of the given chunk ids from the stored corpus vectors."""
    wanted = set(ids.ravel().tolist())
    found = {}
    for path in vector_paths:
        column = pq.read_table(path.with_name(f'{path.stem}-ids.parquet'), columns=['chunk_id']).column('chunk_id').to_pylist()
        hits = [(i, c) for i, c in enumerate(column) if c in wanted]
        if hits:
            vectors = np.load(path, mmap_mode='r')
            for i, c in hits:
                found[c] = np.asarray(vectors[i], dtype=np.float32)
    assert set(found) == wanted, f'{len(wanted - set(found))} feedback chunks missing'
    return np.stack([[found[c] for c in row] for row in ids])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', action='append', required=True, choices=['octen8b', 'qwen8b'])
    parser.add_argument('--alpha', type=float, default=0.3)
    parser.add_argument('--top', type=int, default=5)
    args = parser.parse_args()
    kaggle = ROOT / 'artifacts' / 'kaggle'
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    ids = feedback_ids(args.top)
    for model in args.model:
        vector_paths = sorted((path.with_name(path.name.removesuffix('-ids.parquet') + '.npy') for s in range(3)
                               for path in (kaggle / f'vectors-{model}-s{s}' / 'vectors').glob('*-ids.parquet')),
                              key=lambda path: path.stem)
        assert len(vector_paths) == 9 and all(p.exists() for p in vector_paths), vector_paths
        query_dir = kaggle / f'query-vectors-{model}'
        q = np.load(query_dir / 'query_vectors.npy').astype(np.float32)
        assert json.loads((query_dir / 'query_ids.json').read_text()) == queries['id'].tolist()
        feedback = feedback_vectors(ids, vector_paths).mean(axis=1)
        new_q = q + args.alpha * feedback
        new_q /= np.linalg.norm(new_q, axis=1, keepdims=True)
        print(model, 'cosine(q, q\') mean', round(float((q * new_q).sum(axis=1).mean()), 4), flush=True)

        all_scores, all_parts, all_rows = [], [], []
        for part_index, path in enumerate(vector_paths):
            scores, rows = r8.search_part(new_q, path, r8.TOP_K)
            all_scores.append(scores)
            all_rows.append(rows)
            all_parts.append(np.full(scores.shape, part_index, dtype=np.int16))
            print(model, path.stem, flush=True)
        scores, parts, rows = (np.concatenate(a, axis=1) for a in (all_scores, all_parts, all_rows))
        order = np.argsort(-scores, axis=1, kind='stable')[:, :r8.TOP_K]
        scores, parts, rows = (np.take_along_axis(a, order, axis=1) for a in (scores, parts, rows))
        out_ids = dense.chunk_ids(parts, rows, vector_paths)
        out = SUB / f'candidates-{model}-prf-k{r8.TOP_K}.npz'
        np.savez(out, chunk_ids=out_ids, scores=scores, query_ids=queries['id'].to_numpy())
        old = np.load(SUB / f'candidates-{model}-k500.npz', allow_pickle=True)['chunk_ids']
        print(out, 'top20 shared with plain', round(float(np.mean([len(set(out_ids[i, :20]) & set(old[i, :20])) for i in range(len(old))])), 1),
              'top150', round(float(np.mean([len(set(out_ids[i, :150]) & set(old[i, :150])) for i in range(len(old))])), 1), flush=True)


if __name__ == '__main__':
    main()
