"""Write the reranker input: the current top-150 dense chunks per query, with their text.

Reads the cached dense candidates (artifacts/submission/candidates-4b-k500.npz) and the chunk corpus, and
writes artifacts/rerank/candidates.parquet (one row per query and rank). That file is uploaded as the Kaggle
dataset boinhbo/medxir-rerank-candidates so the offline GPU benchmark can read it.

    uv run python scripts/build_rerank_candidates.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

import retrieve_local_4b as dense

ROOT = Path(__file__).resolve().parent.parent
TOP = 150
OUT = ROOT / 'artifacts' / 'rerank'


def main():
    kaggle = ROOT / 'artifacts' / 'kaggle'
    vector_paths = dense.part_paths(kaggle)
    cached = np.load(ROOT / 'artifacts' / 'submission' / 'candidates-4b-k500.npz')
    assert cached['keys'].tolist() == [p.stem for p in vector_paths]
    ids = dense.chunk_ids(cached['parts'][:, :TOP], cached['rows'][:, :TOP], vector_paths)
    scores = cached['scores'][:, :TOP]
    queries = pd.read_parquet(ROOT / 'dataset' / 'query.parquet')
    wanted = set(ids.ravel().tolist())
    chunk_files = sorted((kaggle / 'chunk-corpus-v1' / 'chunks').glob('*/part-*.parquet'))
    records = dense.chunk_records(chunk_files, wanted, wanted)
    assert not wanted - set(records)
    rows = []
    for qi, (qid, text) in enumerate(zip(queries['id'].tolist(), queries['query'].tolist())):
        for rank in range(TOP):
            cid = ids[qi, rank]
            rec = records[cid]
            rows.append(dict(query_id=int(qid), query=text, rank=rank, chunk_id=cid, dense_score=float(scores[qi, rank]),
                             doc_id=rec['rep_doc_id'], lang=rec.get('lang') or '', chunk_text=rec['chunk_text']))
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_parquet(OUT / 'candidates.parquet', index=False, compression='zstd')
    print(len(frame), 'rows', frame['query_id'].nunique(), 'queries', round((OUT / 'candidates.parquet').stat().st_size / 1e6, 1), 'MB')


if __name__ == '__main__':
    main()
