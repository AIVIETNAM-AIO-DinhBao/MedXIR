"""Build the Kaggle notebook that searches uploaded FAISS indexes and writes a submission."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src/notebooks/kaggle_retrieve_qwen3_4b.ipynb"

CELLS = [
    ("markdown", """# MedXIR retrieval baseline: Qwen3-Embedding-4B + uploaded FAISS indexes

Reads the existing FP32 `IndexFlatIP` files from the attached Kaggle dataset. It embeds the
1,200 query rows with the packaged model, reads each uploaded index part with the attached
offline FAISS runtime, searches its vectors exactly on the GPU, joins candidates back to
canonical chunk text and duplicate-document
groups, and writes a submission JSON plus a one-file ZIP under `/kaggle/working`.

Run with Internet OFF on RTX Pro 6000. Inputs: the packaged model kernel, chunk kernel,
`medxir-faiss-qwen3-4b-fp32`, `medxir-retrieval-inputs`, and `medxir-retrieval-runtime`.
"""),
    ("code", """import glob
import json
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import torch

MODEL_DIR = 'Qwen3-Embedding-4B'
TASK = 'Given a web search query, retrieve relevant passages that answer the query'
MAX_LENGTH = 1024
EMBED_BATCH = 8
TOP_CHUNKS = 20
SEARCH_K_PER_PART = TOP_CHUNKS
OUT = Path('/kaggle/working')
OUT.mkdir(parents=True, exist_ok=True)
T0 = time.perf_counter()

def one_match(pattern, description):
    hits = sorted(glob.glob(pattern, recursive=True))
    assert len(hits) == 1, f'{description}: expected one match, found {hits[:10]}'
    return Path(hits[0])

QUERY_PATH = one_match('/kaggle/input/**/query.parquet', 'query.parquet')
CORPUS_PATH = one_match('/kaggle/input/**/links_corpus_vi_en_zh.parquet', 'official corpus IDs')
HASH_PATH = one_match('/kaggle/input/**/hash_groups.parquet', 'hash_groups.parquet')
INDEX_MANIFEST_PATH = one_match('/kaggle/input/**/index_manifest.json', 'FAISS index manifest')
MODEL_CONFIG_PATH = one_match(f'/kaggle/input/**/{MODEL_DIR}/config.json', 'packaged Qwen model')
MODEL_PATH = MODEL_CONFIG_PATH.parent
print('query:', QUERY_PATH, '| index manifest:', INDEX_MANIFEST_PATH, '| model:', MODEL_PATH)

gpu_probe = subprocess.run(
    ['nvidia-smi', '--query-gpu=index,name,memory.total,driver_version', '--format=csv'],
    capture_output=True, text=True, check=True,
)
print(gpu_probe.stdout)
gpu_name = torch.cuda.get_device_name(0)
assert '6000' in gpu_name, f'Expected RTX Pro 6000, got {gpu_name!r}'
print('GPU:', gpu_name, '| VRAM GiB:', round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1))
"""),
    ("markdown", "## 1. Encode query vectors with the same instruction and pooling"),
    ("code", """from transformers import AutoModel, AutoTokenizer
import torch.nn.functional as F

os.environ['HF_HUB_OFFLINE'] = '1'
tok = AutoTokenizer.from_pretrained(str(MODEL_PATH))
tok.padding_side = 'left'
dtype = torch.bfloat16 if torch.cuda.get_device_capability(0)[0] >= 8 else torch.float16
model = AutoModel.from_pretrained(str(MODEL_PATH), torch_dtype=dtype,
                                  attn_implementation='sdpa').cuda().eval()

queries = pd.read_parquet(QUERY_PATH, columns=['id', 'query'])
assert len(queries) == 1200 and queries.id.is_unique
texts = [f'Instruct: {TASK}\\nQuery:{q}' for q in queries['query'].astype(str)]

def last_token_pool(hidden, mask):
    if mask[:, -1].sum() == mask.shape[0]:
        return hidden[:, -1]
    lengths = mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), lengths]

@torch.inference_mode()
def encode(texts):
    tokenized = tok(texts, truncation=True, max_length=MAX_LENGTH)['input_ids']
    order = np.argsort([-len(row) for row in tokenized], kind='stable')
    vectors = np.empty((len(texts), model.config.hidden_size), dtype=np.float32)
    for start in range(0, len(order), EMBED_BATCH):
        ix = order[start:start + EMBED_BATCH]
        batch = tok.pad({'input_ids': [tokenized[i] for i in ix]}, return_tensors='pt').to('cuda')
        hidden = model(input_ids=batch['input_ids'], attention_mask=batch['attention_mask'],
                       use_cache=False).last_hidden_state
        pooled = F.normalize(last_token_pool(hidden, batch['attention_mask']).float(), p=2, dim=1)
        vectors[ix] = pooled.cpu().numpy()
    return vectors

query_vectors = encode(texts)
assert query_vectors.shape == (len(queries), 2560)
assert np.isfinite(query_vectors).all()
del model, tok
torch.cuda.empty_cache()
print('encoded', len(queries), 'queries; shape', query_vectors.shape,
      '| elapsed seconds', round(time.perf_counter() - T0, 1))
"""),
    ("markdown", "## 2. Search each uploaded exact index on the GPU"),
    ("code", """import sys
import zipfile

runtime_modules = sorted(glob.glob('/kaggle/input/**/faiss/__init__.py', recursive=True))
if runtime_modules:
    assert len(runtime_modules) == 1, runtime_modules
    faiss_module_root = Path(runtime_modules[0]).parent.parent
else:
    runtime_archives = sorted(glob.glob('/kaggle/input/**/faiss_runtime.zip', recursive=True))
    assert len(runtime_archives) == 1, f'attach boinhbo/medxir-retrieval-runtime: {runtime_archives}'
    extracted = OUT / 'faiss_runtime'
    extracted.mkdir(exist_ok=True)
    with zipfile.ZipFile(runtime_archives[0]) as archive:
        archive.extractall(extracted)
    faiss_module_root = extracted
sys.path.insert(0, str(faiss_module_root))
import faiss

assert torch.cuda.is_available(), 'CUDA GPU is required'
print('FAISS reader:', faiss.__version__, '| CUDA search:', torch.cuda.get_device_name(0))
torch.backends.cuda.matmul.allow_tf32 = False
manifest = json.loads(INDEX_MANIFEST_PATH.read_text())
assert manifest['index_type'] == 'IndexFlatIP' and manifest['index_dtype'] == 'float32'
assert manifest['dimension'] == query_vectors.shape[1]
parts = manifest['parts']
assert len(parts) == 9 and sum(int(p['rows']) for p in parts) == 2_621_553
per_query = [[] for _ in range(len(queries))]
index_root = INDEX_MANIFEST_PATH.parent
query_gpu = torch.from_numpy(query_vectors).to('cuda')
started = time.perf_counter()

for part in parts:
    cpu_index_path = index_root / part['index']
    row_map_path = index_root / part['row_map']
    cpu_index = faiss.read_index(str(cpu_index_path))
    assert cpu_index.ntotal == int(part['rows']) and cpu_index.d == query_vectors.shape[1]
    vectors_cpu = cpu_index.reconstruct_n(0, cpu_index.ntotal)
    assert vectors_cpu.shape == (int(part['rows']), query_vectors.shape[1])
    vectors_gpu = torch.from_numpy(vectors_cpu).to('cuda')
    del vectors_cpu, cpu_index
    torch.cuda.empty_cache()
    for start in range(0, len(queries), 32):
        scores, rows = torch.topk(
            query_gpu[start:start + 32] @ vectors_gpu.T,
            k=SEARCH_K_PER_PART, dim=1, largest=True, sorted=True,
        )
        scores = scores.cpu().numpy()
        rows = rows.cpu().numpy()
        for offset in range(len(scores)):
            qi = start + offset
            per_query[qi].extend(
                (float(score), part['part'], int(row))
                for score, row in zip(scores[offset], rows[offset])
            )
    del vectors_gpu
    torch.cuda.empty_cache()
    print(part['part'], 'searched', f"{part['rows']:,}", 'rows')

del query_gpu
print('searched all parts in', round(time.perf_counter() - started, 1), 's')
"""),
    ("markdown", "## 3. Join top candidates to the source chunk text and document IDs"),
    ("code", """# Keep the globally highest-scoring candidates, then read only candidate rows from each
# row-map and source chunk table. Flat per-part top-k is sufficient for the global top-k.
for qi in range(len(per_query)):
    per_query[qi].sort(key=lambda item: item[0], reverse=True)
    per_query[qi] = per_query[qi][:TOP_CHUNKS]

part_root = {}
for path in sorted(glob.glob('/kaggle/input/**/chunks/*/part-*.parquet', recursive=True)):
    lang = Path(path).parent.name
    name = Path(path).stem
    key = f'{lang}-{name}'
    assert key not in part_root, f'duplicate chunk source for {key}'
    part_root[key] = Path(path)

candidate_rows = {}
for part in parts:
    wanted = sorted({row for query_rows in per_query for _, key, row in query_rows if key == part['part']})
    if not wanted:
        candidate_rows[part['part']] = {}
        continue
    row_map = pq.read_table(index_root / part['row_map'])
    selected = row_map.take(pa.array(wanted, type=pa.int64()))
    selected_ids = selected.column('chunk_id').to_pylist()
    selected_shas = selected.column('text_sha256').to_pylist()
    selected_reps = selected.column('rep_doc_id').to_pylist()
    selected_orders = selected.column('chunk_order').to_pylist()
    selected_langs = selected.column('lang').to_pylist()
    wanted_ids = set(selected_ids)
    chunk_path = part_root[part['part']]
    table = pq.read_table(chunk_path, columns=['chunk_id', 'chunk_text'])
    mask = pc.is_in(table.column('chunk_id'), value_set=pa.array(wanted_ids, type=pa.string()))
    table = table.filter(mask)
    text_by_id = dict(zip(table.column('chunk_id').to_pylist(), table.column('chunk_text').to_pylist()))
    assert len(text_by_id) == len(wanted_ids), f'missing canonical chunk text in {part["part"]}'
    candidate_rows[part['part']] = {
        row: dict(chunk_id=cid, text_sha256=sha, rep_doc_id=int(rep), lang=lang,
                  chunk_order=int(order), chunk_text=text_by_id[cid])
        for row, cid, sha, rep, lang, order in zip(
            wanted, selected_ids, selected_shas, selected_reps, selected_langs, selected_orders)
    }
    del row_map, selected, table, text_by_id
    print(part['part'], 'joined', len(wanted), 'candidate rows')

groups = pd.read_parquet(HASH_PATH, columns=['text_sha256', 'doc_ids', 'fanout_safe'])
group_lookup = {
    row.text_sha256: (row.doc_ids if row.fanout_safe else None)
    for row in groups.itertuples(index=False)
}
"""),
    ("markdown", "## 4. Write and validate the submission JSON and ZIP"),
    ("code", """submission = []
empty = 0
for qi, query_id in enumerate(queries['id'].tolist()):
    chunks = []
    relevant_docs = []
    seen_docs = set()
    for score, part_key, row in per_query[qi]:
        item = candidate_rows[part_key][row]
        doc_ids = group_lookup.get(item['text_sha256'])
        if not doc_ids:
            doc_ids = [item['rep_doc_id']]
        for doc_id in doc_ids:
            doc_id = int(doc_id)
            if doc_id not in seen_docs:
                seen_docs.add(doc_id)
                relevant_docs.append(doc_id)
            chunks.append(dict(doc_id=doc_id, chunk_text=item['chunk_text'],
                               chunk_order=item['chunk_order']))
    if not chunks:
        empty += 1
    submission.append(dict(id=int(query_id), relevant_docs=relevant_docs, relevant_chunks=chunks))

corpus_ids = set(map(int, pd.read_parquet(CORPUS_PATH, columns=['id'])['id'].tolist()))
query_ids = set(map(int, queries['id'].tolist()))
assert len(submission) == 1200 and {row['id'] for row in submission} == query_ids
assert all(set(row['relevant_docs']).issubset(corpus_ids) for row in submission)
for entry in submission:
    listed = set(entry['relevant_docs'])
    assert all(chunk['doc_id'] in listed and chunk['chunk_text'].strip()
               for chunk in entry['relevant_chunks'])

json_path = OUT / 'medxir-qwen3-4b-submission.json'
zip_path = OUT / 'medxir-qwen3-4b-submission.zip'
json_path.write_text(json.dumps(submission, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')
import zipfile
with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    archive.write(json_path, arcname=json_path.name)
with zipfile.ZipFile(zip_path) as archive:
    names = [name for name in archive.namelist() if not name.endswith('/')]
    assert names == [json_path.name]

result_manifest = dict(
    model='Qwen/Qwen3-Embedding-4B', index_type='IndexFlatIP', index_dtype='float32',
    observed_hardware=gpu_name,
    query_count=len(queries), top_chunks=TOP_CHUNKS, empty_queries=empty,
    output_json=str(json_path), output_zip=str(zip_path),
    json_bytes=json_path.stat().st_size, zip_bytes=zip_path.stat().st_size,
    elapsed_seconds=round(time.perf_counter() - T0, 1),
)
(OUT / 'retrieval_manifest.json').write_text(json.dumps(result_manifest, indent=2) + '\\n')
print(json.dumps(result_manifest, indent=2))
"""),
]


def cell(kind: str, source: str, index: int) -> dict:
    cell_type = "markdown" if kind == "markdown" else "code"
    value = {
        "cell_type": cell_type,
        "id": f"retrieval-{index:02d}",
        "metadata": {},
        "source": source.strip("\n").splitlines(keepends=True),
    }
    if cell_type == "code":
        value.update(execution_count=None, outputs=[])
    return value


notebook = {
    "cells": [cell(kind, source, i) for i, (kind, source) in enumerate(CELLS)],
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10.12"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}
OUT.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"Wrote {OUT.relative_to(ROOT)} ({len(CELLS)} cells)")
