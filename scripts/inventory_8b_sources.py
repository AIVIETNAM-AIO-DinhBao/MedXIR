"""Read-only audit of the team's existing 8B embedding outputs and source contracts."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.kaggle.accounts import account_session


def main():
    out = ROOT / 'artifacts/embedding_8b_inventory_20261030'
    out.mkdir(parents=True, exist_ok=True)
    refs = [f'dinhbaobao/medxir-embed-corpus-qwen8b-s{i}' for i in range(3)]
    refs += [f'boinhbo/medxir-embed-corpus-octen8b-s{i}' for i in range(3)]
    refs += ['dinhbaobao/medxir-encode-queries-qwen8b',
             'boinhbo/medxir-encode-queries-octen8b',
             'boinhbo/medxir-package-qwen8b', 'boinhbo/medxir-package-octen8b']
    summary = {}
    with account_session('ntklinhfitus'):
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi()
        api.authenticate()
        for ref in refs:
            dest = out / ref.replace('/', '__')
            dest.mkdir(exist_ok=True)
            try:
                status = str(api.kernels_status(ref))
                api.kernels_pull(ref, str(dest), metadata=True, quiet=True)
                api.kernels_output(ref, str(dest), file_pattern=r'(^|/)(embed_manifest|query_manifest|encode_manifest|MODELS)\.json$', quiet=True)
                result = dict(readable=True, status=status, local=str(dest.relative_to(ROOT)))
                for path in dest.rglob('*.json'):
                    if path.name == 'kernel-metadata.json':
                        continue
                    try:
                        m = json.loads(path.read_text(encoding='utf-8'))
                        result[path.name] = {k: v for k, v in m.items() if k not in ('parts', 'models', 'files')}
                    except (ValueError, AttributeError):
                        pass
                summary[ref] = result
                print(ref, status, 'readable', flush=True)
            except Exception as exc:
                summary[ref] = dict(readable=False, error=str(exc)[:500])
                print(ref, type(exc).__name__, str(exc)[:150], flush=True)
            (out / 'inventory.json').write_text(json.dumps(summary, indent=2, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
