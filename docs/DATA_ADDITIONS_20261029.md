# Corpus additions 20261029 v2

Status: **COMPLETE. Final corpus and incremental Qwen4B vectors VERIFIED.**

Final counts: **168,894 new document IDs; 270,405 new chunks**. All final output
checksums, row counts and document/group mapping counts passed validation.

| Language | New document IDs | New chunks |
|---|---:|---:|
| VI | 37,734 | 95,938 |
| ZH | 131,146 | 174,453 |
| EN | 14 | 14 |

The canonical local package is
`runs/smoke/recovery-finalize-20261029-v2-cpu/artifacts/recovery_release_20261029_v2/`.
The release manifest SHA-256 is
`1e6e2e710e6355d769d8fad7cc57e21c9400d6d029daab91058438d5f3e8f88f`.
This package replaces both earlier small additions and the intermediate bulk pass.

19,364 accepted documents retain `needs_review` status. Their warnings are preserved;
acceptance does not imply complete media, image or Q&A content. 11,438 candidate
IDs remain unresolved; another 12,750 missing-baseline IDs had no source candidate.
196 new document IDs alias existing raw text and need reviewed mapping, not new vectors.

The user subsequently authorized Qwen3-Embedding-4B inference for the additions.
The CPU inventory verified all **2,621,553** old chunk/vector ID pairs: **267,982**
new inputs require inference, **1,035** output rows reuse old vectors, and **1,388**
reuse another new input. Existing corpus and vector parts remain immutable.
The offline RTX Pro 6000 job completed in 3,227.7 seconds (53.8 minutes), including
model loading and compatibility checks. All 270,405 vectors are downloaded in 11
separate new parts with ID sidecars. All checksums, ID order, shapes, finite values
and unit norms passed. The 27 baseline compatibility samples had minimum cosine
0.99977994. No full baseline re-embedding occurred: 267,982 new inputs and 27
compatibility samples account for all 268,009 model forward inputs.

Verified vector package:
`runs/smoke/qwen4b-additions-20261030-v1/artifacts/`.
It contains `incremental_embed_manifest.json` and `vectors/*`.
Validation reports: `artifacts/data_inventory_20261029/final_corpus_validation.json`
and `final_embedding_validation.json`. Checkpoint resume was also tested with a
tiny fixture, including rejection of a corrupt checkpoint. See `RUN_STATE.json`.
No retrieval, leaderboard submission, commit or push is part of this task.

## Retrieval integration (2026-10-30)

The full corpus v2 is now published as a separate private dataset:
`ntklinhfitus/medxir-chunk-corpus-v2` (initial Kaggle version 1).
The original `ntklinhfitus/medxir-chunk-corpus` remains unchanged: its dataset type
rejected API version updates. The user authorized creating the separate dataset.

The new dataset includes all nine byte-identical baseline chunk parts plus:

| Part | Added chunks |
|---|---:|
| `chunks/vi/part-007.parquet` | 95,938 |
| `chunks/en/part-001.parquet` | 14 |
| `chunks/zh/part-001.parquet` | 174,453 |

Total: 12 chunk parts, 2,891,958 chunks. `hash_groups.parquet` includes the novel
groups while preserving all original group rows. The 196 aliases remain in
`metadata/aliases_pending_review.parquet` and are not automatically fanned out.
Baseline and additions manifests are retained under `provenance/`.
Remote verification SUCCESS: all 17 published files match the staged SHA-256
checksums, including every old chunk part. Report:
`runs/smoke/chunk-dataset-verify-20261030-v1/artifacts/published_dataset_validation.json`.

Use this full dataset as the sole chunk source, replacing the old chunk source.
Do not attach the old corpus or the flat additions table alongside it: that would
duplicate chunk IDs. The retrieval generator now discovers and validates the
selected language parts instead of requiring nine parts. The already-computed addition vectors are published in the
private dataset `ntklinhfitus/medxir-vectors-qwen3-4b-additions`, with parts
`vi-part-007`, `en-part-001` and `zh-part-001`. Each vector and ID sidecar row
matches the corresponding chunk part exactly. The three original vector datasets
remain unchanged. All seven published file checksums, all IDs and row order,
finite values and norms passed the remote Kaggle CPU check:
`runs/smoke/vector-additions-verify-20261030-v1/artifacts/published_vectors_validation.json`.
No additional model inference was run during repackaging.

`scripts/build_hybrid_notebook.py` now includes `scripts/hybrid_inputs.py` and
regenerates `src/notebooks/kaggle_hybrid_retrieval.ipynb`. Before model loading or
GPU vector allocation, the CPU preflight rejects duplicate/missing parts, wrong
corpus versions, incompatible vector shapes/contracts, duplicate IDs, and ID/order
mismatches. `LANGS` filters both chunks and vectors consistently. Vector loading
still uses mmap with 32k-row blocks. Ranking switches and their defaults are preserved.
The output `hybrid_manifest.json` records corpus/vector manifest hashes, part row
counts, selected languages, and BM25 coverage.

`configs/kaggle/hybrid_retrieval_rtx6000.yaml` replaces the old chunk source with
the full v2 dataset and adds the new vector dataset. Keep the three existing
baseline vector sources. Use either their kernel outputs or the corresponding
`boinhbo/medxir-vectors-qwen3-4b-s0/s1/s2` datasets, never both. The config still
runs as `boinhbo`; share the two private `ntklinhfitus` datasets with that account
before running it. Dataset access permissions were not changed by this task.

The existing precomputed BM25 candidates still cover corpus v1. The notebook
logs that limitation; new chunks participate through dense search until BM25 is
rebuilt separately. BM25 inputs are unnecessary when `LANGS` excludes VI, and an
inactive BM25 setting no longer changes dense-only scoring mode.

Validation: 19 targeted unit tests passed. A Kaggle CPU run used the exact copied
input-validation helper with all 12 real parts and verified **2,891,958** unique,
aligned chunk/vector rows: VI 2,224,539; EN 250,501; ZH 416,918. Report:
`runs/smoke/hybrid-input-check-20261030-v1/artifacts/hybrid_input_check.json`.
Full local test discovery also encountered an existing Windows import failure:
`scripts/checkpoint_handoff.py` imports Unix-only `fcntl`. This integration did
not change that crawler module. No full GPU retrieval or leaderboard submission
was run, and no commit or push was made.

Earlier flat additions tables and mixed-language embedding kernel outputs remain
historical release artifacts. The updated retrieval loader expects the repacked
language-part datasets above; do not also attach the flat/mixed outputs or earlier
244-chunk, 14,115-chunk or intermediate 236,073-chunk packages. Review the 196 aliases
before adding fanout. Query vectors must retain the same Qwen4B query contract.

## Baseline and scope

The baseline is corpus v1 built at `2026-10-21T15:56:12+00:00`, with 1,027,516
document IDs. Its existing chunk corpus uses `clean-v1`, parameters `350/50/40`
and IDs `{raw_text_sha256[:16]}-{order}`. Existing files remain immutable.

The official parquet used for reconciliation has 1,220,598 IDs and SHA-256
`87e44a792d7a99fb0fde5dff7f09f1a38a0a1fdf02ddd1ac93a005736e00b0a7`.
Only matching official IDs and URLs can enter this additions package.

Public HTML comes from `snute/ai_guru-S3`, pinned revision
`0ec31d7eb5cc42fc6961eb9c3edd65ac5b5b761c`. The job downloads one source SQLite
database at a time, verifies its pinned checksum, extracts content offline, and
deletes its temporary database. It does not access blocked websites.

## Corrected VI crawl reconciliation

The local VI shard has 6,149 completed IDs absent from baseline v1:

| Category | IDs |
|---|---:|
| Verified post-baseline articles with text | 4,733 |
| Older nonempty payloads containing only short labels | 82 |
| Errors or records without text | 1,334 |
| Total | 6,149 |

All older label payloads have been retrieved from Drive and checked against their
batch SHA-256 values. A nonempty `text` flag was insufficient to qualify these
82 records as articles. Their HTML is retried; unrecovered labels are excluded.

Final reconciliation covers **4,941 of these 6,149 checkpoint IDs**: all 4,733
valid crawl articles plus **208** error/no-text IDs recovered from other sources.
The other 1,208 IDs, including all 82 labels, remain excluded. These 4,941 IDs
are already included in the 37,734 VI additions; do not add the counts again.
See `artifacts/data_inventory_20261029/final_vi_checkpoint_reconciliation.json`.

The existing small delta combines 4,733 crawl IDs and 116 accepted Long Chau IDs:
4,849 document IDs, 4,677 novel raw-text groups, 172 aliases to existing text and
14,115 new chunks. **The final v2 package includes and replaces this delta and
the earlier 244-chunk Long Chau delta. Never append all three packages.**

## Public-source candidate inventory

85 relevant source databases were audited. Stored HTTP 200 payloads with matching
official ID and URL exists for 180,313 missing-baseline IDs:

| Language | Payload candidates |
|---|---:|
| VI | 42,730 |
| ZH | 137,554 |
| EN | 29 |

The 29 EN payloads are PubMed XML rather than HTML; 14 yielded usable abstracts.

These counts precede parsing, quality checks and text deduplication. They are not
counts of imported articles or chunks. Of the 4,849 verified seed IDs, 4,830 overlap
this inventory. The candidate union is 180,332 IDs; 12,750 missing-baseline IDs have
neither stored HTML in this inventory nor a verified seed payload.

## Final package

The verified canonical output comes from Kaggle kernel
`ntklinhfitus/medxir-recovery-finalize-20261029-v2`:

| File | Meaning |
|---|---|
| `chunk_corpus_additions_20261029_v2.parquet` | One separate chunk file, with the same 12 columns as the baseline |
| `documents_additions_20261029_v2.parquet` | Accepted document IDs, original URLs, raw text and parser metadata |
| `hash_groups_additions_20261029_v2.parquet` | New raw-text groups and their representative/fanout metadata |
| `aliases_to_existing_20261029_v2.parquet` | New IDs whose raw text already exists; mapping requires review |
| `embedding_todo_20261029_v2.parquet` | New chunk IDs and hashes of their actual embedding text |
| `unresolved_recovery_ids_20261029_v2.parquet` | Unique stored-HTML candidate IDs not accepted into the final release |
| `needs_review.jsonl.gz` | Failed attempts for IDs still unresolved; recovered IDs are removed |
| `vi_crawl_ids_not_in_release.json` | Nonempty VI checkpoint records not accepted, including unrecovered labels |
| `release_manifest.json` | Final counts, source/code provenance and SHA-256 for output files |

The initial `medxir-recovery-release-20261029-v2` kernel is an intermediate parent,
not the final package. Finalization removes short seed labels, retries alternate
archived templates/copies, then rebuilds all additions as one consistent file.
It also retains complete short Q&A, retries Q&A rejected solely for length, and
revalidates archived capture hosts instead of masking mismatched Wayback URLs.

## Chunk and embedding contracts

- `documents_additions.text` is raw extracted text. Chunk offsets refer to
  `clean_text(documents_additions.text, lang)` using `scripts/text_clean.py`,
  **not directly to the raw text column**. Each chunk is asserted to equal the
  exact cleaned-text slice `[start:end]`.
- `text_sha256` remains the hash of raw text, matching baseline identity rules.
  `embedding_todo.chunk_text_sha256` hashes the actual chunk payload.
- New chunk prefixes are checked for collisions with all baseline text groups.
  Existing chunk IDs and vectors are not rewritten.
- Aliases to existing text do not need a second embedding of identical input.
  They do need a reviewed document mapping. Do not assume an old safe group stays
  safe after adding unrelated URLs; recalculate `group_type` and `fanout_safe`.
- Accepted documents can retain `needs_review` parser status and warnings in
  `warnings_json`, such as unparsed images or incomplete media. Acceptance is not
  a claim of full semantic or medical-content validation.
- Before embedding, compare each model's existing inventory using its exact
  model revision, instruction, tokenizer/input construction, pooling, dimensions
  and normalization. Reuse compatible identical input; embed only missing input
  into separate new vector parts. Do not mix vectors from different models.

## Reproduction

Generators and CPU configs:

- `scripts/build_source_inventory_notebook.py`
- `scripts/build_recovery_parser_probe.py`
- `scripts/offline_recovery_parser.py`
- `scripts/recovery_release_job.py`
- `scripts/build_recovery_release_notebook.py --finalize`
- `configs/kaggle/recovery_release_20261029_v2_cpu.yaml`
- `configs/kaggle/recovery_finalize_20261029_v2_cpu.yaml`

The tiny offline `scripts/smoke_recovery_finalize.py` fixture checks stub removal,
alternate-snapshot recovery, URL mismatch rejection, stale-rejection cleanup,
alias reuse and isolated chunk output. Small real HTML samples test older/current
Lao Dong templates, Familydoctor and archived Long Chau URLs. Bulk work runs on
Kaggle CPU, never locally.

Detailed inventories and run state are under `artifacts/data_inventory_20261029/`.
The baseline registry SHA-256 is
`a2472d74765e1554b84d9683c526bfef847404b2ac16f6be76bcb69ab79f5377`.
