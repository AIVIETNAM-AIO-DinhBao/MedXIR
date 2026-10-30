"""Exact dense top-500 over the stored Qwen3-Embedding-0.6B vectors (local CPU), cached for fuse_scores.py.

Same search as retrieve_local_4b.py, one vector part at a time. Output is in the BM25 candidate format
(chunk_ids, scores, query_ids), so fuse_scores.py --extra can weight it like any other list.

Inputs:  artifacts/kaggle/query-vectors-06b/query_vectors.npy, query_ids.json    encode kernel output
         artifacts/kaggle/vectors-qwen3-0-6b/vectors/<part>.npy, <part>-ids.parquet
Output:  artifacts/submission/candidates-06b-k500.npz

    uv run python scripts/retrieve_local_06b.py
"""
import json

import numpy as np
import pandas as pd

import retrieve_local_4b as dense

ROOT = dense.ROOT
TOP_K = 500


def main():
    kaggle = ROOT / 'artifacts' / 'kaggle'
    vector_paths = sorted((kaggle / 'vectors-qwen3-0-6b' / 'vectors').glob('*-part-???.npy'), key=lambda p: p.stem)
    assert len(vector_paths) == 9, vector_paths
    query_vectors = np.load(kaggle / 'query-vectors-06b' / 'query_vectors.npy').astype(np.float32)
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    assert json.loads((kaggle / 'query-vectors-06b' / 'query_ids.json').read_text()) == queries['id'].tolist()
    assert query_vectors.shape == (1200, 1024) and np.isfinite(query_vectors).all()
    scores, parts, rows = dense.search(query_vectors, vector_paths, TOP_K)
    ids = dense.chunk_ids(parts, rows, vector_paths)
    out = ROOT / 'artifacts' / 'submission' / f'candidates-06b-k{TOP_K}.npz'
    np.savez(out, chunk_ids=ids, scores=scores, query_ids=queries['id'].to_numpy())
    print(out, 'top1 mean', round(float(scores[:, 0].mean()), 4))


if __name__ == '__main__':
    main()
