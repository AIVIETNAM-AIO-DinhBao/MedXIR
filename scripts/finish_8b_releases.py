"""Finish verified 8B GPU outputs: CPU repack, private publication, remote check, cache release."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / 'artifacts/embedding_8b_inventory_20261030'


def invoke(*args):
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def wait_for(run_id):
    p = ROOT / 'runs/smoke' / run_id / 'metadata.json'
    deadline = time.monotonic() + 8 * 3600
    last = None
    while time.monotonic() < deadline:
        try:
            m = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            time.sleep(10)
            continue
        state = m['status']
        if state != last:
            print(run_id, state, flush=True)
            last = state
        if state == 'SUCCESS':
            return
        if state == 'ERROR' and any(s in str(m.get('error')) for s in ('ConnectTimeout', 'ConnectionError', 'timed out', 'ConnectionAborted')):
            # The read-only recovery monitor verifies real remote completion before updating metadata.
            time.sleep(10)
            continue
        if state not in ('RUNNING', 'QUEUED', 'STAGED', 'PENDING', 'SUBMITTED', 'SUBMITTING', 'CREATED'):
            raise RuntimeError(f'{run_id}: {state}; inspect metadata before retrying')
        time.sleep(10)
    raise TimeoutError(run_id)


def cpu_run(family, stage):
    run_id = f'{family}-{stage}-20261030-v1'
    p = ROOT / 'runs/smoke' / run_id / 'metadata.json'
    if p.exists():
        wait_for(run_id)
        return
    invoke('-m', 'tools.kaggle', 'kernel', 'run', '--config', f'configs/kaggle/{family}_{stage}_20261030.yaml',
           '--source', '.', '--account', 'ntklinhfitus', '--kind', 'smoke', '--run-id', run_id)
    wait_for(run_id)


def main(families):
    state_path = AUDIT / 'RELEASE_STATE.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    failures = []
    for family in families:
        try:
            final = AUDIT / 'releases' / family / 'STATE.json'
            if final.exists() and json.loads(final.read_text())['status'] == 'COMPLETE':
                print('Already complete:', family, flush=True)
                state[family] = 'COMPLETE'
                continue
            state[family] = 'WAIT_GPU'; state_path.write_text(json.dumps(state, indent=2))
            mapping_path = AUDIT / 'GPU_RUNS.json'
            mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
            wait_for(mapping.get(family, {}).get('run_id', f'{family}-additions-20261030-v1'))
            invoke('scripts/build_8b_release_notebooks.py', '--model', family, '--stage', 'repack')
            state[family] = 'CPU_REPACK'; state_path.write_text(json.dumps(state, indent=2))
            cpu_run(family, 'repack')
            state[family] = 'PRIVATE_PUBLICATION'; state_path.write_text(json.dumps(state, indent=2))
            invoke('scripts/publish_8b_additions_dataset.py', '--model', family)
            invoke('scripts/build_8b_release_notebooks.py', '--model', family, '--stage', 'verify')
            state[family] = 'REMOTE_VERIFY'; state_path.write_text(json.dumps(state, indent=2))
            cpu_run(family, 'verify')
            invoke('scripts/publish_8b_additions_dataset.py', '--model', family, '--cleanup')
            state[family] = 'COMPLETE'; state_path.write_text(json.dumps(state, indent=2))
        except Exception as exc:
            failures.append(family)
            state[family] = dict(status='NEEDS_ATTENTION', error=str(exc))
            state_path.write_text(json.dumps(state, indent=2))
            print('NEEDS_ATTENTION', family, str(exc), flush=True)
    if failures:
        raise SystemExit('Incomplete releases: ' + ', '.join(failures))
    print('COMPLETE: all requested 8B additions datasets remotely verified', flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--models', nargs='+', choices=['qwen8b', 'octen8b'], default=['octen8b', 'qwen8b'])
    args = p.parse_args()
    main(args.models)
