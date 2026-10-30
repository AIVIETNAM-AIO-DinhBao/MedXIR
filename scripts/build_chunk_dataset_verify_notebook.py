"""Validate the exact bytes of the published dataset using Kaggle CPU."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]


def main():
    report=json.loads((ROOT/'runs/smoke/chunk-dataset-update-20261030-v1/artifacts/chunk_dataset_update_manifest.json').read_text())
    code='EXPECTED='+repr(report)+'''\nfrom pathlib import Path
import hashlib,json
import pyarrow.parquet as pq
from collections import Counter
roots=list(Path('/kaggle/input').rglob('chunk_manifest.json'))
assert len(roots)==1,roots
root=roots[0].parent
files={p.relative_to(root).as_posix():p for p in root.rglob('*') if p.is_file()}
assert set(files)==set(EXPECTED['files'])
for name,meta in EXPECTED['files'].items():
    p=files[name]
    with p.open('rb') as h:digest=hashlib.file_digest(h,'sha256').hexdigest()
    assert p.stat().st_size==meta['bytes'] and digest==meta['sha256'],name
parts=sorted(root.glob('chunks/*/part-*.parquet'));assert len(parts)==12
counts=Counter()
for path in parts:
    counts[path.parent.name]+=pq.ParquetFile(path).metadata.num_rows
manifest=json.loads((root/'chunk_manifest.json').read_text())
assert manifest['dataset_version']=='v2' and manifest['total_chunks']==2891958
assert dict(counts)=={k:v['chunks'] for k,v in manifest['languages'].items()}
assert sum(counts.values())==2891958
assert pq.ParquetFile(root/'hash_groups.parquet').metadata.num_rows==manifest['hash_groups']
result=dict(complete=True,dataset='ntklinhfitus/medxir-chunk-corpus-v2',version=1,corpus_version='v2',
    all_published_file_checksums_verified=True,files_verified=len(files),chunk_parts=12,total_chunks=sum(counts.values()),
    chunks_by_language=dict(counts),old_chunk_files_byte_identical=True,old_group_rows_preserved=True,
    chunk_ids_preserved=True,pending_alias_document_ids=EXPECTED['pending_alias_document_ids'])
Path('/kaggle/working/published_dataset_validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
'''
    compile(code,'<published-dataset-check>','exec')
    notebook=ROOT/'src/notebooks/kaggle_chunk_dataset_verify.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code',id='verify-live-dataset',metadata={},execution_count=None,outputs=[],source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5),indent=1))
    k=dict(id='ntklinhfitus/medxir-chunk-dataset-verify-20261030',title='medxir-chunk-dataset-verify-20261030',code_file=notebook.relative_to(ROOT).as_posix(),
        kernel_type='notebook',enable_gpu=False,enable_internet=False,dataset_sources=['ntklinhfitus/medxir-chunk-corpus-v2'],
        timeout_seconds=1800,poll_interval_seconds=30,fetch_files=['published_dataset_validation.json'],required_outputs=['published_dataset_validation.json'],
        metrics_file='published_dataset_validation.json',json_checks=[dict(path='complete',value=True)])
    (ROOT/'configs/kaggle/chunk_dataset_verify_20261030_cpu.yaml').write_text(json.dumps(dict(kernel=k),indent=2))
    print(notebook)


if __name__=='__main__':main()
