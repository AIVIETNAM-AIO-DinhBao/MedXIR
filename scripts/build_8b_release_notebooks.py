"""Build CPU repack/remote-verification notebooks for separate 8B additions datasets."""
import argparse
import hashlib
import json
from pathlib import Path
from build_8b_delta_notebooks import FAMILIES

ROOT = Path(__file__).resolve().parents[1]


def build(family, stage):
    model = FAMILIES[family][0]
    dataset = 'ntklinhfitus/medxir-vectors-' + ('qwen3-8b' if family == 'qwen8b' else 'octen-8b') + '-additions'
    if stage == 'repack':
        mapping_path = ROOT / 'artifacts/embedding_8b_inventory_20261030/GPU_RUNS.json'
        mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
        run = mapping.get(family, dict(run_id=f'{family}-additions-20261030-v1',
            kernel=f'ntklinhfitus/medxir-{family}-additions-20261030', version=1))
        parent = ROOT / 'runs/smoke' / run['run_id'] / 'artifacts/incremental_embed_manifest.json'
        m = json.loads(parent.read_text())
        assert m['complete'] and m['model'] == model and m['dim'] == 4096 and m['completed_rows'] == 270405
        assert m['compatibility_cosine_min'] >= .999 and not m['baseline_modified']
        source_kernel = run['kernel']
        spec = dict(model=model, source_kernel=source_kernel,
                    embedding_manifest_sha256=hashlib.sha256(parent.read_bytes()).hexdigest(),
                    corpus_manifest_sha256='fdc057d88448532cd9565209135c65334f640f27ab6b45324cd76368b00bb1a7',
                    counts=dict(vi=95938, en=14, zh=174453))
        source = (ROOT / 'scripts/repack_8b_additions.py').read_text(encoding='utf-8')
        code = 'SPEC=' + repr(spec) + '\nSOURCE=' + repr(source) + '''
from pathlib import Path
import json, subprocess, sys
p=Path('/kaggle/working/repack_8b_additions.py'); p.write_text(SOURCE, encoding='utf-8')
Path('/kaggle/working/vector_repack_spec.json').write_text(json.dumps(SPEC))
subprocess.run([sys.executable, '-u', str(p)], check=True)
'''
        sources = [source_kernel]
        expected = [dict(kernel=source_kernel, version=run['version'], manifest='incremental_embed_manifest.json', manifest_sha256=spec['embedding_manifest_sha256'])]
        datasets = ['ntklinhfitus/medxir-chunk-corpus-v2']
        output = 'vector_repack_validation.json'
    else:
        report = ROOT / f'runs/smoke/{family}-repack-20261030-v1/artifacts/vector_repack_validation.json'
        expected_report = json.loads(report.read_text())
        assert expected_report['complete'] and expected_report['model'] == model and len(expected_report['files']) == 7
        code = 'EXPECTED=' + repr(expected_report) + '\nDATASET=' + repr(dataset) + '''
from pathlib import Path
import hashlib,json
import numpy as np
import pyarrow.parquet as pq
roots=list(Path('/kaggle/input').rglob('embed_manifest.json'))
corpora=list(Path('/kaggle/input').rglob('chunk_manifest.json'))
assert len(roots)==len(corpora)==1
root=roots[0].parent; corpus=corpora[0].parent
actual={p.relative_to(root).as_posix():p for p in root.rglob('*') if p.is_file()}
assert set(actual)==set(EXPECTED['files'])
for name,meta in EXPECTED['files'].items():
    with actual[name].open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    assert actual[name].stat().st_size==meta['bytes'] and digest==meta['sha256'],name
m=json.loads(roots[0].read_text())
with corpora[0].open('rb') as f: corpus_sha=hashlib.file_digest(f,'sha256').hexdigest()
assert corpus_sha==m['corpus_manifest_sha256']
assert m['complete'] and m['chunks']==270405 and m['dim']==4096 and m['model']==EXPECTED['model']
assert m['new_model_forward_inputs']==0 and m['vectors_bitwise_preserved']
assert {'Qwen/Qwen3-Embedding-8B':'','Octen/Octen-Embedding-8B':' '}[m['model']]==m['doc_prefix']
counts={}; all_ids=set()
for lang,part in [('vi','007'),('en','001'),('zh','001')]:
    key=f'{lang}-part-{part}'
    ids=pq.read_table(root/f'vectors/{key}-ids.parquet',columns=['chunk_id'])['chunk_id'].to_pylist()
    expected_ids=pq.read_table(corpus/f'chunks/{lang}/part-{part}.parquet',columns=['chunk_id'])['chunk_id'].to_pylist()
    assert ids==expected_ids and len(set(ids))==len(ids) and not all_ids.intersection(ids)
    all_ids.update(ids)
    v=np.load(root/f'vectors/{key}.npy',mmap_mode='r',allow_pickle=False)
    assert v.dtype==np.float16 and v.shape==(len(ids),4096)
    for start in range(0,len(v),8192):
        block=np.array(v[start:start+8192],dtype=np.float32)
        assert np.isfinite(block).all() and np.max(abs(np.linalg.norm(block,axis=1)-1))<.01
    counts[lang]=len(ids); del v
assert counts==dict(vi=95938,en=14,zh=174453) and len(all_ids)==270405
report=dict(complete=True,dataset=DATASET,version=1,model=m['model'],dim=4096,
    rows=len(all_ids),rows_by_language=counts,all_file_checksums_verified=True,
    chunk_ids_and_order_match=True,all_vectors_finite_and_unit_norm=True,
    vectors_bitwise_unchanged_from_verified_source=True,baseline_modified=False)
Path('/kaggle/working/published_vectors_validation.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
'''
        sources, expected = [], []
        datasets = ['ntklinhfitus/medxir-chunk-corpus-v2', dataset]
        output = 'published_vectors_validation.json'
    compile(code, '<8b-release>', 'exec')
    notebook = ROOT / f'src/notebooks/kaggle_{family}_{stage}.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code', id='verified-8b-release', metadata={}, execution_count=None, outputs=[], source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3')), nbformat=4, nbformat_minor=5), indent=1), encoding='utf-8')
    slug = f'medxir-{family}-{stage}-20261030'
    kernel = dict(id='ntklinhfitus/' + slug, title=slug, code_file=notebook.relative_to(ROOT).as_posix(),
        kernel_type='notebook', immutable_kernel=True, enable_gpu=False, enable_internet=False,
        dataset_sources=datasets, kernel_sources=sources, kernel_source_expectations=expected,
        timeout_seconds=3600, poll_interval_seconds=30, fetch_files=[output], required_outputs=[output],
        metrics_file=output, json_checks=[dict(path='complete', value=True)])
    (ROOT / f'configs/kaggle/{family}_{stage}_20261030.yaml').write_text(json.dumps(dict(kernel=kernel), indent=2), encoding='utf-8')
    print(notebook)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', choices=FAMILIES, required=True)
    p.add_argument('--stage', choices=['repack', 'verify'], required=True)
    args = p.parse_args()
    build(args.model, args.stage)
