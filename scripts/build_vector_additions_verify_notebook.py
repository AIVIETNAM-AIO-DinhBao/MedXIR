"""Verify the published vector dataset and every ID against chunk corpus v2."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]


def main():
    expected=json.loads((ROOT/'runs/smoke/vector-additions-dataset-20261030-v1/artifacts/vector_repack_validation.json').read_text())
    code='EXPECTED='+repr(expected)+'''\nfrom pathlib import Path
import hashlib,json
import numpy as np
import pyarrow.parquet as pq
roots=list(Path('/kaggle/input').rglob('embed_manifest.json'))
corpora=list(Path('/kaggle/input').rglob('chunk_manifest.json'))
assert len(roots)==len(corpora)==1
root=roots[0].parent;corpus=corpora[0].parent
actual={p.relative_to(root).as_posix():p for p in root.rglob('*') if p.is_file()}
assert set(actual)==set(EXPECTED['files'])
for name,meta in EXPECTED['files'].items():
    with actual[name].open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    assert actual[name].stat().st_size==meta['bytes'] and digest==meta['sha256'],name
manifest=json.loads((root/'embed_manifest.json').read_text())
with (corpus/'chunk_manifest.json').open('rb') as f:corpus_sha=hashlib.file_digest(f,'sha256').hexdigest()
assert corpus_sha==manifest['corpus_manifest_sha256']
assert manifest['complete'] and manifest['chunks']==270405 and manifest['parts_done']==3
assert manifest['new_model_forward_inputs']==0 and manifest['vectors_bitwise_preserved']
counts={};all_ids=set()
for lang,part in [('vi','007'),('en','001'),('zh','001')]:
    key=f'{lang}-part-{part}'
    ids=pq.read_table(root/f'vectors/{key}-ids.parquet',columns=['chunk_id'])['chunk_id'].to_pylist()
    expected=pq.read_table(corpus/f'chunks/{lang}/part-{part}.parquet',columns=['chunk_id'])['chunk_id'].to_pylist()
    assert ids==expected and not all_ids.intersection(ids);all_ids.update(ids)
    v=np.load(root/f'vectors/{key}.npy',mmap_mode='r',allow_pickle=False)
    assert v.dtype==np.float16 and v.shape==(len(ids),2560)
    for start in range(0,len(v),8192):
        b=np.array(v[start:start+8192],dtype=np.float32)
        assert np.isfinite(b).all() and np.max(abs(np.linalg.norm(b,axis=1)-1))<0.01
    counts[lang]=len(ids);del v
assert counts=={'vi':95938,'en':14,'zh':174453} and len(all_ids)==270405
result=dict(complete=True,dataset='ntklinhfitus/medxir-vectors-qwen3-4b-additions',version=1,
    corpus='ntklinhfitus/medxir-chunk-corpus-v2',rows=len(all_ids),rows_by_language=counts,
    all_file_checksums_verified=True,chunk_ids_and_order_match=True,all_vectors_finite_and_unit_norm=True,
    vectors_bitwise_unchanged_from_verified_source=True,new_model_forward_inputs=0)
Path('/kaggle/working/published_vectors_validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
'''
    compile(code,'<verify-published-vectors>','exec')
    notebook=ROOT/'src/notebooks/kaggle_vector_additions_verify.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code',id='verify-published-vectors',metadata={},execution_count=None,outputs=[],source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5),indent=1))
    k=dict(id='ntklinhfitus/medxir-vector-additions-verify-20261030',title='medxir-vector-additions-verify-20261030',
        code_file=notebook.relative_to(ROOT).as_posix(),kernel_type='notebook',enable_gpu=False,enable_internet=False,
        dataset_sources=['ntklinhfitus/medxir-chunk-corpus-v2','ntklinhfitus/medxir-vectors-qwen3-4b-additions'],
        timeout_seconds=1800,poll_interval_seconds=30,fetch_files=['published_vectors_validation.json'],
        required_outputs=['published_vectors_validation.json'],metrics_file='published_vectors_validation.json',
        json_checks=[dict(path='complete',value=True),dict(path='rows',value=270405)])
    (ROOT/'configs/kaggle/vector_additions_verify_20261030_cpu.yaml').write_text(json.dumps(dict(kernel=k),indent=2))
    print(notebook)


if __name__=='__main__':main()
