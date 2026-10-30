"""Read-only Kaggle logs; writes an audit copy and never prints credentials."""
import argparse
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.kaggle.accounts import account_session

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('kernel')
    args = p.parse_args()
    out = Path('artifacts/embedding_8b_inventory_20261030/live')
    out.mkdir(parents=True, exist_ok=True)
    with account_session('ntklinhfitus'):
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi(); api.authenticate()
        seen = set()
        with (out / (args.kernel.split('/')[-1] + '.log')).open('a', encoding='utf-8') as f:
            for attempt in range(180):
                try:
                    for event in api.kernels_logs_stream(args.kernel):
                        value = event.get('data', '')
                        f.write(value); f.flush()
                        value = value.strip()
                        if value and value not in seen:
                            seen.add(value)
                            if any(k in value for k in ['Verified ', 'PASS', 'Loading pinned', 'Model ready', 'Forward inputs', 'SEALED', 'COMPLETE', 'Traceback', 'Error', 'Reordered']):
                                print(value[:3000], flush=True)
                    break
                except Exception as exc:
                    print('Log stream reconnect:', type(exc).__name__, flush=True)
                    time.sleep(10)
