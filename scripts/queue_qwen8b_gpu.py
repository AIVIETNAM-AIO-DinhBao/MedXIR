"""Run Qwen8B only after this task's accepted Octen GPU job releases its slot."""
import json
from pathlib import Path
from finish_8b_releases import ROOT, AUDIT, invoke, wait_for

if __name__ == '__main__':
    wait_for('octen8b-additions-20261030-v1')
    mapping = json.loads((AUDIT / 'GPU_RUNS.json').read_text())['qwen8b']
    print('Octen GPU complete; launching queued Qwen8B on the released slot', flush=True)
    invoke('-m', 'tools.kaggle', 'kernel', 'run', '--config', 'configs/kaggle/qwen8b_delta_gpu_20261030.yaml',
           '--source', '.', '--account', 'ntklinhfitus', '--kind', 'smoke', '--run-id', mapping['run_id'])
