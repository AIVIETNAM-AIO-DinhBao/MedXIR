# Hybrid notebook: embedding models and weighted fusion

Edit the first config cell of `src/notebooks/kaggle_hybrid_retrieval.ipynb`.
Source changes belong in `scripts/build_hybrid_notebook.py`,
`scripts/hybrid_embedding_cells.py`, `scripts/hybrid_inputs.py` and
`scripts/hybrid_retrieval.py`; regenerate with:

```powershell
uv run python scripts/build_hybrid_notebook.py
```

For a single model:

```python
EMBEDDING_MODEL = 'qwen8b'  # qwen4b / qwen8b / octen8b
USE_MODEL_FUSION = False
```

For fusion:

```python
USE_MODEL_FUSION = True
MODEL_WEIGHTS = {'qwen4b': 0.3, 'qwen8b': 0.5, 'octen8b': 0.2}
```

`EMBEDDING_MODEL` is ignored in fusion mode. Weights must be finite and
nonnegative, with at least one positive. They are normalized to sum to one;
`1, 2, 1` is equivalent to `.25, .5, .25`. Weight zero disables that model and
its assets. The default remains a single Qwen4B model.

Each model encodes the current full/sub queries and retrieves/PRF-searches its
own vector space. One weighted RRF stage combines its full list at the model
weight, sub lists at `model_weight * W_SUB`, and PRF at `model_weight * W_PRF`.
BM25 is included once at `W_BM25`. `RRF_K` controls the rank denominator.
Cosines from different models are not added together. Without reranking,
multi-model ranking uses RRF scores; a single plain dense model keeps its cosine
scores. With `USE_RERANK=True`, these weights determine the pool sent to the
reranker; reranker scores then determine its final order.

Matrices are loaded/searched/released one model at a time. A complete 8B matrix
is about 23.7 GB fp16, versus 14.8 GB for 4B. Fusion reads all enabled matrices
sequentially. Existing rerank, document rerank and neighbour expansion settings
remain available.

## Inputs

Attach one full corpus: https://www.kaggle.com/datasets/ntklinhfitus/medxir-chunk-corpus-v2

Each active model needs its three original vector outputs plus its additions.
With `ENCODE_HERE=True`, also attach its packaged model:

| Key | Model package | Original outputs | Additions dataset |
|---|---|---|---|
| `qwen4b` | `boinhbo/medxir-package-models` | `boinhbo/medxir-embed-corpus-4b-s0`, `-s1`, `-s2` | `ntklinhfitus/medxir-vectors-qwen3-4b-additions` |
| `qwen8b` | `boinhbo/medxir-package-qwen8b` | `dinhbaobao/medxir-embed-corpus-qwen8b-s0`, `-s1`, `-s2` | `ntklinhfitus/medxir-vectors-qwen3-8b-additions` |
| `octen8b` | `boinhbo/medxir-package-octen8b` | `boinhbo/medxir-embed-corpus-octen8b-s0`, `-s1`, `-s2` | `ntklinhfitus/medxir-vectors-octen-8b-additions` |

The YAML `configs/kaggle/hybrid_retrieval_rtx6000.yaml` lists all these inputs.
Private sources must be shared with the running account. Do not attach another
copy of the same model's vector parts. IDs are checked against the selected
language corpus parts before GPU allocation. Manifest model names distinguish
the two 4096-dimensional models; their document prefixes and model revisions
are validated too.

Queries are encoded with the chosen default/custom instruction and split toggle
for each active model. Saved caches are labelled by model:
`query_variant_vectors_<key>.npy` and `query_variants_<key>.json`. Reuse with
`ENCODE_HERE=False` requires matching model, revision, query IDs/order, text
variants, instruction and encoding format. The old plain 1200-query outputs do
not include custom instructions or sub-queries and are not treated as hybrid
variant caches. Legacy unlabelled hybrid caches are supported only for 4B.

`hybrid_manifest.json` records every selected model's source manifests, parts,
dimensions and normalized model weights. `dense_rank` in `pool.parquet` refers
to the weighted full-query RRF ranking in multi-model mode.

## Validation

26 local targeted tests passed. The Kaggle CPU integration job successfully executed the
actual generated load/search/fusion cells on six synthetic chunks, with real
PyTorch. It covers each single model, three-model fusion and a zero-weight model
whose files are absent, checking full/sub/PRF rankings against NumPy. Its report
is `runs/smoke/hybrid-multimodel-smoke-20261030-v1/artifacts/hybrid_multimodel_smoke.json`.
Full GPU inference and leaderboard submission are separate from this code change.
