"""CPU-only integration check of the exact helper embedded in hybrid retrieval."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]


def main():
    source=(ROOT/'scripts/hybrid_inputs.py').read_text(encoding='utf-8')
    code=source+'''\nimport glob
roots=list(Path('/kaggle/input').rglob('chunk_manifest.json'))
assert len(roots)==1,roots
chunks=sorted(glob.glob('/kaggle/input/**/chunks/*/part-*.parquet',recursive=True))
vectors=sorted(glob.glob('/kaggle/input/**/vectors/*.npy',recursive=True))
_,_,report=validate_hybrid_inputs(chunks,vectors,['vi','en','zh'],roots[0],expected_version='v2',progress=print)
assert report['part_count']==12 and report['vector_count']==2891958
assert report['rows_by_language']=={'vi':2224539,'en':250501,'zh':416918}
report.update(complete=True,helper_sha256=HELPER_SHA)
Path('/kaggle/working/hybrid_input_check.json').write_text(json.dumps(report,indent=2))
print('PASS: all 12 parts, 2,891,958 IDs aligned; no GPU/model inference')
'''
    code='HELPER_SHA='+repr(hashlib.sha256(source.encode()).hexdigest())+'\n'+code
    compile(code,'<hybrid-input-check>','exec')
    notebook=ROOT/'src/notebooks/kaggle_hybrid_input_check.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code',id='real-data-preflight',metadata={},execution_count=None,outputs=[],source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5),indent=1))
    k=dict(id='ntklinhfitus/medxir-hybrid-input-check-20261030',title='medxir-hybrid-input-check-20261030',
        code_file=notebook.relative_to(ROOT).as_posix(),kernel_type='notebook',enable_gpu=False,enable_internet=False,
        dataset_sources=['ntklinhfitus/medxir-chunk-corpus-v2','ntklinhfitus/medxir-vectors-qwen3-4b-additions']+
            [f'boinhbo/medxir-vectors-qwen3-4b-s{i}' for i in range(3)],
        timeout_seconds=1800,poll_interval_seconds=30,fetch_files=['hybrid_input_check.json'],required_outputs=['hybrid_input_check.json'],
        metrics_file='hybrid_input_check.json',json_checks=[dict(path='complete',value=True),dict(path='vector_count',value=2891958)])
    (ROOT/'configs/kaggle/hybrid_input_check_20261030_cpu.yaml').write_text(json.dumps(dict(kernel=k),indent=2))
    print(notebook)


if __name__=='__main__':main()
