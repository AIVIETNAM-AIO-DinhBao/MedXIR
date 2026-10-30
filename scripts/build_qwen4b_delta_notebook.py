"""Build the pinned offline RTX job for additions, leaving baseline parts intact."""
from pathlib import Path
import argparse
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume-kernel')
    args=parser.parse_args()
    plan_path=ROOT/'runs/smoke/qwen4b-delta-plan-20261030-v1/artifacts/qwen4b_additions_plan/embedding_plan_manifest.json'
    plan=json.loads(plan_path.read_text())
    digest=hashlib.sha256(plan_path.read_bytes()).hexdigest()
    spec=dict(embedding_plan_manifest_sha256=digest)
    source=(ROOT/'scripts/embed_qwen4b_delta.py').read_text(encoding='utf-8')
    code='SPEC='+repr(spec)+'\nSOURCE='+repr(source)+'''\nfrom pathlib import Path
import json,subprocess,sys
p=Path('/kaggle/working/embed_qwen4b_delta.py');p.write_text(SOURCE,encoding='utf-8')
Path('/kaggle/working/incremental_embed_spec.json').write_text(json.dumps(SPEC))
subprocess.run([sys.executable,'-u',str(p)],check=True)
'''
    nb=dict(cells=[dict(cell_type='code',id='incremental-embedding',metadata={},execution_count=None,outputs=[],source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5)
    notebook=ROOT/'src/notebooks/kaggle_qwen4b_delta.ipynb'
    notebook.write_text(json.dumps(nb,ensure_ascii=False,indent=1),encoding='utf-8')
    files=['incremental_embed_manifest.json']
    for i in range((plan['output_chunks']+24_999)//25_000):
        files.extend([f'vectors/additions-20261029-v2-part-{i:03d}.npy',f'vectors/additions-20261029-v2-part-{i:03d}-ids.parquet'])
    slug='medxir-qwen4b-additions-20261030'+('-r2' if args.resume_kernel else '')
    sources=['ntklinhfitus/medxir-qwen4b-delta-plan-20261030','boinhbo/medxir-package-models']
    if args.resume_kernel:
        assert args.resume_kernel != 'ntklinhfitus/'+slug
        sources.append(args.resume_kernel)
    kernel=dict(id='ntklinhfitus/'+slug,title=slug,code_file=notebook.relative_to(ROOT).as_posix(),kernel_type='notebook',
        enable_gpu=True,enable_internet=False,machine_shape='NvidiaRtxPro6000',expected_hardware_contains='6000',
        competition_sources=['arc-prize-2026-arc-agi-3'],kernel_sources=sources,
        kernel_source_expectations=[
            dict(kernel=sources[0],version=1,manifest='qwen4b_additions_plan/embedding_plan_manifest.json',manifest_sha256=digest),
            dict(kernel=sources[1],version=1,manifest='models/MODELS.json',manifest_sha256=plan['model_package_manifest_sha256'])],
        timeout_seconds=21_600,poll_interval_seconds=60,fetch_files=files,required_outputs=files,
        metrics_file='incremental_embed_manifest.json',json_checks=[dict(path='complete',op='eq',value=True),
            dict(path='total_rows',op='eq',value=plan['output_chunks']),dict(path='nan_rows',op='eq',value=0)])
    # JSON is a YAML subset and avoids an extra dependency in this generator.
    config=ROOT/'configs/kaggle/qwen4b_additions_20261030_rtx6000.yaml'
    config.write_text(json.dumps(dict(kernel=kernel),indent=2),encoding='utf-8')
    print(config)


if __name__=='__main__':main()
