# Incremental Qwen8B and Octen8B embeddings

Status: **COMPLETE.** Both GPU jobs and CPU repacks completed; both private
datasets were published and independently verified on Kaggle CPU. Local staging
copies and upload caches were removed after successful remote verification.

The full chunk corpus is already `ntklinhfitus/medxir-chunk-corpus-v2`. This task
adds model-specific vectors only, without appending chunks again or changing
existing Qwen4B artifacts or the original 8B vectors.

## Verified baseline inventory

| Family | Existing vector sources | Model | Document prefix | Dimensions |
|---|---|---|---|---:|
| Qwen8B | `dinhbaobao/medxir-embed-corpus-qwen8b-s0/s1/s2` | `Qwen/Qwen3-Embedding-8B` | empty | 4096 |
| Octen8B | `boinhbo/medxir-embed-corpus-octen8b-s0/s1/s2` | `Octen/Octen-Embedding-8B` | one space | 4096 |

All six original jobs are complete. Each family has 2,621,553 aligned baseline
chunk/vector IDs, verified on Kaggle CPU. The document contracts are bf16 model
inference, batch 16, max length 1024, left padding, last-token pooling, L2
normalization and float16 output. Original source notebooks were inspected.

Pinned packaged model revisions:

- Qwen: `1d8ad4ca9b3dd8059ad90a75d4983776a23d44af`, package version 2.
- Octen: `88695e4f8ed87b4ce08df2fea6ff25bfe0f9875b`, package version 1.

The CPU plans independently verify all baseline IDs and identify, for each model:

| Action | Rows |
|---|---:|
| Infer distinct new inputs | 267,982 |
| Copy compatible existing vectors | 1,035 |
| Copy another new input's vector | 1,388 |
| Total added chunk IDs | 270,405 |

Hashes cover the actual prefixed document input. No existing chunk ID requires
re-embedding. Each GPU job first re-encodes 27 baseline samples and requires
minimum cosine >= 0.999 before generating additions.

Both models passed 27 baseline checks: minimum cosine 0.9995905 for Octen and
0.9998174 for Qwen. Each generated all 270,405 vector rows with no NaNs. The
local monitors lost API connections during the runs; read-only recovery checked
the actual remote completion and manifests without resubmitting either job.
Both CPU repacks verified the 270,405 IDs, exact language-part order, numeric
values and bitwise preservation of the GPU output.

Both existing query outputs were verified on Kaggle CPU: 1,200 x 4096, exact
official query ID order, finite values and unit norms. They do not need encoding
again just because the document corpus grew. Report:
`runs/smoke/8b-query-check-20261030-v2/artifacts/query_outputs_validation.json`.

## Outputs and integration

Separate private datasets:

- `ntklinhfitus/medxir-vectors-qwen3-8b-additions` — published and remotely verified.
- `ntklinhfitus/medxir-vectors-octen-8b-additions` — published and remotely verified.

Remote verification reports:

- `runs/smoke/octen8b-verify-20261030-v1/artifacts/published_vectors_validation.json`
- `runs/smoke/qwen8b-verify-20261030-v1/artifacts/published_vectors_validation.json`

Each dataset has `embed_manifest.json` plus these files under `vectors/`:

| Stem | Language | Rows |
|---|---|---:|
| `vi-part-007` | VI | 95,938 |
| `en-part-001` | EN | 14 |
| `zh-part-001` | ZH | 174,453 |

Each stem has `.npy` and `-ids.parquet` files; the latter has a single `chunk_id`
column. Rows match corpus-v2 part order exactly. Each model's final inventory is
12 parts / 2,891,958 rows. The additions occupy about 2.22 GB per family.

Attach the full v2 chunk dataset once, each model's three original vector outputs
and its own additions dataset. Keep models separate: Qwen4B has 2560 dimensions;
both 8B models have 4096 dimensions but different embedding spaces. Do not
concatenate their matrices as if they were one model. The existing 4B-only hybrid
loader intentionally rejects other models; a multi-model inference notebook must
search each model separately and fuse rankings.

GPU jobs seal 25,000-row checkpoints with checksums. CPU repacking verifies
source checksums and preserves vector bytes while splitting language parts.
After private publication, a separate Kaggle CPU job verifies every remote file
SHA-256, every ID/order, dimensions, finite values and unit norms. Local staging
copies are removed only after remote verification; original source outputs stay
on Kaggle. Publication runs sequentially to fit the approximately 3 GB free disk.

## Audit and reproduction

- `scripts/inventory_8b_sources.py`: read-only source and manifest inventory.
- `scripts/build_8b_delta_notebooks.py --model <family> --stage plan|gpu`.
- `scripts/prepare_8b_delta.py`, `scripts/embed_8b_delta.py`: CPU inventory and GPU inference.
- `scripts/build_8b_release_notebooks.py --model <family> --stage repack|verify`.
- `scripts/repack_8b_additions.py`: exact language-part reordering without inference.
- `scripts/publish_8b_additions_dataset.py --model <family>`: private publication.
- `scripts/finish_8b_releases.py`: sequential repack/publication/verification.
- `scripts/queue_qwen8b_gpu.py`: optional sequential GPU wait helper; it was stopped
  before Qwen was submitted directly once the user freed another GPU session.

Small offline fixtures passed for both models: old-ID exclusion, old/new-input
deduplication, prefixed input hashes, exact output IDs, cross-checkpoint copying,
resume skipping completed inference, corrupt-checkpoint rejection, language-part
reordering and bitwise preservation including signed zero.

Authoritative state: `artifacts/embedding_8b_inventory_20261030/` contains
`inventory.json`, `versions.json`, `GPU_RUNS.json`, `RELEASE_STATE.json` and each
published family's `releases/<family>/STATE.json`. CPU inventory runs are
`runs/smoke/<family>-delta-plan-20261030-v1/`. Use `GPU_RUNS.json` for actual GPU
run IDs: the first Qwen submission was rejected before running because the
account's maximum two batch GPU sessions were already occupied. Its replacement
uses a new immutable `-r2` slug submitted after another GPU session became free.

No leaderboard submission, commit, push or permission-sharing change is included.
