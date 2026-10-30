"""Kaggle CPU: execute actual notebook load/search/fusion cells with tiny real Torch matrices."""
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from hybrid_inputs import EMBEDDING_SPECS, embedding_weights, group_embedding_vectors, validate_hybrid_inputs
from hybrid_retrieval import rrf, model_candidate_lists
from hybrid_embedding_cells import LOAD_VECTORS, SEARCH


def main(fusion_source):
    import torch
    original_empty, original_full = torch.empty, torch.full

    def cpu_empty(*args, **kwargs):
        if kwargs.get('device') == 'cuda': kwargs['device'] = 'cpu'
        return original_empty(*args, **kwargs)

    def cpu_full(*args, **kwargs):
        if kwargs.get('device') == 'cuda': kwargs['device'] = 'cpu'
        return original_full(*args, **kwargs)

    reports = []
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); corpus=root/'corpus'; corpus.mkdir()
        ids=['en0','vi0','vi1','vi2','zh0','zh1']
        spans={'en':(0,1),'vi':(1,4),'zh':(4,6)}
        chunks=[]; all_vectors=[]; original={}
        for lang,(a,b) in spans.items():
            p=corpus/f'chunks/{lang}/part-000.parquet'; p.parent.mkdir(parents=True)
            pq.write_table(pa.table({'chunk_id':ids[a:b],'lang':[lang]*(b-a)}),p);chunks.append(p)
        manifest=corpus/'chunk_manifest.json'
        manifest.write_text(json.dumps(dict(dataset_version='v2',languages={l:dict(chunks=b-a) for l,(a,b) in spans.items()})))
        cosines={'qwen4b':[.95,.85,.75,.65,.55,.45], 'qwen8b':[.45,.95,.55,.85,.65,.75], 'octen8b':[.55,.45,.95,.65,.85,.75]}
        for key,spec in EMBEDDING_SPECS.items():
            folder=root/key; (folder/'vectors').mkdir(parents=True)
            matrix=np.zeros((6,spec['dim']),np.float16)
            matrix[:,0]=cosines[key]; matrix[:,1]=np.sqrt(1-np.array(cosines[key])**2)
            original[key]=matrix.astype(np.float32)
            (folder/'embed_manifest.json').write_text(json.dumps(dict(complete=True,model=spec['model'],dim=spec['dim'],doc_prefix=spec['doc_prefix'],max_length=1024,dtype='torch.bfloat16')))
            for lang,(a,b) in spans.items():
                p=folder/f'vectors/{lang}-part-000.npy'; np.save(p,matrix[a:b]);all_vectors.append(p)
                pq.write_table(pa.table({'chunk_id':ids[a:b]}),p.with_name(p.stem+'-ids.parquet'))

        cases=[('single-4b','qwen4b',False,{},False),('single-qwen8b','qwen8b',False,{},True),
               ('single-octen8b','octen8b',False,{},True),
               ('fusion-three','qwen4b',True,dict(qwen4b=2,qwen8b=3,octen8b=5),True),
               ('fusion-zero-model','qwen4b',True,dict(qwen4b=2,qwen8b=0,octen8b=5),True)]
        for name,selected,fusion,weights,variants_enabled in cases:
            active=embedding_weights(selected,fusion,weights)
            # In the zero-weight case, its files are not even attached.
            available=[p for p in all_vectors if p.parent.parent.name in active]
            grouped=group_embedding_vectors(available,active); paths={}
            for key in active:
                _,paths[key],_=validate_hybrid_inputs(chunks,grouped[key],list(spans),manifest,model_key=key)
            variants=[dict(qi=0,kind='full',text='q')]
            if variants_enabled: variants.append(dict(qi=0,kind='sub',text='sub'))
            qvectors={}
            for key in active:
                q=np.zeros((len(variants),EMBEDDING_SPECS[key]['dim']),np.float32)
                q[0,0]=1
                if variants_enabled:q[1,1]=1
                qvectors[key]=q
            env=dict(np=np,pq=pq,Path=Path,time=time,torch=torch,rrf=rrf,model_candidate_lists=model_candidate_lists,
                note=lambda _:None,TIMINGS={},EMBEDDING_SPECS=EMBEDDING_SPECS,ACTIVE_MODEL_WEIGHTS=active,
                MODEL_VECTOR_PATHS=paths,QUERY_VECTORS=qvectors,VARIANTS=variants,QUERY_TEXT=['q'],
                DENSE_K=20,SUB_K=2,PRF_K=2,PRF_ALPHA=.3,PRF_KEEP=4,USE_PRF=variants_enabled,
                USE_QUERY_SPLIT=variants_enabled,SEARCH_TF32=False,RRF_K=60,W_SUB=.5,W_PRF=.5,
                W_BM25=.3,BM25_ACTIVE=variants_enabled,BM25_IDS=[['en0']] if variants_enabled else [[]],
                USE_RERANK=False,POOL_MAX=600)
            with patch.object(torch.Tensor,'cuda',lambda self,*a,**kw:self),patch.object(torch,'empty',cpu_empty),patch.object(torch,'full',cpu_full),patch.object(torch.cuda,'empty_cache',lambda:None):
                exec(LOAD_VECTORS,env); exec(SEARCH,env); exec(fusion_source,env)
            for key,result in env['MODEL_RESULTS'].items():
                scores=original[key] @ qvectors[key][0]
                rank=np.argsort(-scores)
                assert list(result['full_ids'][0])==[ids[i] for i in rank]
                np.testing.assert_allclose(result['full_scores'][0],scores[rank],atol=1e-6)
                if variants_enabled:
                    subrank=np.argsort(-(original[key] @ qvectors[key][1]))[:2]
                    assert list(result['sub_lists'][0][0])==[ids[i] for i in subrank]
                    feedback=original[key][rank[:2]].mean(axis=0)
                    prf=.7*qvectors[key][0]+.3*feedback;prf/=np.linalg.norm(prf)
                    expected=np.argsort(-(original[key]@prf))[:4]
                    assert list(result['prf_ids'][0])==[ids[i] for i in expected]
            assert env['ONLY_DENSE']==(len(active)==1 and not variants_enabled)
            assert not any('PARTS'==key for key in env), 'GPU parts retained after retrieval'
            reports.append(dict(case=name,active=active,dim={k:EMBEDDING_SPECS[k]['dim'] for k in active},dense_sub_prf_match_numpy=True))
            print('PASS',name,flush=True)
    report=dict(complete=True,cases=reports,real_torch_cpu=True,gpu_used=False)
    Path('/kaggle/working/hybrid_multimodel_smoke.json').write_text(json.dumps(report,indent=2))


if __name__=='__main__':main(Path('/kaggle/working/fusion_cell.py.txt').read_text())
