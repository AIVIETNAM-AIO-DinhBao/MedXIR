# Retrieval baseline on Kaggle

The Qwen3-Embedding-4B baseline reads the prebuilt FP32 `IndexFlatIP` files. It does not rebuild
the corpus index. A small offline FAISS CPU runtime reads each serialized part and reconstructs
its vectors; PyTorch performs exact float32 inner-product search on the Kaggle GPU, one part at a
time, then merges each part's top-20 candidates.

## Inputs

- Kernel source `boinhbo/medxir-package-models`: offline Qwen3-Embedding-4B model.
- Kernel source `boinhbo/medxir-chunk-corpus`: canonical chunk tables and `hash_groups.parquet`.
- Dataset `boinhbo/medxir-faiss-qwen3-4b-fp32`: nine `IndexFlatIP` parts, their row maps, and the
  index manifest.
- Dataset `boinhbo/medxir-retrieval-inputs`: `query.parquet`, official corpus IDs, and bundled
  FAISS 1.15.1 CPU runtime (including native libraries) for offline Python 3.13 loading. Search
  runs with CUDA through PyTorch, not FAISS GPU.

## Run

The Kaggle configuration is `configs/kaggle/retrieve_qwen3_4b_rtx6000.yaml`; the generated notebook
is `src/notebooks/kaggle_retrieve_qwen3_4b.ipynb`. The kernel requires RTX Pro 6000 and Internet
OFF. It writes `medxir-qwen3-4b-submission.json`, its one-file ZIP, and
`retrieval_manifest.json` to `/kaggle/working`.

Download the JSON and ZIP, then validate locally:

```bash
uv run python scripts/validate_submission.py \
  artifacts/kaggle/retrieval/medxir-qwen3-4b-submission.json \
  --zip
```

The local validator confirms all query IDs are present exactly once, document IDs exist in the
official corpus, and ZIP structure matches the submission instructions. Retrieval quality is not
measured because this repository has no relevance labels.
