"""Kaggle CPU: reorder existing vectors into the three corpus-v2 language parts."""
from pathlib import Path
from collections import defaultdict
import hashlib,json,shutil,zipfile
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

KEYS={'vi':'vi-part-007','en':'en-part-001','zh':'zh-part-001'}


def sha(path):
    with Path(path).open('rb') as h:return hashlib.file_digest(h,'sha256').hexdigest()


def stats(vectors):
    lo,hi=2.,0.
    for start in range(0,len(vectors),8192):
        block=np.array(vectors[start:start+8192],dtype=np.float32)
        assert np.isfinite(block).all()
        norms=np.linalg.norm(block,axis=1)
        assert np.max(abs(norms-1))<0.01
        lo=min(lo,float(norms.min()));hi=max(hi,float(norms.max()))
    return dict(nan_rows=0,norm_min=lo,norm_max=hi)


def package(source,corpus,out,spec):
    source,corpus,out=map(Path,(source,corpus,out));(out/'vectors').mkdir(parents=True,exist_ok=True)
    assert sha(source/'incremental_embed_manifest.json')==spec['embedding_manifest_sha256']
    assert sha(corpus/'chunk_manifest.json')==spec['corpus_manifest_sha256']
    parent=json.loads((source/'incremental_embed_manifest.json').read_text())
    assert parent['complete'] and parent['model']==spec['model'] and parent['dim']==4096
    assert parent['max_length']==1024 and parent['output_dtype']=='float16'
    targets={};arrays={};seen={};lookup={}
    for lang,key in KEYS.items():
        path=corpus/f'chunks/{lang}/{key.removeprefix(lang+"-")}.parquet'
        records=pq.read_table(path,columns=['chunk_id','lang']).to_pydict()
        ids=records['chunk_id'];assert set(records['lang'])=={lang} and len(ids)==spec['counts'][lang]
        targets[lang]=ids;seen[lang]=np.zeros(len(ids),dtype=bool)
        arrays[lang]=np.lib.format.open_memmap(out/f'vectors/{key}.npy',mode='w+',dtype=np.float16,shape=(len(ids),4096))
        for pos,cid in enumerate(ids):
            assert cid not in lookup;lookup[cid]=(lang,pos)
    assert len(lookup)==parent['total_rows']==sum(spec['counts'].values())
    copied=0
    for part in parent['parts']:
        for kind in ['vectors','ids']:
            meta=part[kind];path=source/meta['path']
            assert path.stat().st_size==meta['bytes'] and sha(path)==meta['sha256']
        vectors=np.load(source/part['vectors']['path'],mmap_mode='r',allow_pickle=False)
        records=pq.read_table(source/part['ids']['path'],columns=['chunk_id','lang']).to_pylist()
        assert vectors.dtype==np.float16 and vectors.shape==(len(records),4096)
        mappings=defaultdict(lambda:([],[]))
        for row,record in enumerate(records):
            cid=record['chunk_id'];assert cid in lookup
            lang,pos=lookup[cid];assert lang==record['lang'] and not seen[lang][pos]
            seen[lang][pos]=True;mappings[lang][0].append(row);mappings[lang][1].append(pos)
        for lang,(src_rows,dst_rows) in mappings.items():
            block=np.array(vectors[src_rows],copy=True)
            arrays[lang][dst_rows]=block
            assert np.array_equal(arrays[lang][dst_rows].view(np.uint16),block.view(np.uint16)),'Vector bytes changed during reorder'
        copied+=len(records);del vectors
        print(f'Reordered {copied:,}/{len(lookup):,} existing vector rows',flush=True)
    assert all(flags.all() for flags in seen.values())
    parts=[]
    for lang,key in KEYS.items():
        arrays[lang].flush()
        checks=stats(arrays[lang])
        pq.write_table(pa.table({'chunk_id':targets[lang]}),out/f'vectors/{key}-ids.parquet',compression='zstd')
        saved=pq.read_table(out/f'vectors/{key}-ids.parquet')['chunk_id'].to_pylist()
        assert saved==targets[lang]
        parts.append(dict(part=key,lang=lang,chunks=len(saved),**checks))
        arrays[lang]._mmap.close()
        print(f'PASS {key}: {len(saved):,} aligned rows; values unchanged',flush=True)
    report=dict(model=parent['model'],model_revision=parent['model_revision'],dim=4096,dtype=parent['dtype'],
        output_dtype='float16',doc_prefix=parent['doc_prefix'],batch=parent['batch'],max_length=1024,instruction=parent['instruction'],
        pooling=parent['pooling'],padding_side=parent['padding_side'],normalize=parent['normalize'],
        complete=True,parts_done=3,parts_total=3,chunks=copied,nan_rows=0,parts=parts,
        new_model_forward_inputs=0,repacked_without_inference=True,vectors_bitwise_preserved=True,
        all_ids_match_corpus_part_order=True,baseline_modified=False,
        source_embedding_manifest_sha256=spec['embedding_manifest_sha256'],corpus_manifest_sha256=spec['corpus_manifest_sha256'],
        corpus_dataset='ntklinhfitus/medxir-chunk-corpus-v2',corpus_version='v2',corpus_dataset_version=1,
        source_plan_sha256=parent.get('source_plan_sha256'),
        model_package_manifest_sha256=parent.get('model_package_manifest_sha256'),
        source_inference_actions=parent.get('actions'),
        source_forward_inputs_including_compatibility=parent.get('forward_tally_including_compatibility',{}).get('forward_inputs'),
        compatibility_cosine_min=parent.get('compatibility_cosine_min'),
        baseline_chunks_verified=parent.get('baseline_chunks_verified'),
        source_kernel=spec['source_kernel'],
        files={p.relative_to(out).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted((out/'vectors').iterdir())})
    (out/'embed_manifest.json').write_text(json.dumps(report,indent=2))
    return report


def main():
    spec=json.loads(Path('/kaggle/working/vector_repack_spec.json').read_text())
    sources=list(Path('/kaggle/input').rglob('incremental_embed_manifest.json'))
    corpora=list(Path('/kaggle/input').rglob('chunk_manifest.json'))
    assert len(sources)==len(corpora)==1
    out=Path('/kaggle/working/vector_payload')
    report=package(sources[0].parent,corpora[0].parent,out,spec)
    files={p.relative_to(out).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p)) for p in out.rglob('*') if p.is_file()}
    validation=dict(complete=True,model=report['model'],dim=4096,chunks=report['chunks'],parts=3,new_model_forward_inputs=0,
        vectors_bitwise_preserved=True,all_ids_match_corpus_part_order=True,files=files)
    Path('/kaggle/working/vector_repack_validation.json').write_text(json.dumps(validation,indent=2))
    print(json.dumps({k:v for k,v in validation.items() if k!='files'},indent=2))



if __name__=='__main__':main()
