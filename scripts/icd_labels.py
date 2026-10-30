"""ICD-10 chapter labels for every chunk and every query, from Octen-Embedding-8B vectors (local CPU, light RAM).

A chapter vector is the mean of its vi/en/zh description vectors (kaggle_embed_icd_octen8b.ipynb), L2-normalised.
Raw cosine to a chapter has a chapter-specific baseline (some descriptions sit closer to every text), so each
chapter column is z-scored: chunks over the whole corpus, queries over the 1,200 queries (queries carry the
retrieval instruction, so their scores sit on another scale). Labels are softmax(z / T) over the 22 chapters.

Inputs:  artifacts/kaggle/icd-octen8b/icd_vectors.npy, icd_rows.json
         artifacts/kaggle/vectors-octen8b-s{0,1,2}/vectors/<part>.npy, <part>-ids.parquet
         artifacts/kaggle/query-vectors-octen8b/query_vectors.npy
Outputs: artifacts/icd/chunk_z.npy   (2.6M x 22 float16, rows = the 9 Octen parts in sorted order)
         artifacts/icd/chunk_ids.npy (chunk_id per row), artifacts/icd/query_z.npy (1200 x 22), chapters.json

    uv run python scripts/icd_labels.py
"""
import json

import numpy as np
import pandas as pd

import retrieve_local_4b as dense

ROOT = dense.ROOT
ROW_BLOCK = 100_000


def chapter_vectors(icd_dir):
    vectors = np.load(icd_dir / 'icd_vectors.npy').astype(np.float32)
    rows = json.loads((icd_dir / 'icd_rows.json').read_text(encoding='utf-8'))
    chapters = list(dict.fromkeys(r['id'] for r in rows))
    out = np.stack([vectors[[i for i, r in enumerate(rows) if r['id'] == c]].mean(axis=0) for c in chapters])
    return chapters, out / np.linalg.norm(out, axis=1, keepdims=True)


def main():
    kaggle = ROOT / 'artifacts' / 'kaggle'
    out_dir = ROOT / 'artifacts' / 'icd'
    out_dir.mkdir(parents=True, exist_ok=True)
    chapters, g = chapter_vectors(kaggle / 'icd-octen8b')
    paths = sorted((p.with_name(p.name.removesuffix('-ids.parquet') + '.npy') for s in range(3)
                    for p in (kaggle / f'vectors-octen8b-s{s}' / 'vectors').glob('*-ids.parquet')), key=lambda p: p.stem)
    assert len(paths) == 9

    sims, ids = [], []
    for path in paths:
        vectors = np.load(path, mmap_mode='r')
        part = np.empty((len(vectors), len(chapters)), dtype=np.float32)
        for start in range(0, len(vectors), ROW_BLOCK):
            part[start:start + ROW_BLOCK] = np.asarray(vectors[start:start + ROW_BLOCK], dtype=np.float32) @ g.T
        sims.append(part)
        ids.append(pd.read_parquet(path.with_name(path.stem + '-ids.parquet'))['chunk_id'].to_numpy())
        assert len(ids[-1]) == len(part), path.stem
        print(path.stem, f'{len(part):,}', flush=True)
    sims = np.concatenate(sims)
    chunk_z = (sims - sims.mean(axis=0)) / sims.std(axis=0)

    query = np.load(kaggle / 'query-vectors-octen8b' / 'query_vectors.npy').astype(np.float32) @ g.T
    query_z = (query - query.mean(axis=0)) / query.std(axis=0)

    np.save(out_dir / 'chunk_z.npy', chunk_z.astype(np.float16))
    np.save(out_dir / 'chunk_ids.npy', np.concatenate(ids).astype(object), allow_pickle=True)
    np.save(out_dir / 'query_z.npy', query_z.astype(np.float32))
    (out_dir / 'chapters.json').write_text(json.dumps(chapters), encoding='utf-8')
    top_chunk = np.bincount(chunk_z.argmax(axis=1), minlength=len(chapters)) / len(chunk_z)
    top_query = np.bincount(query_z.argmax(axis=1), minlength=len(chapters)) / len(query_z)
    print(pd.DataFrame(dict(chapter=chapters, chunk_share=top_chunk.round(3), query_share=top_query.round(3))).to_string(index=False))


if __name__ == '__main__':
    main()
