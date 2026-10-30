"""Read-only live logs for the active additions job; never prints credentials."""
from pathlib import Path
import time,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.kaggle.accounts import account_session

REF='ntklinhfitus/medxir-qwen4b-additions-20261030'
OUT=Path('artifacts/embedding_inventory_20261030/gpu_live');OUT.mkdir(exist_ok=True,parents=True)
with account_session('ntklinhfitus'):
    from kaggle.api.kaggle_api_extended import KaggleApi
    api=KaggleApi();api.authenticate()
    seen=set()
    for attempt in range(120):
        try:
            with (OUT/'current.log').open('a',encoding='utf-8') as handle:
                for event in api.kernels_logs_stream(REF):
                    data=event.get('data','');handle.write(data);handle.flush()
                    line=data.strip()
                    if line not in seen and any(word in line for word in ['Loading pinned','Model ready','PASS:','SEALED','COMPLETE','Forward inputs','Error','Traceback']):
                        print(line,flush=True);seen.add(line)
            break
        except Exception as exc:
            print('Log stream retry:',type(exc).__name__,flush=True);time.sleep(5)
