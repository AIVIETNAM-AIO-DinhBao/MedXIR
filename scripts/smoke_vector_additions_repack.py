"""Tiny reordered/multilingual fixture; no model or real vector corpus loaded locally."""
from pathlib import Path
import tempfile,json
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from repack_qwen4b_additions import package,sha,KEYS


def main():
    with tempfile.TemporaryDirectory(prefix='vector-repack-fixture-') as temp:
        root=Path(temp);src=root/'source';corpus=root/'corpus';(src/'vectors').mkdir(parents=True);corpus.mkdir()
        ids=['v0','z0','e0','v1','z1','z2'];langs=['vi','zh','en','vi','zh','zh']
        vectors=np.zeros((6,2560),np.float16)
        for i in range(6):vectors[i,i]=1
        vectors[0,11]=np.float16(-0.)
        parent=dict(model='Qwen/Qwen3-Embedding-4B',model_revision='fixture',dim=2560,max_length=1024,
            output_dtype='float16',dtype='torch.bfloat16',complete=True,total_rows=6,batch=8,instruction='',
            pooling='last_token',padding_side='left',normalize=True,parts=[])
        for part in range(2):
            start=part*3;vp=src/f'vectors/mixed-{part}.npy';ip=src/f'vectors/mixed-{part}-ids.parquet'
            np.save(vp,vectors[start:start+3]);pq.write_table(pa.table({'chunk_id':ids[start:start+3],'lang':langs[start:start+3]}),ip)
            parent['parts'].append({k:dict(path=p.relative_to(src).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for k,p in [('vectors',vp),('ids',ip)]})
        (src/'incremental_embed_manifest.json').write_text(json.dumps(parent))
        desired={'vi':['v1','v0'],'en':['e0'],'zh':['z2','z0','z1']}
        for lang,key in KEYS.items():
            folder=corpus/'chunks'/lang;folder.mkdir(parents=True)
            pq.write_table(pa.table({'chunk_id':desired[lang],'lang':[lang]*len(desired[lang])}),folder/(key.removeprefix(lang+'-')+'.parquet'))
        (corpus/'chunk_manifest.json').write_text(json.dumps(dict(dataset_version='v2')))
        spec=dict(embedding_manifest_sha256=sha(src/'incremental_embed_manifest.json'),corpus_manifest_sha256=sha(corpus/'chunk_manifest.json'),counts={l:len(v) for l,v in desired.items()})
        out=root/'out';report=package(src,corpus,out,spec)
        assert report['complete'] and report['chunks']==6 and report['new_model_forward_inputs']==0
        for lang,key in KEYS.items():
            v=np.load(out/f'vectors/{key}.npy');saved=pq.read_table(out/f'vectors/{key}-ids.parquet')['chunk_id'].to_pylist()
            assert saved==desired[lang]
            expected=vectors[[ids.index(cid) for cid in desired[lang]]]
            assert np.array_equal(v.view(np.uint16),expected.view(np.uint16))
        bad=corpus/'chunks/vi/part-007.parquet'
        pq.write_table(pa.table({'chunk_id':['unknown','v0'],'lang':['vi','vi']}),bad)
        try:package(src,corpus,root/'bad',spec)
        except AssertionError:pass
        else:raise AssertionError('Mismatched chunk IDs were accepted')
        print('PASS: language split, nontrivial row reorder, exact sidecar alignment, bitwise preservation including signed zero, mismatch rejected; no inference')


if __name__=='__main__':main()
