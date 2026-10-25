"""Generate the two Kaggle notebooks for the multilingual reranker benchmark.

  src/notebooks/kaggle_package_rerankers.ipynb   Kaggle CPU, Internet ON: snapshot four rerankers at pinned
                                                 commits into /kaggle/working/models/<name>/ with MODELS.json
                                                 (commit date, license, size). Save the output as a dataset.
  src/notebooks/kaggle_rerank_benchmark.ipynb    RTX Pro 6000, Internet OFF: score the top-150 dense chunks of
                                                 each query with every reranker. Writes speed, memory, and the
                                                 scores; no labels, so quality is checked on the leaderboard.

    uv run python scripts/build_rerank_notebooks.py
"""
import argparse
import json
from pathlib import Path

from build_chunk_notebook import notebook

PACKAGE = [
    ('md', """
# Package multilingual rerankers for offline Kaggle runs

Run with **Internet ON** on CPU. Writes `/kaggle/working/models/<name>/` and `MODELS.json`.
Each model is pinned to the commit that is current when this runs; MODELS.json keeps the commit date and license.
"""),
    ('code', """
import json
import os
from datetime import datetime

from huggingface_hub import HfApi, snapshot_download

REPOS = [
    'Qwen/Qwen3-Reranker-0.6B',
    'Qwen/Qwen3-Reranker-4B',
    'BAAI/bge-reranker-v2-m3',
    'jinaai/jina-reranker-v3.5',
]
OUT = '/kaggle/working/models'
LIMIT_GB = 20
ALLOW = ['*.json', '*.safetensors', '*.txt', '*.md', 'merges.txt', '*.py']
IGNORE = ['onnx/*', 'openvino/*', 'original/*', '*.gguf', '*.bin', '*.onnx', '*.pt', '*.msgpack', '*.h5']
os.makedirs(OUT, exist_ok=True)

api = HfApi()
manifest = {}
for repo in REPOS:
    info = api.model_info(repo, files_metadata=True)
    license_name = (info.card_data.license if info.card_data else None) or next(
        (t.split(':', 1)[1] for t in info.tags if t.startswith('license:')), None)
    name = repo.split('/')[-1]
    target = os.path.join(OUT, name)
    snapshot_download(repo_id=repo, revision=info.sha, local_dir=target, allow_patterns=ALLOW, ignore_patterns=IGNORE)
    files = []
    for root, dirs, names in os.walk(target):
        dirs[:] = [d for d in dirs if d != '.cache']
        for file_name in names:
            if file_name.startswith('.'):
                continue
            path = os.path.join(root, file_name)
            files.append(dict(path=os.path.relpath(path, target), bytes=os.path.getsize(path)))
    manifest[repo] = dict(folder=f'models/{name}', sha=info.sha, created_at=str(info.created_at),
                          last_modified=str(info.last_modified), license=license_name,
                          size_bytes=sum(f['bytes'] for f in files), files=sorted(files, key=lambda f: f['path']))
    print(repo, info.sha, 'created', info.created_at, 'license', license_name)

total_gb = sum(m['size_bytes'] for m in manifest.values()) / 1e9
doc = dict(downloaded_at=datetime.now().isoformat(timespec='seconds'), total_gb=round(total_gb, 2),
           limit_gb=LIMIT_GB, models=manifest,
           rule='open models <= 15B released before 2026-08-01 (OVERVIEW.md)')
with open(os.path.join(OUT, 'MODELS.json'), 'w', encoding='utf-8') as file:
    json.dump(doc, file, ensure_ascii=False, indent=2)
print(f'total {total_gb:.2f} GB of limit {LIMIT_GB} GB')
assert total_gb < LIMIT_GB
"""),
]

BENCHMARK = [
    ('md', """
# Multilingual reranker benchmark (top-150 dense chunks per query)

Input: dataset `boinhbo/medxir-rerank-candidates` (candidates.parquet) and the packaged models
`boinhbo/medxir-package-rerankers`. No internet. Output: `rerank_benchmark.json`, one `scores_<name>.parquet`
per model (query_id, rank, score).

Set `QUERY_LIMIT` to 100 for a speed and memory check; use 0 for all 1,200 queries.
"""),
    ('code', """
import glob
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

QUERY_LIMIT = int(os.environ.get('QUERY_LIMIT', '100'))
MAX_LEN = 1024
BATCH = 16
OUT = Path('/kaggle/working')

models_json = glob.glob('/kaggle/input/**/MODELS.json', recursive=True)
cands_path = glob.glob('/kaggle/input/**/candidates.parquet', recursive=True)
assert len(models_json) == 1 and len(cands_path) == 1, (models_json, cands_path)
MODEL_ROOT = Path(models_json[0]).parent  # MODELS.json sits in models/ next to the model folders
manifest = json.load(open(models_json[0], encoding='utf-8'))['models']

cands = pd.read_parquet(cands_path[0])
if QUERY_LIMIT:
    keep = sorted(cands['query_id'].unique())[:QUERY_LIMIT]
    cands = cands[cands['query_id'].isin(keep)]
print('rows', len(cands), 'queries', cands['query_id'].nunique(), flush=True)
print('gpu', torch.cuda.get_device_name(0), flush=True)
"""),
    ('code', """
from transformers import AutoModelForSequenceClassification, AutoTokenizer

QWEN_PREFIX = ('<|im_start|>system\\nJudge whether the Document meets the requirements based on the Query and the '
               'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\\n<|im_start|>user\\n')
QWEN_SUFFIX = '<|im_end|>\\n<|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n'
INSTRUCT = 'Given a Vietnamese medical question, judge whether the document contains the answer to the question'


def score_qwen(model_dir, pairs):
    from transformers import AutoModelForCausalLM
    tok = AutoTokenizer.from_pretrained(model_dir, padding_side='left')
    # Qwen3-Reranker is a causal LM: score = P(yes) vs P(no) at the last position (model card).
    model = AutoModelForCausalLM.from_pretrained(model_dir, torch_dtype=torch.bfloat16).cuda().eval()
    yes, no = tok.convert_tokens_to_ids('yes'), tok.convert_tokens_to_ids('no')
    texts = [QWEN_PREFIX + f'<Instruct>: {INSTRUCT}\\n<Query>: {q}\\n<Document>: {d}' + QWEN_SUFFIX for q, d in pairs]
    out = np.empty(len(texts), dtype=np.float32)
    order = np.argsort([len(t) for t in texts])
    for start in range(0, len(texts), BATCH):
        idx = order[start:start + BATCH]
        enc = tok([texts[i] for i in idx], padding=True, truncation=True, max_length=MAX_LEN, return_tensors='pt').to('cuda')
        with torch.no_grad():
            logits = model(**enc).logits[:, -1, :]
        two = torch.stack([logits[:, no], logits[:, yes]], dim=1).float()
        out[idx] = torch.log_softmax(two, dim=1)[:, 1].exp().cpu().numpy()
    return out, model


def score_seqcls(model_dir, pairs):
    tok = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, torch_dtype=torch.bfloat16).cuda().eval()
    out = np.empty(len(pairs), dtype=np.float32)
    order = np.argsort([len(d) for _, d in pairs])
    for start in range(0, len(pairs), BATCH):
        idx = order[start:start + BATCH]
        enc = tok([pairs[i][0] for i in idx], [pairs[i][1] for i in idx], padding=True, truncation=True,
                  max_length=MAX_LEN, return_tensors='pt').to('cuda')
        with torch.no_grad():
            out[idx] = model(**enc).logits.view(-1).float().cpu().numpy()
    return out, model


def score_jina(model_dir, pairs):
    # Listwise: all candidates for one query in one context (model card). Returns per-query scores.
    from transformers import AutoModel
    model = AutoModel.from_pretrained(model_dir, torch_dtype=torch.bfloat16, trust_remote_code=True).cuda().eval()
    out = np.empty(len(pairs), dtype=np.float32)
    by_query = {}
    for i, (q, d) in enumerate(pairs):
        by_query.setdefault(q, []).append(i)
    for q, idx in by_query.items():
        docs = [pairs[i][1] for i in idx]
        results = model.rerank(q, docs)
        for r in results:
            out[idx[r['index']]] = r['relevance_score']
    return out, model


SCORERS = {
    'Qwen3-Reranker-0.6B': score_qwen,
    'Qwen3-Reranker-4B': score_qwen,
    'bge-reranker-v2-m3': score_seqcls,
    'jina-reranker-v3.5': score_jina,
}
"""),
    ('code', """
results = {}
for name, scorer in SCORERS.items():
    entry = dict(name=name)
    try:
        model_dir = str(MODEL_ROOT / name)
        meta = manifest[[r for r in manifest if r.split('/')[-1] == name][0]]
        entry.update(sha=meta['sha'], created_at=meta['created_at'], license=meta['license'])
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        pairs = list(zip(cands['query'], cands['chunk_text']))
        started = time.perf_counter()
        scores, model = scorer(model_dir, pairs)
        elapsed = time.perf_counter() - started
        entry.update(pairs=len(pairs), seconds=round(elapsed, 1), pairs_per_second=round(len(pairs) / elapsed, 1),
                     peak_gpu_gb=round(torch.cuda.max_memory_allocated() / 1e9, 2), status='ok')
        frame = cands[['query_id', 'rank']].copy()
        frame['score'] = scores
        frame.to_parquet(OUT / f'scores_{name}.parquet', index=False)
        del model
    except Exception as error:  # record and continue with the next model
        entry.update(status='failed', error=f'{type(error).__name__}: {str(error)[:300]}')
    results[name] = entry
    print(json.dumps(entry, ensure_ascii=False), flush=True)
    torch.cuda.empty_cache()

json.dump(dict(query_limit=QUERY_LIMIT, rows=len(cands), results=results),
          open(OUT / 'rerank_benchmark.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('done', flush=True)
"""),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out-dir', type=Path, default=Path('src/notebooks'))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, cells in [('kaggle_package_rerankers.ipynb', PACKAGE), ('kaggle_rerank_benchmark.ipynb', BENCHMARK)]:
        path = args.out_dir / name
        path.write_text(json.dumps(notebook(cells), ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print(path, len(cells), 'cells')


if __name__ == '__main__':
    main()
