"""Exact top-500 over corpus v2 for every cached candidate list: search the three v2 addition parts and merge with
the v1 top-500 by score.

Corpus v2 = the nine v1 parts unchanged + vi-part-007, en-part-001, zh-part-001 (270,405 chunks,
docs/DATA_ADDITIONS_20261029.md). Same model, same cosine scale, disjoint chunk ids, so the merged top-500 equals a
search over all of v2. PRF query vectors are rebuilt exactly as scripts/prf_local_8b.py builds them (feedback from
the v1 ens4 top 5).

Inputs:  artifacts/submission/candidates-*-k500.npz (v1 lists), query vectors (plain, variants),
         artifacts/kaggle/vectors-{qwen3-4b,octen8b,qwen8b}-add/vectors/<part>.npy + -ids.parquet
Output:  artifacts/submission/candidates-<list>-v2-k500.npz  (chunk_ids, scores, query_ids; fuse_scores.py --extra)

    uv run python scripts/retrieve_additions.py
"""
import argparse
import json

import numpy as np
import pandas as pd

import prf_local_8b as prf
import retrieve_local_4b as dense
import retrieve_local_8b as r8
import retrieve_variants_8b as rv

ROOT = dense.ROOT
SUB = ROOT / 'artifacts' / 'submission'
KAGGLE = ROOT / 'artifacts' / 'kaggle'
ADD_PARTS = ['en-part-001', 'vi-part-007', 'zh-part-001']
FIELDS = ['question', 'terms', 'passage']


def addition_paths(folder):
    paths = [KAGGLE / folder / 'vectors' / f'{p}.npy' for p in ADD_PARTS]
    assert all(p.exists() and p.with_name(f'{p.stem}-ids.parquet').exists() for p in paths), paths
    return paths


def search_additions(q, paths):
    """(ids, scores), each (queries, 500), sorted, over the addition parts."""
    all_scores, all_parts, all_rows = [], [], []
    for part_index, path in enumerate(paths):
        scores, rows = r8.search_part(q, path, r8.TOP_K)
        all_scores.append(scores)
        all_rows.append(rows)
        all_parts.append(np.full(scores.shape, part_index, dtype=np.int16))
    scores, parts, rows = (np.concatenate(a, axis=1) for a in (all_scores, all_parts, all_rows))
    order = np.argsort(-scores, axis=1, kind='stable')[:, :r8.TOP_K]
    scores, parts, rows = (np.take_along_axis(a, order, axis=1) for a in (scores, parts, rows))
    return dense.chunk_ids(parts, rows, paths), scores


def merge(v1_ids, v1_scores, add_ids, add_scores):
    ids = np.concatenate([v1_ids, add_ids], axis=1)
    scores = np.concatenate([v1_scores.astype(np.float32), add_scores], axis=1)
    order = np.argsort(-scores, axis=1, kind='stable')[:, :r8.TOP_K]
    return np.take_along_axis(ids, order, axis=1), np.take_along_axis(scores, order, axis=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--only', nargs='+', default=None, help='list names to build, e.g. 4b octen8b-prf')
    args = parser.parse_args()
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    qids = queries['id'].tolist()
    variant_dir = KAGGLE / 'query-variants'
    assert json.loads((variant_dir / 'query_ids.json').read_text()) == qids

    jobs = []  # (name, query vectors loader, addition folder, v1 list loader)
    jobs.append(('4b', lambda: np.load(KAGGLE / 'query-vectors-4b' / 'query_vectors.npy'), 'vectors-qwen3-4b-add',
                 lambda: (dense.chunk_ids(*(lambda c: (c['parts'], c['rows']))(np.load(SUB / 'candidates-4b-k500.npz')),
                                          dense.part_paths(KAGGLE)), np.load(SUB / 'candidates-4b-k500.npz')['scores'])))
    feedback = None
    for model in ['octen8b', 'qwen8b']:
        def plain(model=model):
            return np.load(KAGGLE / f'query-vectors-{model}' / 'query_vectors.npy')

        def prf_vectors(model=model):
            nonlocal feedback
            feedback = prf.feedback_ids(5) if feedback is None else feedback
            q = plain(model).astype(np.float32)
            new_q = q + 0.3 * prf.feedback_vectors(feedback, rv.vector_paths(model)).mean(axis=1)
            return new_q / np.linalg.norm(new_q, axis=1, keepdims=True)

        def v1_list(name):
            z = np.load(SUB / f'candidates-{name}-k500.npz', allow_pickle=True)
            assert z['query_ids'].tolist() == qids, name
            return z['chunk_ids'], z['scores']

        jobs.append((model, plain, f'vectors-{model}-add', lambda n=model: v1_list(n)))
        jobs.append((f'{model}-prf', prf_vectors, f'vectors-{model}-add', lambda n=f'{model}-prf': v1_list(n)))
        for field in FIELDS:
            jobs.append((f'{model}-{field}', lambda m=model, f=field: np.load(variant_dir / f'vectors-{m}-{f}.npy'),
                         f'vectors-{model}-add', lambda n=f'{model}-{field}': v1_list(n)))

    for name, load_q, folder, load_v1 in jobs:
        if args.only and name not in args.only:
            continue
        q = load_q().astype(np.float32)
        assert q.shape[0] == len(qids) and np.isfinite(q).all(), name
        add_ids, add_scores = search_additions(q, addition_paths(folder))
        v1_ids, v1_scores = load_v1()
        ids, scores = merge(v1_ids, v1_scores, add_ids, add_scores)
        out = SUB / f'candidates-{name}-v2-k500.npz'
        np.savez(out, chunk_ids=ids, scores=scores, query_ids=queries['id'].to_numpy())
        new_top20 = np.mean([np.isin(ids[i, :20], add_ids[i]).sum() for i in range(len(ids))])
        new_top500 = np.mean([np.isin(ids[i], add_ids[i]).sum() for i in range(len(ids))])
        print(out.name, f'v2 additions in top-20: {new_top20:.1f}, in top-500: {new_top500:.1f}', flush=True)


if __name__ == '__main__':
    main()
