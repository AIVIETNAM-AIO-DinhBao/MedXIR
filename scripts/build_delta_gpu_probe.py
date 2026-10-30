"""Generate a minimal RTX/ARC allocation check; no model inference."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
CODE='''from pathlib import Path
import json,subprocess,torch
assert torch.cuda.is_available(), 'No CUDA GPU allocated'
gpu=torch.cuda.get_device_name(0)
print(subprocess.run(['nvidia-smi','--query-gpu=name,memory.total','--format=csv'],capture_output=True,text=True).stdout)
print('GPU:',gpu)
assert '6000' in gpu, gpu
report=dict(gpu=gpu,torch_version=torch.__version__,vram_bytes=torch.cuda.get_device_properties(0).total_memory)
Path('/kaggle/working/hardware.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
'''
def main():
 nb=dict(cells=[dict(cell_type='code',id='hardware-check',metadata={},execution_count=None,outputs=[],source=CODE.splitlines(True))],metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5)
 p=ROOT/'src/notebooks/kaggle_delta_gpu_probe.ipynb';p.write_text(json.dumps(nb,indent=1),encoding='utf-8');print(p)
if __name__=='__main__':main()
