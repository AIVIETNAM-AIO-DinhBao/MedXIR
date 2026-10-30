"""Recover the existing Qwen GPU monitor if a transient API error interrupts it."""
import json
from pathlib import Path
import time
from recover_8b_gpu_monitor import recover

ROOT = Path(__file__).resolve().parents[1]
METADATA = ROOT / 'runs/smoke/qwen8b-additions-20261030-v1-r2/metadata.json'

if __name__ == '__main__':
    deadline = time.monotonic() + 8 * 3600
    while time.monotonic() < deadline:
        try:
            m = json.loads(METADATA.read_text())
        except (OSError, json.JSONDecodeError):
            time.sleep(10)
            continue
        if m['status'] == 'SUCCESS':
            print('Qwen monitor completed normally', flush=True)
            break
        if m['status'] == 'ERROR':
            if any(s in str(m.get('error')) for s in ('ConnectTimeout', 'ConnectionError', 'timed out', 'ConnectionAborted')):
                print('Qwen monitor hit a transport error; checking the existing remote job', flush=True)
                recover('qwen8b')
                break
            raise RuntimeError('Qwen reported an error; inspect the original metadata')
        if m['status'] in ('SUBMIT_ERROR', 'MISSING_OUTPUT', 'TIMEOUT'):
            raise RuntimeError('Qwen runner requires attention: ' + m['status'])
        time.sleep(10)
    else:
        raise TimeoutError('Qwen monitor deadline; the remote job was not cancelled')
