"""Generate the CPU-only Qwen4B additions packaging notebook."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]


def main():
    manifest=ROOT/'runs/smoke/qwen4b-additions-20261030-v1/artifacts/incremental_embed_manifest.json'
    corpus_report=json.loads((ROOT/'runs/smoke/chunk-dataset-update-20261030-v1/artifacts/chunk_dataset_update_manifest.json').read_text())
    spec=dict(embedding_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        corpus_manifest_sha256=corpus_report['files']['chunk_manifest.json']['sha256'],counts=dict(vi=95938,en=14,zh=174453))
    source=(ROOT/'scripts/repack_qwen4b_additions.py').read_text(encoding='utf-8')
    code='SPEC='+repr(spec)+'\nSOURCE='+repr(source)+'''\nfrom pathlib import Path
import json,subprocess,sys
p=Path('/kaggle/working/repack_qwen4b_additions.py');p.write_text(SOURCE,encoding='utf-8')
Path('/kaggle/working/vector_repack_spec.json').write_text(json.dumps(SPEC))
subprocess.run([sys.executable,'-u',str(p)],check=True)
'''
    compile(code,'<repack-vectors>','exec')
    notebook=ROOT/'src/notebooks/kaggle_vector_additions_dataset.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code',id='repack-without-inference',metadata={},execution_count=None,outputs=[],source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5),indent=1))
    k=dict(id='ntklinhfitus/medxir-vector-additions-dataset-20261030',title='medxir-vector-additions-dataset-20261030',
        code_file=notebook.relative_to(ROOT).as_posix(),kernel_type='notebook',enable_gpu=False,enable_internet=False,
        dataset_sources=['ntklinhfitus/medxir-chunk-corpus-v2'],kernel_sources=['ntklinhfitus/medxir-qwen4b-additions-20261030'],
        kernel_source_expectations=[dict(kernel='ntklinhfitus/medxir-qwen4b-additions-20261030',version=1,
            manifest='incremental_embed_manifest.json',manifest_sha256=spec['embedding_manifest_sha256'])],
        timeout_seconds=3600,poll_interval_seconds=30,fetch_files=['vector_repack_validation.json','vector_payload.zip'],
        required_outputs=['vector_repack_validation.json','vector_payload.zip'],metrics_file='vector_repack_validation.json',
        json_checks=[dict(path='complete',value=True),dict(path='chunks',value=270405),dict(path='new_model_forward_inputs',value=0)])
    (ROOT/'configs/kaggle/vector_additions_dataset_20261030_cpu.yaml').write_text(json.dumps(dict(kernel=k),indent=2))
    print(notebook)


if __name__=='__main__':main()
