"""Offline Kaggle GPU inference for a verified incremental Qwen8B/Octen8B inventory."""
from pathlib import Path
from collections import Counter
import hashlib
import importlib.metadata
import json
import os
import shutil
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

PART_ROWS = 25_000
DIM = 4096


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def atomic_json(path, data):
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temp, path)


def vector_stats(vectors):
    assert vectors.ndim == 2 and vectors.shape[1] == DIM
    assert vectors.dtype == np.float16 and np.isfinite(vectors).all()
    norms = np.linalg.norm(vectors.astype(np.float32), axis=1)
    assert not len(norms) or np.max(np.abs(norms - 1)) < 0.01
    return dict(nan_rows=0, norm_min=float(norms.min()) if len(norms) else None,
                norm_max=float(norms.max()) if len(norms) else None)


def fill_vectors(rows, embed, old_vectors, lookup):
    """All inference/copy dependencies are explicit; no uninitialized canonical rows."""
    result = np.empty((len(rows), DIM), dtype=np.float16)
    ready = np.zeros(len(rows), dtype=bool)
    start = rows[0]['output_row'] if rows else 0
    assert [r['output_row'] for r in rows] == list(range(start, start + len(rows)))
    infer = [i for i, r in enumerate(rows) if r['action'] == 'infer']
    for offset in range(0, len(infer), 4096):
        indices = infer[offset:offset + 4096]
        vectors = embed([rows[i]['chunk_text'] for i in indices])
        assert vectors.shape == (len(indices), DIM)
        vector_stats(vectors)
        result[indices] = vectors
        ready[indices] = True
    for i, row in enumerate(rows):
        if row['action'] == 'copy_old':
            pos = row['old_reuse_row']
            assert 0 <= pos < len(old_vectors)
            result[i] = old_vectors[pos]
            ready[i] = True
        elif row['action'] == 'copy_new':
            prior = row['canonical_output_row']
            assert 0 <= prior < row['output_row']
            if prior >= start:
                assert ready[prior - start]
                result[i] = result[prior - start]
            else:
                result[i] = lookup(prior)
            ready[i] = True
        else:
            assert row['action'] == 'infer'
    assert ready.all()
    vector_stats(result)
    return result


def make_embedder(model_path, contract):
    import torch
    import torch.nn.functional as F
    from transformers import AutoModel, AutoTokenizer

    assert torch.cuda.is_available()
    gpu = torch.cuda.get_device_name(0)
    assert '6000' in gpu, gpu
    torch.set_num_threads(2)
    tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
    tokenizer.padding_side = 'left'
    started = time.monotonic()
    print('Loading pinned', contract['model'], 'on', gpu, flush=True)
    model = AutoModel.from_pretrained(str(model_path), torch_dtype=torch.bfloat16,
        attn_implementation='sdpa', local_files_only=True).cuda().eval()
    assert model.config.hidden_size == DIM
    print(f'Model ready after {time.monotonic() - started:.1f}s', flush=True)
    tally = dict(forward_inputs=0, tokens=0, inputs_at_length_cap=0)

    @torch.inference_mode()
    def embed(texts):
        ids = tokenizer([contract['doc_prefix'] + t for t in texts], truncation=True, max_length=1024)['input_ids']
        order = np.argsort([-len(x) for x in ids], kind='stable')
        output = np.empty((len(texts), DIM), dtype=np.float16)
        for offset in range(0, len(order), 16):
            index = order[offset:offset + 16]
            enc = tokenizer.pad({'input_ids': [ids[i] for i in index]}, return_tensors='pt').to('cuda')
            hidden = model(**enc, use_cache=False).last_hidden_state
            mask = enc['attention_mask']
            if mask[:, -1].sum() == mask.shape[0]:
                pooled = hidden[:, -1]
            else:
                lengths = mask.sum(dim=1) - 1
                pooled = hidden[torch.arange(hidden.shape[0], device=hidden.device), lengths]
            output[index] = F.normalize(pooled.float(), p=2, dim=1).cpu().numpy().astype(np.float16)
            tally['forward_inputs'] += len(index)
            tally['tokens'] += sum(len(ids[i]) for i in index)
            tally['inputs_at_length_cap'] += sum(len(ids[i]) == 1024 for i in index)
            if offset % 800 == 0:
                print(f"Forward inputs incl. compatibility checks: {tally['forward_inputs']:,}", flush=True)
        return output
    return embed, tally, dict(gpu=gpu, torch=torch.__version__, transformers=importlib.metadata.version('transformers'))


def run(spec, input_root='/kaggle/input', output_root='/kaggle/working'):
    started = time.monotonic()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    root = Path(input_root)
    plans = list(root.rglob('embedding_plan_manifest.json'))
    assert len(plans) == 1, plans
    plan_path = plans[0]
    assert sha(plan_path) == spec['embedding_plan_manifest_sha256']
    plan = json.loads(plan_path.read_text())
    assert plan['dim'] == DIM and {'Qwen/Qwen3-Embedding-8B': '', 'Octen/Octen-Embedding-8B': ' '}[plan['model']] == plan['doc_prefix']
    assert plan['max_length'] == 1024 and plan['batch'] == 16
    assert plan['inference_dtype'] == 'torch.bfloat16' and plan['output_dtype'] == 'float16'
    assert plan['instruction'] == '' and plan['pooling'] == 'last_token'
    assert plan['normalize'] and plan['padding_side'] == 'left'
    for name, meta in plan['files'].items():
        path = plan_path.parent / name
        assert path.stat().st_size == meta['bytes'] and sha(path) == meta['sha256'], name
    packages = list(root.rglob('MODELS.json'))
    assert len(packages) == 1, packages
    package_path = packages[0]
    assert sha(package_path) == plan['model_package_manifest_sha256']
    entry = json.loads(package_path.read_text())['models'][plan['model']]
    assert entry['sha'] == plan['model_revision']
    model_path = package_path.parent / Path(entry['folder']).name
    for item in entry['files']:
        path = model_path / item['path']
        assert path.is_file() and path.stat().st_size == item['bytes'], path
    rows = pq.read_table(plan_path.parent / 'embedding_tasks.parquet').to_pylist()
    assert len(rows) == plan['output_chunks']
    assert [r['output_row'] for r in rows] == list(range(len(rows)))
    assert len({r['chunk_id'] for r in rows}) == len(rows)
    assert dict(Counter(r['action'] for r in rows)) == plan['actions']
    for row in rows:
        assert hashlib.sha256((plan['doc_prefix']+row['chunk_text']).encode()).hexdigest() == row['input_sha256']
    old_vectors = np.load(plan_path.parent / 'reused_old_vectors.npy', mmap_mode='r', allow_pickle=False)
    assert old_vectors.shape == (plan['old_reuse_unique_inputs'], DIM)
    vector_stats(old_vectors)
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'vectors').mkdir(exist_ok=True)
    manifest_path = out / 'incremental_embed_manifest.json'
    manifest = dict(model=plan['model'], model_revision=plan['model_revision'], dim=DIM,
        dtype='torch.bfloat16', output_dtype='float16', batch=16, max_length=1024, doc_prefix=plan['doc_prefix'],
        instruction='', pooling='last_token', padding_side='left', normalize=True,
        source_plan_sha256=sha(plan_path), source_release_manifest_sha256=plan['source_release_manifest_sha256'],
        model_package_manifest_sha256=plan['model_package_manifest_sha256'],
        job_code_sha256=sha(Path(__file__)), total_rows=len(rows), parts_total=(len(rows)+PART_ROWS-1)//PART_ROWS,
        part_rows=PART_ROWS, actions=plan['actions'], already_embedded_chunk_ids=plan['already_embedded_chunk_ids'],
        baseline_chunks_verified=plan['old_chunks_verified'], baseline_modified=False,
        full_baseline_reembedding=False, complete=False, parts=[], completed_rows=0,
        parts_done=0, nan_rows=0, resumed_rows=0)
    # A later run may mount an earlier interrupted job. Only verified sealed parts are reused.
    candidates=[]
    for candidate in root.rglob('incremental_embed_manifest.json'):
        previous=json.loads(candidate.read_text())
        if all(previous.get(k)==manifest[k] for k in ('source_plan_sha256','model_revision','job_code_sha256','part_rows')):
            candidates.append((len(previous['parts']),candidate,previous))
    if candidates:
        _, candidate, previous=max(candidates,key=lambda item:item[0])
        for index, part in enumerate(previous['parts']):
            assert part['part']==index and part['start_row']==manifest['completed_rows']
            expected=rows[part['start_row']:part['start_row']+part['rows']]
            assert part['rows']==min(PART_ROWS,len(rows)-part['start_row'])
            for key in ('vectors','ids'):
                item=part[key];source=candidate.parent/item['path'];target=out/item['path']
                assert source.stat().st_size==item['bytes'] and sha(source)==item['sha256']
                shutil.copyfile(source,target)
            ids=pq.read_table(out/part['ids']['path'],columns=['chunk_id']).column(0).to_pylist()
            assert ids==[r['chunk_id'] for r in expected]
            v=np.load(out/part['vectors']['path'],mmap_mode='r',allow_pickle=False)
            assert v.shape==(len(expected),DIM);vector_stats(v);del v
            manifest['parts'].append(part);manifest['completed_rows']+=part['rows']
        manifest['resumed_rows']=manifest['completed_rows'];manifest['parts_done']=len(manifest['parts'])
    atomic_json(manifest_path,manifest)
    embed,tally,hardware=make_embedder(model_path,plan)
    manifest.update(hardware)
    gold=pq.read_table(plan_path.parent/'golden_texts.parquet').to_pylist()
    expected=np.load(plan_path.parent/'golden_vectors.npy',allow_pickle=False).astype(np.float32)
    actual=embed([r['chunk_text'] for r in gold]).astype(np.float32)
    cosine=np.sum(actual*expected,axis=1)/(np.linalg.norm(actual,axis=1)*np.linalg.norm(expected,axis=1))
    manifest['compatibility_check_old_texts']=len(gold)
    manifest['compatibility_cosine_min']=float(cosine.min())
    manifest['compatibility_cosine_mean']=float(cosine.mean())
    assert len(gold)==plan['golden_checks'] and np.isfinite(cosine).all() and cosine.min()>=0.999, cosine
    print(f'PASS: {len(gold)} baseline compatibility checks; minimum cosine {cosine.min():.7f}',flush=True)
    atomic_json(manifest_path,manifest)
    cache={}
    def lookup(output_row):
        part=manifest['parts'][output_row//PART_ROWS]
        key=part['vectors']['path']
        if key not in cache:
            cache.clear()
            cache[key]=np.load(out/key,mmap_mode='r',allow_pickle=False)
        return cache[key][output_row-part['start_row']]
    for index in range(len(manifest['parts']),manifest['parts_total']):
        start=index*PART_ROWS;current=rows[start:start+PART_ROWS]
        part_started=time.monotonic()
        vectors=fill_vectors(current,embed,old_vectors,lookup)
        stem=f'vectors/additions-20261029-v2-part-{index:03d}'
        vp=out/(stem+'.npy');ip=out/(stem+'-ids.parquet')
        temp=vp.with_suffix('.npy.tmp')
        with temp.open('wb') as handle:np.save(handle,vectors,allow_pickle=False)
        os.replace(temp,vp)
        ids=pa.Table.from_pylist([{k:r[k] for k in ('chunk_id','input_sha256','lang','source_chunk_row')} for r in current])
        temp=ip.with_suffix('.parquet.tmp');pq.write_table(ids,temp,compression='zstd');os.replace(temp,ip)
        assert pq.ParquetFile(ip).metadata.num_rows==len(current)
        part=dict(part=index,start_row=start,rows=len(current),actions=dict(Counter(r['action'] for r in current)),
            elapsed_seconds=round(time.monotonic()-part_started,1),**vector_stats(vectors))
        for key,path in [('vectors',vp),('ids',ip)]:
            part[key]=dict(path=path.relative_to(out).as_posix(),bytes=path.stat().st_size,sha256=sha(path))
        manifest['parts'].append(part);manifest['parts_done']=index+1
        manifest['completed_rows']+=len(current)
        manifest['elapsed_seconds']=round(time.monotonic()-started,1)
        manifest['forward_tally_including_compatibility']=dict(tally)
        atomic_json(manifest_path,manifest)
        print(f'SEALED part {index}: {len(current):,} rows; {manifest["completed_rows"]:,}/{len(rows):,}',flush=True)
        del vectors
    assert manifest['completed_rows']==len(rows)
    assert Counter({k:sum(p['actions'].get(k,0) for p in manifest['parts']) for k in plan['actions']})==Counter(plan['actions'])
    manifest['complete']=True
    manifest['elapsed_seconds']=round(time.monotonic()-started,1)
    atomic_json(manifest_path,manifest)
    print('COMPLETE',json.dumps({k:v for k,v in manifest.items() if k!='parts'}),flush=True)


if __name__=='__main__':
    run(json.loads(Path('/kaggle/working/incremental_embed_spec.json').read_text()))
