"""Kaggle CPU inventory for pinned Qwen8B/Octen8B additions; derived from the verified 4B inventory."""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda:f.read(8*1024*1024),b''):h.update(data)
    return h.hexdigest()


def one(pattern):
    found=list(Path('/kaggle/input').rglob(pattern))
    assert len(found)==1,(pattern,[str(p) for p in found])
    return found[0]


def run(spec):
    started=time.monotonic()
    model, dim, prefix = spec['model'], spec['dim'], spec['doc_prefix']
    assert dim == 4096 and {"Qwen/Qwen3-Embedding-8B": "", "Octen/Octen-Embedding-8B": " "}[model] == prefix
    out=Path('/kaggle/working') / (spec['slug']+'_additions_plan');out.mkdir(exist_ok=True)
    release_path=one('release_manifest.json')
    assert sha(release_path)==spec['release_manifest_sha256']
    release=json.loads(release_path.read_text())
    assert release['parent_release_manifest_sha256'], 'Require finalized corpus'
    assert release['baseline_manifest']==spec['baseline_chunk_manifest']
    source=release_path.parent/'chunk_corpus_additions_20261029_v2.parquet'
    assert sha(source)==release['files'][source.name]['sha256']
    manifests=list(Path('/kaggle/input').rglob('embed_manifest.json'))
    assert {sha(p) for p in manifests}==set(spec['baseline_embed_manifest_sha256'])
    for p in manifests:
        m=json.loads(p.read_text())
        assert m['model']==model and m['dim']==dim and m['doc_prefix']==prefix
        assert m['dtype']=='torch.bfloat16' and m['max_length']==1024 and m['batch']==16
        assert m['complete'] and m['nan_rows']==0 and m['parts_done']==m['parts_total']==3
    rows=pq.read_table(source,columns=['chunk_id','chunk_text','lang','text_sha256']).to_pylist()
    assert len(rows)==release['new_chunks']
    new_ids={}
    wanted=set()
    for i,r in enumerate(rows):
        h=hashlib.sha256((prefix+r['chunk_text']).encode()).hexdigest()
        assert r['chunk_id'] not in new_ids
        r.update(source_chunk_row=i,input_sha256=h)
        new_ids[r['chunk_id']]=h;wanted.add(h)
    old_seen=set();reuse={};already={};parts=[];golden=[];golden_vectors=[];vector_paths={}
    chunks=sorted(Path('/kaggle/input').rglob('chunks/*/part-*.parquet'))
    assert len(chunks)==9
    for path in chunks:
        key=path.parent.name+'-'+path.stem
        vector_path=one('vectors/'+key+'.npy');ids_path=one('vectors/'+key+'-ids.parquet')
        vec=np.load(vector_path,mmap_mode='r',allow_pickle=False)
        ids=pq.read_table(ids_path,columns=['chunk_id']).column(0).to_pylist()
        n=pq.ParquetFile(path).metadata.num_rows
        assert vec.dtype==np.float16 and vec.shape==(n,dim) and len(ids)==n
        vector_paths[key]=vector_path
        sample={0,n//2,n-1};offset=0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=4096,columns=['chunk_id','chunk_text','lang']):
            current=batch.to_pylist()
            assert [r['chunk_id'] for r in current]==ids[offset:offset+len(current)]
            for j,r in enumerate(current):
                cid=r['chunk_id'];pos=offset+j
                assert cid not in old_seen,'Duplicate old vector ID'
                old_seen.add(cid)
                h=hashlib.sha256((prefix+r['chunk_text']).encode()).hexdigest()
                if h in wanted:reuse.setdefault(h,(key,pos,cid))
                if cid in new_ids:
                    assert new_ids[cid]==h,'Existing ID has changed embedding text'
                    already[cid]=(key,pos)
                if pos in sample:
                    v=np.array(vec[pos],copy=True)
                    assert np.isfinite(v).all() and abs(float(np.linalg.norm(v.astype(np.float32)))-1)<0.01
                    golden.append(dict(chunk_id=cid,chunk_text=r['chunk_text'],lang=r['lang'],old_part=key,old_row=pos))
                    golden_vectors.append(v)
            offset+=len(current)
        assert offset==n
        parts.append(dict(part=key,rows=n,ids_sha256=sha(ids_path),vectors_sha256=sha(vector_path)))
        print(f'Verified {key}: {n:,} aligned rows; reusable new inputs so far {len(reuse):,}',flush=True)
        del vec,ids
    assert len(old_seen)==spec['expected_old_chunks']
    needed_hashes={r['input_sha256'] for r in rows if r['chunk_id'] not in already}
    reuse_keys=sorted(set(reuse)&needed_hashes);reuse_index={h:i for i,h in enumerate(reuse_keys)}
    copied=np.lib.format.open_memmap(out/'reused_old_vectors.npy',mode='w+',dtype=np.float16,shape=(len(reuse_keys),dim)) if reuse_keys else np.empty((0,dim),dtype=np.float16)
    by_part=defaultdict(list)
    for h in reuse_keys:by_part[reuse[h][0]].append(h)
    reuse_provenance=[]
    for key,values in by_part.items():
        vec=np.load(vector_paths[key],mmap_mode='r',allow_pickle=False)
        for h in values:
            _,old_row,cid=reuse[h];idx=reuse_index[h];v=np.array(vec[old_row],copy=True)
            assert np.isfinite(v).all() and abs(float(np.linalg.norm(v.astype(np.float32)))-1)<0.01
            copied[idx]=v
            reuse_provenance.append(dict(reuse_row=idx,input_sha256=h,old_chunk_id=cid,old_part=key,old_row=old_row,vector_sha256=hashlib.sha256(v.tobytes()).hexdigest()))
        del vec
    if reuse_keys:copied.flush();del copied
    else:np.save(out/'reused_old_vectors.npy',copied)
    np.save(out/'golden_vectors.npy',np.stack(golden_vectors))
    pq.write_table(pa.Table.from_pylist(golden),out/'golden_texts.parquet',compression='zstd')
    if reuse_provenance:pq.write_table(pa.Table.from_pylist(reuse_provenance),out/'reuse_provenance.parquet',compression='zstd')
    actions=Counter();canonical={};writer=None;buffer=[];count=0
    schema=pa.schema([('output_row',pa.int64()),('source_chunk_row',pa.int64()),('chunk_id',pa.string()),
        ('input_sha256',pa.string()),('chunk_text',pa.string()),('lang',pa.string()),('action',pa.string()),
        ('old_reuse_row',pa.int64()),('canonical_output_row',pa.int64())])
    for r in rows:
        if r['chunk_id'] in already:continue
        h=r['input_sha256'];old=-1;prior=-1
        if h in reuse_index:action='copy_old';old=reuse_index[h]
        elif h in canonical:action='copy_new';prior=canonical[h]
        else:action='infer';canonical[h]=count
        buffer.append(dict(output_row=count,source_chunk_row=r['source_chunk_row'],chunk_id=r['chunk_id'],input_sha256=h,
            chunk_text=r['chunk_text'],lang=r['lang'],action=action,old_reuse_row=old,canonical_output_row=prior))
        actions[action]+=1;count+=1
        if len(buffer)>=4096:
            if writer is None:writer=pq.ParquetWriter(out/'embedding_tasks.parquet',schema,compression='zstd')
            writer.write_table(pa.Table.from_pylist(buffer,schema=schema));buffer=[]
    if buffer:
        if writer is None:writer=pq.ParquetWriter(out/'embedding_tasks.parquet',schema,compression='zstd')
        writer.write_table(pa.Table.from_pylist(buffer,schema=schema))
    if writer:writer.close()
    else:pq.write_table(pa.Table.from_pylist([],schema=schema),out/'embedding_tasks.parquet')
    report=dict(model=model,model_revision=spec['model_revision'],dim=dim,doc_prefix=prefix,
        inference_dtype='torch.bfloat16',output_dtype='float16',max_length=1024,batch=16,
        instruction='',padding_side='left',pooling='last_token',normalize=True,
        source_release_manifest_sha256=spec['release_manifest_sha256'],model_package_manifest_sha256=spec['model_package_manifest_sha256'],
        source_chunks=len(rows),output_chunks=count,already_embedded_chunk_ids=len(already),old_chunks_verified=len(old_seen),
        actions=dict(actions),old_reuse_unique_inputs=len(reuse_keys),golden_checks=len(golden),old_parts=parts,
        baseline_modified=False,embeddings_generated=False,elapsed_seconds=round(time.monotonic()-started,1))
    report['files']={p.name:dict(bytes=p.stat().st_size,sha256=sha(p)) for p in out.iterdir() if p.is_file() and p.name!='embedding_plan_manifest.json'}
    (out/'embedding_plan_manifest.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ['files','old_parts']},indent=2))


if __name__=='__main__':run(json.loads(Path('/kaggle/working/embedding_spec.json').read_text()))
