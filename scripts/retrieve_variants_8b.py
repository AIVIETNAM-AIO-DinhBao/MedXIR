"""Exact dense top-500 for the LLM query variants (scripts/build_variants_notebook.py), local CPU.

Each --vectors file is one variant encoded by one 8B model (vectors-<model>-<field>.npy, 1200 x 4096, query order);
it is searched over that model's stored corpus vectors exactly as retrieve_local_8b.py searches the plain queries.

Inputs:  artifacts/kaggle/query-variants/vectors-<model>-<field>.npy, query_ids.json   variants kernel output
Output:  artifacts/submission/candidates-<model>-<field>-k500.npz  (same format; use with fuse_scores.py --extra)

    uv run python scripts/retrieve_variants_8b.py --field question --field terms --field passage
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import retrieve_local_4b as dense
import retrieve_local_8b as r8

ROOT = dense.ROOT


def vector_paths(model):
    kaggle = ROOT / 'artifacts' / 'kaggle'
    paths = sorted((path.with_name(path.name.removesuffix('-ids.parquet') + '.npy') for s in range(3)
                    for path in (kaggle / f'vectors-{model}-s{s}' / 'vectors').glob('*-ids.parquet')),
                   key=lambda path: path.stem)
    assert len(paths) == 9 and all(p.exists() for p in paths), paths
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', action='append', choices=['octen8b', 'qwen8b'])
    parser.add_argument('--field', action='append', required=True, choices=['question', 'terms', 'passage'])
    parser.add_argument('--inputs', type=Path, default=ROOT / 'artifacts' / 'kaggle' / 'query-variants')
    args = parser.parse_args()
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    assert json.loads((args.inputs / 'query_ids.json').read_text()) == queries['id'].tolist()
    for model in args.model or ['octen8b', 'qwen8b']:
        paths = vector_paths(model)
        plain = np.load(ROOT / 'artifacts' / 'submission' / f'candidates-{model}-k500.npz', allow_pickle=True)['chunk_ids']
        for field in args.field:
            q = np.load(args.inputs / f'vectors-{model}-{field}.npy').astype(np.float32)
            assert q.shape == (1200, 4096) and np.isfinite(q).all()
            all_scores, all_parts, all_rows = [], [], []
            for part_index, path in enumerate(paths):
                scores, rows = r8.search_part(q, path, r8.TOP_K)
                all_scores.append(scores)
                all_rows.append(rows)
                all_parts.append(np.full(scores.shape, part_index, dtype=np.int16))
            scores, parts, rows = (np.concatenate(a, axis=1) for a in (all_scores, all_parts, all_rows))
            order = np.argsort(-scores, axis=1, kind='stable')[:, :r8.TOP_K]
            scores, parts, rows = (np.take_along_axis(a, order, axis=1) for a in (scores, parts, rows))
            ids = dense.chunk_ids(parts, rows, paths)
            out = ROOT / 'artifacts' / 'submission' / f'candidates-{model}-{field}-k{r8.TOP_K}.npz'
            np.savez(out, chunk_ids=ids, scores=scores, query_ids=queries['id'].to_numpy())
            shared = np.mean([len(set(ids[i, :20]) & set(plain[i, :20])) for i in range(len(ids))])
            print(out.name, 'top1 mean', round(float(scores[:, 0].mean()), 4), 'top20 shared with plain', round(float(shared), 1), flush=True)


if __name__ == '__main__':
    main()
