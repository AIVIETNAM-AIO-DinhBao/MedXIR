"""Generate the CPU-only integration check for the exact notebook model-search cells."""
from pathlib import Path
import hashlib
import json
import build_hybrid_notebook as hybrid

ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    files={name:(ROOT/'scripts'/name).read_text(encoding='utf-8') for name in
           ['hybrid_inputs.py','hybrid_retrieval.py','hybrid_embedding_cells.py','smoke_hybrid_multimodel.py']}
    files['fusion_cell.py.txt']=hybrid.FUSE
    code='FILES='+repr(files)+'''\nfrom pathlib import Path
import sys,subprocess
for name,source in FILES.items():Path('/kaggle/working',name).write_text(source,encoding='utf-8')
subprocess.run([sys.executable,'-u','/kaggle/working/smoke_hybrid_multimodel.py'],check=True)
'''
    compile(code,'<multi-model-smoke>','exec')
    notebook=ROOT/'src/notebooks/kaggle_hybrid_multimodel_smoke.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code',id='multi-model-cpu-check',metadata={},execution_count=None,outputs=[],source=code.splitlines(True))],metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5),indent=1),encoding='utf-8')
    slug='medxir-hybrid-multimodel-smoke-20261030'
    config=dict(id='ntklinhfitus/'+slug,title=slug,code_file=notebook.relative_to(ROOT).as_posix(),kernel_type='notebook',
        immutable_kernel=True,enable_gpu=False,enable_internet=False,timeout_seconds=1800,poll_interval_seconds=30,
        fetch_files=['hybrid_multimodel_smoke.json'],required_outputs=['hybrid_multimodel_smoke.json'],
        metrics_file='hybrid_multimodel_smoke.json',json_checks=[dict(path='complete',value=True)])
    (ROOT/'configs/kaggle/hybrid_multimodel_smoke_20261030_cpu.yaml').write_text(json.dumps(dict(kernel=config),indent=2),encoding='utf-8')
    print(notebook)
