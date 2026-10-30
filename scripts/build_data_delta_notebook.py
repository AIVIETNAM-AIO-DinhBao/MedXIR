"""Build a CPU notebook for an isolated Long Chau chunk delta, never merging baseline."""
from pathlib import Path
import base64, hashlib, json
ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / 'artifacts/data_delta_20261029'
def code(text):
    return dict(cell_type='code', metadata={}, execution_count=None, outputs=[], source=text.splitlines(True))
def main():
    payload=(BATCH/'documents.jsonl.gz').read_bytes()
    manifest=json.loads((BATCH/'manifest.json').read_text())
    cells=[dict(cell_type='markdown',metadata={},source=['# Long Chau offline: isolated chunk delta\nCPU only. No embedding, baseline overwrite or submission. Alias candidates require review.'])]
    for name in ['text_clean.py','chunking.py']:
        cells.append(code((ROOT/'scripts'/name).read_text(encoding='utf-8').replace("if __name__ == '__main__':", "if False:")))
    config="PAYLOAD_B64 = " + repr(base64.b64encode(payload).decode()) + "\n"
    config+="EXPECTED_PAYLOAD_SHA = " + repr(hashlib.sha256(payload).hexdigest()) + "\n"
    config+="BATCH_MANIFEST = " + repr(manifest) + "\n"
    cells.append(code(config))
    cells.append(code(RUN))
    target=ROOT/'src/notebooks/kaggle_data_delta_20261029.ipynb'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(dict(cells=cells,metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5),ensure_ascii=False,indent=1),encoding='utf-8')
    print(target)
RUN = r"""
import base64, gzip, hashlib, inspect, json
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

payload=base64.b64decode(PAYLOAD_B64)
assert hashlib.sha256(payload).hexdigest()==EXPECTED_PAYLOAD_SHA
records=[json.loads(s) for s in gzip.decompress(payload).decode().splitlines()]
params=inspect.signature(chunk_document).parameters
chunk_params='/'.join(str(params[n].default) for n in ['limit','overlap','minimum'])
assert chunk_params=='350/50/40' and CLEAN_VERSION==BATCH_MANIFEST['clean_version']

# Attach ntklinhfitus/medxir-chunk-corpus; full corpus registry is optional.
inputs=Path('/kaggle/input')
registries=list(inputs.rglob('registry.parquet'))
manifests=list(inputs.rglob('chunk_manifest.json'))
assert len(registries)<=1 and len(manifests)==1,(registries,manifests)
registry_path=registries[0] if registries else None
chunk_manifest_path=manifests[0]
if registry_path is not None:
    assert hashlib.sha256(registry_path.read_bytes()).hexdigest()==BATCH_MANIFEST['baseline_registry_sha256'], 'Baseline changed: rebuild data audit first'
base_manifest=json.loads(chunk_manifest_path.read_text())
assert base_manifest['chunk_params']==chunk_params
assert base_manifest['clean_version']==CLEAN_VERSION
assert base_manifest['dataset_version']==BATCH_MANIFEST['baseline_version']
assert base_manifest['dataset_built_at']==BATCH_MANIFEST['baseline_built_at']
groups_path=chunk_manifest_path.parent/'hash_groups.parquet'
assert groups_path.is_file()
groups=pd.read_parquet(groups_path)
by_sha={r.text_sha256:r for r in groups.itertuples(index=False)}
reg=None
if registry_path is not None:
    registry=pd.read_parquet(registry_path,columns=['doc_id','url','state'])
    reg=registry.set_index('doc_id')
else:
    print('No full registry attached; use packaged ID/URL audit and matching baseline chunk manifest.')

out=Path('/kaggle/working/data_delta_20261029');out.mkdir(exist_ok=True)
new_chunks=[];alias_rows=[];new_groups=[];todo=[];seen_ids=set();seen_hashes=set()
for d in records:
    assert d['doc_id'] not in seen_ids;seen_ids.add(d['doc_id'])
    if reg is not None:
        assert d['doc_id'] in reg.index and reg.loc[d['doc_id'],'url']==d['url']
        assert reg.loc[d['doc_id'],'state']!='in_corpus'
    else:
        assert d['baseline_state']=='pending'
        assert d['url'].startswith('https://nhathuoclongchau.com.vn/')
    sha=hashlib.sha256(d['text'].encode()).hexdigest()
    assert sha==d['text_sha256']
    if sha in by_sha:
        old=by_sha[sha]
        alias_rows.append(dict(doc_id=d['doc_id'],text_sha256=sha,
            baseline_rep_doc_id=int(old.rep_doc_id),baseline_fanout_safe=bool(old.fanout_safe),
            baseline_group_type=old.group_type,decision='manual_mapping_review'))
        continue
    if sha in seen_hashes:
        alias_rows.append(dict(doc_id=d['doc_id'],text_sha256=sha,decision='duplicate_within_delta_review'))
        continue
    seen_hashes.add(sha)
    text=clean_text(d['text'],d['lang'])
    for c in chunk_document(text,d['lang']):
        assert text[c.start:c.end]==c.text
        cid=f'{sha[:16]}-{c.order}'
        row=dict(chunk_id=cid,text_sha256=sha,rep_doc_id=d['doc_id'],lang=d['lang'],
            order=c.order,start=c.start,end=c.end,chunk_text=c.text,n_units=size(c.text,d['lang']),
            title=d['title'],clean_version=CLEAN_VERSION,chunk_params=chunk_params)
        new_chunks.append(row)
        todo.append(dict(chunk_id=cid,text_sha256=sha,
            chunk_text_sha256=hashlib.sha256(c.text.encode()).hexdigest(),
            embedding_state='needs_per_model_vector_inventory'))
    new_groups.append(dict(text_sha256=sha,rep_doc_id=d['doc_id'],doc_ids=[d['doc_id']],
                          n_ids=1,group_type='single',fanout_safe=True))
assert len({r['chunk_id'] for r in new_chunks})==len(new_chunks)
# No mutation of existing hash groups; new IDs on old text are review-only.
for name,rows in [('new_chunks',new_chunks),('new_hash_groups',new_groups),
                  ('alias_candidates',alias_rows),('embedding_todo',todo)]:
    if rows: pq.write_table(pa.Table.from_pylist(rows),out/(name+'.parquet'),compression='zstd')
    (out/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
summary=dict(batch_id=BATCH_MANIFEST['batch_id'],accepted_docs=len(records),
    new_text_groups=len(new_groups),alias_candidates=len(alias_rows),new_chunks=len(new_chunks),
    baseline_manifest=base_manifest,clean_version=CLEAN_VERSION,chunk_params=chunk_params,
    baseline_modified=False,vectors_generated=False,full_registry_rechecked=reg is not None,
    limitations=['Recheck patches newer than corpus v1 before merge.',
        'Alias candidates are not automatically added to fanout.',
        'Each model must verify input/config compatibility before vector reuse.'])
summary['files']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
(out/'delta_chunk_manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ['baseline_manifest','files']},indent=2))
"""
if __name__=='__main__':main()
