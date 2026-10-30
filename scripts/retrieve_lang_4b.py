"""Top-K chunks of ONE language for each query, from the stored Qwen3-Embedding-4B vectors (local CPU).

The global top-500 (retrieve_local_4b.py) holds few en chunks because cross-lingual cosine scores run lower
(best en score 0.59 vs 0.73 for vi), so this searches one language part on its own. Output is in the BM25
candidate format (chunk_ids, scores, query_ids).

    uv run python scripts/retrieve_lang_4b.py --part en-part-000 --top-k 100
"""
import argparse
import json

import numpy as np
import pandas as pd

import retrieve_local_4b as dense

ROOT = dense.ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--part', required=True, help='vector part stem, e.g. en-part-000')
    parser.add_argument('--top-k', type=int, default=100)
    args = parser.parse_args()
    kaggle = ROOT / 'artifacts' / 'kaggle'
    paths = [p for p in dense.part_paths(kaggle) if p.stem == args.part]
    assert len(paths) == 1 and paths[0].exists(), f'{args.part}.npy missing'
    query_vectors = np.load(kaggle / 'query-vectors-4b' / 'query_vectors.npy')
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    assert json.loads((kaggle / 'query-vectors-4b' / 'query_ids.json').read_text()) == queries['id'].tolist()
    scores, parts, rows = dense.search(query_vectors, paths, args.top_k)
    ids = dense.chunk_ids(parts, rows, paths)
    out = ROOT / 'artifacts' / 'submission' / f'candidates-4b-{args.part}-k{args.top_k}.npz'
    np.savez(out, chunk_ids=ids, scores=scores, query_ids=queries['id'].to_numpy())
    print(out, 'top1 mean', round(float(scores[:, 0].mean()), 4), 'top100 mean', round(float(scores[:, -1].mean()), 4))


if __name__ == '__main__':
    main()
