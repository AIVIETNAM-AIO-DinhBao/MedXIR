"""Exact dense top-500 over the stored 8B vectors (Octen-Embedding-8B or Qwen3-Embedding-8B), local CPU,
cached for fuse_scores.py --extra.

Same result as retrieve_local_4b.search, but each part is memory-mapped and scored in row blocks: a whole
vi part in float32 at 4096 dimensions is ~5.4 GB, too close to the 15 GB of this machine.

Inputs:  artifacts/kaggle/query-vectors-<model>/query_vectors.npy, query_ids.json   encode kernel output
         artifacts/kaggle/vectors-<model>-s{0,1,2}/vectors/<part>.npy, <part>-ids.parquet   embed kernel outputs
Output:  artifacts/submission/candidates-<model>-k500.npz  (chunk_ids, scores, query_ids)

    uv run python scripts/retrieve_local_8b.py --model octen8b
"""
import argparse
import json

import numpy as np
import pandas as pd

import retrieve_local_4b as dense

ROOT = dense.ROOT
TOP_K = 500
ROW_BLOCK = 50_000  # 1,200 x 50,000 float32 scores = 240 MB per block


def search_part(query_vectors, path, top_k):
    """(scores, rows), each (queries, k), unsorted, for one memory-mapped part."""
    vectors = np.load(path, mmap_mode='r')
    assert vectors.shape[1] == query_vectors.shape[1], path.stem
    best_scores = np.full((len(query_vectors), 0), -np.inf, dtype=np.float32)
    best_rows = np.empty((len(query_vectors), 0), dtype=np.int64)
    for start in range(0, len(vectors), ROW_BLOCK):
        block = np.asarray(vectors[start:start + ROW_BLOCK], dtype=np.float32)
        assert np.isfinite(block).all(), f'{path.stem} rows {start}+'
        scores = np.concatenate([best_scores, query_vectors @ block.T], axis=1)
        rows = np.concatenate([best_rows, np.broadcast_to(np.arange(start, start + len(block)), (len(query_vectors), len(block)))], axis=1)
        k = min(top_k, scores.shape[1])
        top = np.argpartition(-scores, k - 1, axis=1)[:, :k]
        best_scores, best_rows = np.take_along_axis(scores, top, axis=1), np.take_along_axis(rows, top, axis=1)
    return best_scores, best_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', required=True, choices=['octen8b', 'qwen8b'])
    args = parser.parse_args()
    kaggle = ROOT / 'artifacts' / 'kaggle'
    vector_paths = sorted((path.with_name(path.name.removesuffix('-ids.parquet') + '.npy') for s in range(3)
                           for path in (kaggle / f'vectors-{args.model}-s{s}' / 'vectors').glob('*-ids.parquet')),
                          key=lambda path: path.stem)
    assert len(vector_paths) == 9 and all(p.exists() for p in vector_paths), vector_paths
    query_dir = kaggle / f'query-vectors-{args.model}'
    query_vectors = np.load(query_dir / 'query_vectors.npy').astype(np.float32)
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    assert json.loads((query_dir / 'query_ids.json').read_text()) == queries['id'].tolist()
    assert query_vectors.shape == (1200, 4096) and np.isfinite(query_vectors).all()

    all_scores, all_parts, all_rows = [], [], []
    for part_index, path in enumerate(vector_paths):
        scores, rows = search_part(query_vectors, path, TOP_K)
        all_scores.append(scores)
        all_rows.append(rows)
        all_parts.append(np.full(scores.shape, part_index, dtype=np.int16))
        print(path.stem, flush=True)
    scores, parts, rows = (np.concatenate(a, axis=1) for a in (all_scores, all_parts, all_rows))
    order = np.argsort(-scores, axis=1, kind='stable')[:, :TOP_K]
    scores, parts, rows = (np.take_along_axis(a, order, axis=1) for a in (scores, parts, rows))
    ids = dense.chunk_ids(parts, rows, vector_paths)
    out = ROOT / 'artifacts' / 'submission' / f'candidates-{args.model}-k{TOP_K}.npz'
    np.savez(out, chunk_ids=ids, scores=scores, query_ids=queries['id'].to_numpy())
    print(out, 'top1 mean', round(float(scores[:, 0].mean()), 4))


if __name__ == '__main__':
    main()
