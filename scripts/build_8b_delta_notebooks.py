"""Build pinned, separate incremental 8B CPU/GPU jobs; never rewrite baseline parts."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / 'artifacts/embedding_8b_inventory_20261030'
FAMILIES = {'qwen8b': ('Qwen/Qwen3-Embedding-8B', 'dinhbaobao', ''),
            'octen8b': ('Octen/Octen-Embedding-8B', 'boinhbo', ' ')}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build(family, stage, attempt=1):
    assert attempt >= 1
    model, owner, prefix = FAMILIES[family]
    package_ref = f'boinhbo/medxir-package-{family}'
    package = AUDIT / package_ref.replace('/', '__') / 'models/MODELS.json'
    versions = json.loads((AUDIT / 'versions.json').read_text())
    baselines = [f'{owner}/medxir-embed-corpus-{family}-s{i}' for i in range(3)]
    release = ROOT / 'runs/smoke/recovery-finalize-20261029-v2-cpu/artifacts/recovery_release_20261029_v2/release_manifest.json'
    base_paths = [AUDIT / ref.replace('/', '__') / 'embed_manifest.json' for ref in baselines]
    manifests = [json.loads(p.read_text()) for p in base_paths]
    assert all(m['complete'] and m['model'] == model and m['doc_prefix'] == prefix and m['dim'] == 4096 for m in manifests)
    assert sum(m['chunks'] for m in manifests) == 2621553
    spec = dict(model=model, dim=4096, slug=family, doc_prefix=prefix,
                release_manifest_sha256=sha(release), baseline_chunk_manifest=json.loads(release.read_text())['baseline_manifest'],
                baseline_embed_manifest_sha256=[sha(p) for p in base_paths], expected_old_chunks=2621553,
                model_revision=json.loads(package.read_text())['models'][model]['sha'], model_package_manifest_sha256=sha(package))
    plan_ref = f'ntklinhfitus/medxir-{family}-delta-plan-20261030'
    plan_rel = f'{family}_additions_plan/embedding_plan_manifest.json'
    if stage == 'plan':
        job = 'prepare_8b_delta.py'
        sources = baselines + ['ntklinhfitus/medxir-recovery-finalize-20261029-v2']
        expected = [dict(kernel=ref, version=versions[ref], manifest='embed_manifest.json', manifest_sha256=sha(p)) for ref, p in zip(baselines, base_paths)]
        expected += [dict(kernel=sources[-1], version=1, manifest='recovery_release_20261029_v2/release_manifest.json', manifest_sha256=sha(release))]
        datasets = ['ntklinhfitus/medxir-chunk-corpus']
        files = [plan_rel, f'{family}_additions_plan/reuse_provenance.parquet', f'{family}_additions_plan/golden_texts.parquet']
        slug = plan_ref.split('/')[1]
        spec_file = 'embedding_spec.json'
        checks = []
    else:
        plan_path = ROOT / f'runs/smoke/{family}-delta-plan-20261030-v1/artifacts' / plan_rel
        plan = json.loads(plan_path.read_text())
        assert plan['model'] == model and plan['doc_prefix'] == prefix and plan['output_chunks'] == 270405
        assert plan['already_embedded_chunk_ids'] == 0
        job = 'embed_8b_delta.py'
        spec = dict(embedding_plan_manifest_sha256=sha(plan_path))
        sources = [plan_ref, package_ref]
        expected = [dict(kernel=plan_ref, version=1, manifest=plan_rel, manifest_sha256=sha(plan_path)),
                    dict(kernel=package_ref, version=versions[package_ref], manifest='models/MODELS.json', manifest_sha256=sha(package))]
        datasets = []
        files = ['incremental_embed_manifest.json']  # large vectors stay on Kaggle for repackaging
        slug = f'medxir-{family}-additions-20261030' + (f'-r{attempt}' if attempt > 1 else '')
        spec_file = 'incremental_embed_spec.json'
        checks = [dict(path='complete', value=True), dict(path='total_rows', value=270405), dict(path='nan_rows', value=0)]
    source = (ROOT / 'scripts' / job).read_text(encoding='utf-8')
    code = 'SPEC=' + repr(spec) + '\nSOURCE=' + repr(source) + '\n'
    code += f'''from pathlib import Path
import json, subprocess, sys
p=Path('/kaggle/working/{job}'); p.write_text(SOURCE, encoding='utf-8')
Path('/kaggle/working/{spec_file}').write_text(json.dumps(SPEC))
subprocess.run([sys.executable, '-u', str(p)], check=True)
'''
    compile(code, '<incremental-8b>', 'exec')
    notebook = ROOT / f'src/notebooks/kaggle_{family}_delta_{stage}.ipynb'
    notebook.write_text(json.dumps(dict(cells=[dict(cell_type='code', id='verified-incremental-8b', metadata={}, execution_count=None, outputs=[], source=code.splitlines(True))],
        metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3')), nbformat=4, nbformat_minor=5), indent=1), encoding='utf-8')
    k = dict(id='ntklinhfitus/' + slug, title=slug, code_file=notebook.relative_to(ROOT).as_posix(), kernel_type='notebook',
             immutable_kernel=True, enable_gpu=stage == 'gpu', enable_internet=False, kernel_sources=sources,
             kernel_source_expectations=expected, dataset_sources=datasets, timeout_seconds=21600 if stage == 'gpu' else 5400,
             poll_interval_seconds=60 if stage == 'gpu' else 30, metrics_file=files[0], fetch_files=files,
             required_outputs=[files[0]], json_checks=checks)
    if stage == 'gpu':
        k.update(machine_shape='NvidiaRtxPro6000', expected_hardware_contains='6000', competition_sources=['arc-prize-2026-arc-agi-3'])
    dest = ROOT / f'configs/kaggle/{family}_delta_{stage}_20261030.yaml'
    dest.write_text(json.dumps(dict(kernel=k), indent=2), encoding='utf-8')
    if stage == 'gpu':
        mapping_path = AUDIT / 'GPU_RUNS.json'
        mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
        mapping[family] = dict(kernel=k['id'], version=1,
            run_id=f'{family}-additions-20261030-v1' + (f'-r{attempt}' if attempt > 1 else ''))
        mapping_path.write_text(json.dumps(mapping, indent=2), encoding='utf-8')
    print(dest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=FAMILIES, required=True)
    parser.add_argument('--stage', choices=['plan', 'gpu'], required=True)
    parser.add_argument('--attempt', type=int, default=1, help='New immutable slug for a rejected/failed GPU submission')
    args = parser.parse_args()
    build(args.model, args.stage, args.attempt)
