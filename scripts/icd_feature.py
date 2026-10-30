"""ICD-10 agreement between a query and a chunk, from scripts/icd_labels.py outputs, for fuse_scores.py --icd.

Query labels keep the top chapters until their softmax mass reaches TOP_P (top-p, so a query that spans two
chapters keeps both), renormalised; chunk labels are the full softmax. agreement = sum over chapters of
p_query * p_chunk, in [0, 1].
"""
import numpy as np
import pandas as pd

import retrieve_local_4b as dense

ROOT = dense.ROOT


def softmax(z, t):
    e = np.exp((z - z.max(axis=1, keepdims=True)) / t)
    return e / e.sum(axis=1, keepdims=True)


def top_p(p, mass):
    order = np.argsort(-p, axis=1)
    sorted_p = np.take_along_axis(p, order, axis=1)
    keep_sorted = np.cumsum(sorted_p, axis=1) - sorted_p < mass  # keep a chapter while the mass before it < mass
    keep = np.zeros_like(p, dtype=bool)
    np.put_along_axis(keep, order, keep_sorted, axis=1)
    out = np.where(keep, p, 0.0)
    return out / out.sum(axis=1, keepdims=True)


class Agreement:
    def __init__(self, t=0.5, mass=0.8):
        icd = ROOT / 'artifacts' / 'icd'
        self.index = pd.Index(np.load(icd / 'chunk_ids.npy', allow_pickle=True))
        self.chunk_z = np.load(icd / 'chunk_z.npy', mmap_mode='r')
        self.query_p = top_p(softmax(np.load(icd / 'query_z.npy'), t), mass)
        self.t = t

    def __call__(self, ids):
        """(queries, depth) chunk ids -> (queries, depth) agreement with each row's query."""
        rows = self.index.get_indexer(ids.ravel())
        assert (rows >= 0).all(), 'chunk ids missing from the ICD labels'
        chunk_p = softmax(np.asarray(self.chunk_z[np.sort(rows)], dtype=np.float32)[np.argsort(np.argsort(rows))], self.t)
        chunk_p = chunk_p.reshape(*ids.shape, -1)
        return np.einsum('qdc,qc->qd', chunk_p, self.query_p).astype(np.float32)
