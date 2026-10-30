"""Light local integrity audit; all full-matrix numerical checks run on Kaggle."""
from pathlib import Path
import hashlib
import json
import time

import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def run():
    release=ROOT/'runs/smoke/recovery-finalize-20261029-v2-cpu/artifacts/recovery_release_20261029_v2'
    plan_path=ROOT/'runs/smoke/qwen4b-delta-plan-20261030-v1/artifacts/qwen4b_additions_plan/embedding_plan_manifest.json'
    out=ROOT/'runs/smoke/qwen4b-additions-20261030-v1/artifacts'
    m=json.loads((out/'incremental_embed_manifest.json').read_text())
    plan=json.loads(plan_path.read_text())
    assert m['complete'] and m['source_plan_sha256']==sha(plan_path)
    assert m['source_release_manifest_sha256']==sha(release/'release_manifest.json')
    assert m['model_revision']==plan['model_revision'] and not m['baseline_modified']
    assert '6000' in m['gpu'] and m['dim']==2560 and m['compatibility_cosine_min']>=0.999
    assert m['completed_rows']==m['total_rows']==plan['output_chunks']
    assert m['parts_done']==m['parts_total']==len(m['parts']) and m['nan_rows']==0
    actual=[];source_rows=[];sampled=0;actions={}
    for i,part in enumerate(m['parts']):
        assert part['part']==i and part['start_row']==len(actual) and part['nan_rows']==0
        assert abs(part['norm_min']-1)<0.01 and abs(part['norm_max']-1)<0.01
        for key in ('vectors','ids'):
            item=part[key];p=out/item['path']
            assert p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],p
        vectors=np.load(out/part['vectors']['path'],mmap_mode='r',allow_pickle=False)
        assert vectors.shape==(part['rows'],2560) and vectors.dtype==np.float16
        positions=np.unique(np.linspace(0,len(vectors)-1,min(9,len(vectors)),dtype=int))
        sample=np.array(vectors[positions],dtype=np.float32)
        assert np.isfinite(sample).all() and np.max(abs(np.linalg.norm(sample,axis=1)-1))<0.01
        sampled+=len(positions);del vectors
        ids=pq.read_table(out/part['ids']['path'],columns=['chunk_id','source_chunk_row']).to_pydict()
        assert len(ids['chunk_id'])==part['rows']
        actual.extend(ids['chunk_id']);source_rows.extend(ids['source_chunk_row'])
        for key,n in part['actions'].items():actions[key]=actions.get(key,0)+n
    assert len(set(actual))==len(actual)==m['total_rows'] and actions==plan['actions']
    expected=pq.read_table(release/'chunk_corpus_additions_20261029_v2.parquet',columns=['chunk_id']).column(0).to_pylist()
    assert actual==[expected[i] for i in source_rows]
    assert len(expected)-len(actual)==plan['already_embedded_chunk_ids']
    report=dict(status='VERIFIED',new_vector_rows=len(actual),new_inputs_inferred=actions['infer'],
        copied_old_vector_rows=actions['copy_old'],copied_new_vector_rows=actions['copy_new'],
        baseline_chunks_verified=plan['old_chunks_verified'],baseline_modified=False,
        checksum_and_id_order_verified=True,all_rows_numerically_checked_on_kaggle=True,
        local_sample_rows=sampled,gpu=m['gpu'],compatibility_cosine_min=m['compatibility_cosine_min'])
    destination=ROOT/'artifacts/data_inventory_20261029/final_embedding_validation.json'
    destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
    state_path=destination.parent/'RUN_STATE.json'
    state=json.loads(state_path.read_text(encoding='utf-8'))
    state.update(phase='COMPLETE',embedding_status='VERIFIED',embedding_validation=str(destination.relative_to(ROOT)),
        embedding_manifest=str((out/'incremental_embed_manifest.json').relative_to(ROOT)),
        embedding_run='qwen4b-additions-20261030-v1',new_vector_rows=len(actual),stop_after='Incremental Qwen4B embedding verified; no retrieval/submission/commit/push')
    state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--wait',action='store_true');args=parser.parse_args()
    if args.wait:
        metadata=ROOT/'runs/smoke/qwen4b-additions-20261030-v1/metadata.json'
        deadline=time.monotonic()+8*3600
        while time.monotonic()<deadline:
            try:m=json.loads(metadata.read_text(encoding='utf-8'))
            except (OSError,json.JSONDecodeError):time.sleep(5);continue
            if m['status']=='SUCCESS':break
            if m['status'] in ['ERROR','TIMEOUT','MISSING_OUTPUT','SUBMIT_ERROR']:
                raise RuntimeError('GPU runner needs intervention: '+json.dumps(m))
            time.sleep(10)
        else:raise TimeoutError('Embedding runner did not complete within 8h')
    run()
