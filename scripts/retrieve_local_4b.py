"""Exact dense retrieval on this machine (CPU) over the stored Qwen3-Embedding-4B vectors, then the submission.

Used when the Kaggle search kernel cannot read the vector datasets (it dies on the first full read).
The query vectors come from the Kaggle encode kernel (notebooks/kaggle_encode_queries_4b.ipynb).

Inputs (local, read-only):
  artifacts/kaggle/query-vectors-4b/query_vectors.npy, query_ids.json   encode kernel output
  artifacts/kaggle/vectors-qwen3-4b-s0..s2/vectors/<part>.npy           float16, unit norm
  artifacts/kaggle/vectors-qwen3-4b-s0..s2/vectors/<part>-ids.parquet   chunk_id per row
  artifacts/kaggle/chunk-corpus-v1/chunks/<lang>/part-NNN.parquet       chunk text
  artifacts/kaggle/chunk-corpus-v1/hash_groups.parquet                  duplicate-text groups
  dataset/query.parquet, dataset/links_corpus_vi_en_zh.parquet

Output (artifacts/submission/): medxir-qwen3-4b-submission.json, .zip, retrieval_manifest.json.

Method: each part is scored by an exact float32 matrix product, its top-k is kept, and the per-part
candidates are merged into the global top-k. The same logic as scripts/build_retrieval_notebook.py.

    uv run python scripts/retrieve_local_4b.py
"""
import argparse
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
TOP_CHUNKS = 20
QUERY_BATCH = 200


def search(query_vectors, vector_paths):
    """Return per_query: for each query, the TOP_CHUNKS best (score, part, row) over all parts."""
    per_query = [[] for _ in range(len(query_vectors))]
    for path in vector_paths:
        key = path.stem
        vectors = np.load(path).astype(np.float32)
        assert vectors.shape[1] == query_vectors.shape[1] and np.isfinite(vectors).all(), key
        k = min(TOP_CHUNKS, len(vectors))
        for start in range(0, len(query_vectors), QUERY_BATCH):
            scores = query_vectors[start:start + QUERY_BATCH] @ vectors.T
            top = np.argpartition(-scores, k - 1, axis=1)[:, :k]
            top_scores = np.take_along_axis(scores, top, axis=1)
            for offset in range(len(top)):
                per_query[start + offset].extend(
                    (float(s), key, int(r)) for s, r in zip(top_scores[offset], top[offset]))
        print(key, f'{len(vectors):,} rows', flush=True)
        del vectors
    for qi in range(len(per_query)):
        per_query[qi].sort(key=lambda item: item[0], reverse=True)
        per_query[qi] = per_query[qi][:TOP_CHUNKS]
    return per_query


def chunk_id_map(per_query, vector_roots):
    """(part, row) -> chunk_id, read from each part's ids file next to its .npy."""
    wanted = {}
    for query_rows in per_query:
        for _, key, row in query_rows:
            wanted.setdefault(key, set()).add(row)
    chunk_id_at = {}
    for key, rows in wanted.items():
        ids_path = next(root / f'{key}-ids.parquet' for root in vector_roots if (root / f'{key}-ids.parquet').exists())
        column = pq.read_table(ids_path, columns=['chunk_id']).column('chunk_id').to_pylist()
        chunk_id_at[key] = {row: column[row] for row in rows}
    return chunk_id_at


def chunk_records(chunk_files, wanted_ids):
    records = {}
    for path in chunk_files:
        table = pq.read_table(path, columns=['chunk_id', 'text_sha256', 'rep_doc_id', 'lang', 'order', 'chunk_text'])
        names = table.column('chunk_id').to_pylist()
        for i, cid in enumerate(names):
            if cid in wanted_ids:
                records[cid] = dict(
                    text_sha256=table.column('text_sha256')[i].as_py(),
                    rep_doc_id=int(table.column('rep_doc_id')[i].as_py()),
                    chunk_order=int(table.column('order')[i].as_py()),
                    chunk_text=table.column('chunk_text')[i].as_py(),
                )
        del table
    return records


def group_lookup(hash_path):
    groups = pd.read_parquet(hash_path, columns=['text_sha256', 'doc_ids', 'fanout_safe'])
    return {
        sha: ([int(doc) for doc in doc_ids] if bool(safe) else None)
        for sha, doc_ids, safe in zip(groups['text_sha256'], groups['doc_ids'], groups['fanout_safe'])
    }


def build_submission(queries, per_query, chunk_id_at, records, lookup):
    submission, empty = [], 0
    for qi, query_id in enumerate(queries['id'].tolist()):
        chunks, relevant_docs, seen = [], [], set()
        for _, key, row in per_query[qi]:
            record = records[chunk_id_at[key][row]]
            doc_ids = lookup.get(record['text_sha256'])
            if not doc_ids:
                doc_ids = [record['rep_doc_id']]
            for doc_id in doc_ids:
                if doc_id not in seen:
                    seen.add(doc_id)
                    relevant_docs.append(doc_id)
                chunks.append(dict(doc_id=doc_id, chunk_text=record['chunk_text'],
                                   chunk_order=record['chunk_order']))
        if not chunks:
            empty += 1
        submission.append(dict(id=int(query_id), relevant_docs=relevant_docs, relevant_chunks=chunks))
    return submission, empty


def validate(submission, queries, corpus_ids):
    assert len(submission) == 1200 and {row['id'] for row in submission} == set(map(int, queries['id'].tolist()))
    for entry in submission:
        assert set(entry['relevant_docs']).issubset(corpus_ids), entry['id']
        listed = set(entry['relevant_docs'])
        assert all(chunk['doc_id'] in listed and chunk['chunk_text'].strip() for chunk in entry['relevant_chunks'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts' / 'submission')
    args = parser.parse_args()
    started = time.perf_counter()
    kaggle = ROOT / 'artifacts' / 'kaggle'
    vector_roots = [kaggle / f'vectors-qwen3-4b-s{s}' / 'vectors' for s in range(3)]
    vector_paths = sorted(path for root in vector_roots for path in root.glob('*.npy'))
    assert len(vector_paths) == 9, 'expected 9 vector parts'

    query_vectors = np.load(kaggle / 'query-vectors-4b' / 'query_vectors.npy')
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet', columns=['id'])
    assert json.loads((kaggle / 'query-vectors-4b' / 'query_ids.json').read_text()) == queries['id'].tolist()
    assert query_vectors.shape == (1200, 2560) and np.isfinite(query_vectors).all()

    per_query = search(query_vectors, vector_paths)
    print('search done', round(time.perf_counter() - started, 1), 's', flush=True)
    chunk_id_at = chunk_id_map(per_query, vector_roots)
    wanted_ids = {cid for mapping in chunk_id_at.values() for cid in mapping.values()}
    chunk_files = sorted((kaggle / 'chunk-corpus-v1' / 'chunks').glob('*/part-*.parquet'))
    records = chunk_records(chunk_files, wanted_ids)
    assert not wanted_ids - set(records), 'candidate chunks without source text'
    lookup = group_lookup(kaggle / 'chunk-corpus-v1' / 'hash_groups.parquet')
    submission, empty = build_submission(queries, per_query, chunk_id_at, records, lookup)
    corpus_ids = set(map(int, pd.read_parquet(ROOT / 'dataset' / 'links_corpus_vi_en_zh.parquet', columns=['id'])['id']))
    validate(submission, queries, corpus_ids)

    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / 'medxir-qwen3-4b-submission.json'
    zip_path = args.out / 'medxir-qwen3-4b-submission.zip'
    json_path.write_text(json.dumps(submission, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(json_path, arcname=json_path.name)
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == [json_path.name]
    manifest = dict(
        model='Qwen/Qwen3-Embedding-4B', search='exact float32 matmul over stored float16 vectors, local CPU',
        query_count=len(submission), top_chunks=TOP_CHUNKS, empty_queries=empty,
        json_bytes=json_path.stat().st_size, zip_bytes=zip_path.stat().st_size,
        elapsed_seconds=round(time.perf_counter() - started, 1),
    )
    (args.out / 'retrieval_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
