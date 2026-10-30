"""Resume read-only GPU monitoring after a local transport failure; never resubmit GPU work."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.kaggle.accounts import account_session
from tools.kaggle.kernel import KaggleKernelExecutor
from tools.kaggle.models import KernelConfig, RunStatus
from tools.kaggle.errors import KaggleCommandError, OutputValidationError
from tools.kaggle.validation import validate_outputs, validate_expected_hardware, observed_hardware_from_result
from build_8b_delta_notebooks import FAMILIES


def recover(family):
    audit = ROOT / 'artifacts/embedding_8b_inventory_20261030'
    run = json.loads((audit / 'GPU_RUNS.json').read_text())[family]
    folder = ROOT / 'runs/smoke' / run['run_id']
    path = folder / 'metadata.json'
    original = json.loads(path.read_text())
    assert original['kernel'] == run['kernel'] and original['kernel_version'] == run['version']
    backup = folder / 'metadata.before-monitor-recovery.json'
    if not backup.exists():
        backup.write_text(json.dumps(original, indent=2), encoding='utf-8')
    deadline = time.monotonic() + 8 * 3600
    last = None
    with account_session('ntklinhfitus'):
        executor = KaggleKernelExecutor(command_timeout_seconds=90)
        while time.monotonic() < deadline:
            try:
                status = executor.status(run['kernel'], timeout_seconds=90)
                observed = dict(kernel=run['kernel'], status=status.status.value,
                    observed_at=datetime.now(timezone.utc).isoformat(), failure_message=status.failure_message)
                (audit / f'{family}_remote_status.json').write_text(json.dumps(observed, indent=2))
                if status.status != last:
                    print('Remote GPU:', family, status.status.value, flush=True)
                    last = status.status
                if status.status == RunStatus.SUCCESS:
                    assert executor.kernel_current_version(run['kernel']) == run['version'], 'Remote producer version changed'
                    config = KernelConfig.from_dict(original['config'])
                    fetched = executor.fetch(run['kernel'], folder / 'artifacts', fetch_files=config.fetch_files)
                    assert fetched.command.returncode == 0
                    result = validate_outputs(folder / 'artifacts', config, folder)
                    validate_expected_hardware(config.expected_hardware_contains, observed_hardware_from_result(result))
                    plan_path = ROOT / f'runs/smoke/{family}-delta-plan-20261030-v1/artifacts/{family}_additions_plan/embedding_plan_manifest.json'
                    plan = json.loads(plan_path.read_text())
                    assert result['source_plan_sha256'] == hashlib.sha256(plan_path.read_bytes()).hexdigest()
                    assert result['model'] == FAMILIES[family][0] and result['dim'] == 4096 and result['doc_prefix'] == FAMILIES[family][2]
                    assert result['model_revision'] == plan['model_revision'] and result['completed_rows'] == 270405
                    assert result['compatibility_check_old_texts'] == 27 and result['compatibility_cosine_min'] >= .999
                    assert not result['baseline_modified'] and not result['full_baseline_reembedding']
                    updated = dict(original, status='SUCCESS', error=None, remote_status='SUCCESS',
                        remote_failure_message=None, observed_hardware=result['gpu'], finished_at=datetime.now(timezone.utc).isoformat(),
                        monitor_recovery=dict(original_metadata=backup.name, remote_status_verified=True,
                            manifest_sha256=hashlib.sha256((folder / 'artifacts/incremental_embed_manifest.json').read_bytes()).hexdigest()))
                    temp = path.with_suffix('.json.tmp')
                    temp.write_text(json.dumps(updated, indent=2), encoding='utf-8'); os.replace(temp, path)
                    print('RECOVERED SUCCESS:', family, 'actual remote completion and manifest verified; no GPU resubmission', flush=True)
                    return
                if status.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
                    raise RuntimeError('Actual remote GPU failed: ' + str(observed))
            except KaggleCommandError as exc:
                print('Read-only Kaggle transport retry:', type(exc).__name__, flush=True)
            except (AssertionError, OutputValidationError, RuntimeError):
                raise
            except Exception as exc:
                print('Read-only monitor retry:', type(exc).__name__, flush=True)
            time.sleep(30)
    raise TimeoutError('Read-only monitor deadline exceeded; remote job was not cancelled')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', choices=FAMILIES, required=True)
    args = p.parse_args()
    recover(args.model)
