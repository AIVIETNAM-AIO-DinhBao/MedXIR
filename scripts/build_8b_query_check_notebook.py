"""Verify the two existing query outputs on Kaggle CPU without downloading matrices."""
from pathlib import Path
import json
import hashlib
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    query_path = ROOT / 'dataset/query.parquet'
    expected_ids = [int(i) for i in pq.read_table(query_path, columns=['id'])['id'].to_pylist()]
    query_sha = hashlib.sha256(query_path.read_bytes()).hexdigest()
    code = 'EXPECTED_QUERY_IDS=' + repr(expected_ids) + '\nQUERY_SHA=' + repr(query_sha) + '\n'
    code += '''from pathlib import Path
import json, hashlib
import numpy as np
import pyarrow.parquet as pq
root=Path('/kaggle/input')
expected=EXPECTED_QUERY_IDS
assert len(expected)==len(set(expected))==1200
report={}
for family in ['qwen8b','octen8b']:
    candidates=[p for p in root.rglob('query_vectors.npy') if f'medxir-encode-queries-{family}' in p.parts]
    assert len(candidates)==1,candidates
    p=candidates[0]; ids=json.loads((p.parent/'query_ids.json').read_text())
    assert [int(i) for i in ids]==expected
    v=np.load(p,mmap_mode='r',allow_pickle=False)
    assert v.shape==(1200,4096) and v.dtype in (np.float16,np.float32) and np.isfinite(v).all()
    norms=np.linalg.norm(v.astype(np.float32),axis=1); assert np.max(abs(norms-1))<.01
    assert not (p.parent/'retrieval_error.txt').exists()
    with p.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    report[family]=dict(queries=1200,dim=4096,dtype=str(v.dtype),ids_match=True,finite=True,
        norm_min=float(norms.min()),norm_max=float(norms.max()),vector_sha256=digest)
    print('PASS existing queries:',family,report[family])
Path('/kaggle/working/query_outputs_validation.json').write_text(json.dumps(dict(complete=True,models=report,official_query_sha256=QUERY_SHA),indent=2))
'''
    compile(code, '<8b-query-check>', 'exec')
    notebook = ROOT / 'src/notebooks/kaggle_8b_query_check.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code', id='query-output-check', metadata={}, execution_count=None, outputs=[], source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3')), nbformat=4, nbformat_minor=5), indent=1), encoding='utf-8')
    slug = 'medxir-8b-query-check-20261030-r2'
    k = dict(id='ntklinhfitus/' + slug, title=slug, code_file=notebook.relative_to(ROOT).as_posix(),
        kernel_type='notebook', immutable_kernel=True, enable_gpu=False, enable_internet=False,
        kernel_sources=['dinhbaobao/medxir-encode-queries-qwen8b','boinhbo/medxir-encode-queries-octen8b'],
        dataset_sources=[], timeout_seconds=1800, poll_interval_seconds=30,
        fetch_files=['query_outputs_validation.json'], required_outputs=['query_outputs_validation.json'],
        metrics_file='query_outputs_validation.json', json_checks=[dict(path='complete',value=True)])
    (ROOT / 'configs/kaggle/8b_query_check_20261030_cpu.yaml').write_text(json.dumps(dict(kernel=k),indent=2),encoding='utf-8')
    print(notebook)
