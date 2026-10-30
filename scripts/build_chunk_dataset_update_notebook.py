"""Generate CPU-only dataset staging; publication is a separate verified action."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]


def main():
    before=json.loads((ROOT/'artifacts/chunk_dataset_update_20261030/before_files.json').read_text())
    release=ROOT/'runs/smoke/recovery-finalize-20261029-v2-cpu/artifacts/recovery_release_20261029_v2/release_manifest.json'
    m=json.loads(release.read_text())
    spec=dict(baseline_manifest=m['baseline_manifest'],release_manifest_sha256=hashlib.sha256(release.read_bytes()).hexdigest(),
        old_chunk_paths=sorted(f['name'] for f in before['datasetFiles'] if f['name'].startswith('chunks/')),old_chunks=2621553)
    source=(ROOT/'scripts/package_chunk_dataset_update.py').read_text(encoding='utf-8')
    code='SPEC='+repr(spec)+'\nSOURCE='+repr(source)+'''\nfrom pathlib import Path
import json,subprocess,sys
p=Path('/kaggle/working/package_chunk_dataset_update.py');p.write_text(SOURCE,encoding='utf-8')
Path('/kaggle/working/chunk_update_spec.json').write_text(json.dumps(SPEC))
subprocess.run([sys.executable,'-u',str(p)],check=True)
'''
    notebook=ROOT/'src/notebooks/kaggle_chunk_dataset_update.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code',id='package-update',metadata={},execution_count=None,outputs=[],source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5),indent=1),encoding='utf-8')
    k=dict(id='ntklinhfitus/medxir-chunk-dataset-update-20261030',title='medxir-chunk-dataset-update-20261030',code_file=notebook.relative_to(ROOT).as_posix(),
        kernel_type='notebook',enable_gpu=False,enable_internet=False,dataset_sources=['ntklinhfitus/medxir-chunk-corpus'],
        kernel_sources=['ntklinhfitus/medxir-recovery-finalize-20261029-v2'],
        kernel_source_expectations=[dict(kernel='ntklinhfitus/medxir-recovery-finalize-20261029-v2',version=1,
            manifest='recovery_release_20261029_v2/release_manifest.json',manifest_sha256=spec['release_manifest_sha256'])],
        timeout_seconds=3600,poll_interval_seconds=30,fetch_files=['chunk_dataset_update_manifest.json','dataset_payload.zip'],
        required_outputs=['chunk_dataset_update_manifest.json','dataset_payload.zip'],metrics_file='chunk_dataset_update_manifest.json',
        json_checks=[dict(path='complete',value=True),dict(path='total_chunks',value=2891958),dict(path='chunk_parts',value=12)])
    (ROOT/'configs/kaggle/chunk_dataset_update_20261030_cpu.yaml').write_text(json.dumps(dict(kernel=k),indent=2))
    compile(code,'<packaging-cell>','exec');print(notebook)


if __name__=='__main__':main()
