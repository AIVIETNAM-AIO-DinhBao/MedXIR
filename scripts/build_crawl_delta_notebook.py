"""Build a combined, isolated CPU chunk delta for verified crawl and offline additions."""
from pathlib import Path
import json, gzip, hashlib
from build_data_delta_notebook import RUN
ROOT=Path(__file__).resolve().parents[1]
def main():
    def cell(text):return dict(cell_type='code',metadata={},execution_count=None,outputs=[],source=text.splitlines(True))
    cells=[dict(cell_type='markdown',metadata={},source=['# Verified crawl + offline delta\n4733 post-baseline crawl IDs + 116 offline Long Chau IDs. CPU only. Does not merge or embed.'])]
    for name in ['text_clean.py','chunking.py']:
        cells.append(cell((ROOT/'scripts'/name).read_text(encoding='utf-8').replace("if __name__ == '__main__':",'if False:')))
    raw=gzip.decompress((ROOT/'artifacts/data_inventory_20261029/crawl_delta_input/crawl_delta_documents.jsonl.gz').read_bytes())
    cells.append(cell(LOAD.replace('__EXPECTED_RAW_SHA__',hashlib.sha256(raw).hexdigest())))
    run=RUN.replace("assert d['url'].startswith('https://nhathuoclongchau.com.vn/')", "assert d['url'].startswith(('https://','http://'))")
    run=run.replace("Path('/kaggle/working/data_delta_20261029')", "Path('/kaggle/working/crawl_chunk_delta_20261029')")
    cells.append(cell(run))
    target=ROOT/'src/notebooks/kaggle_crawl_delta_20261029.ipynb'
    target.write_text(json.dumps(dict(cells=cells,metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5),ensure_ascii=False,indent=1),encoding='utf-8');print(target)
LOAD=r"""
import json,base64,hashlib,gzip
from pathlib import Path
hits=list(Path('/kaggle/input').rglob('crawl_delta_documents.jsonl.gz'))+list(Path('/kaggle/input').rglob('crawl_delta_documents.jsonl'))
assert len(hits)==1,(hits,[str(p) for p in Path('/kaggle/input').rglob('*') if p.is_file()][:30])
path=hits[0];BATCH_MANIFEST=json.loads((path.parent/'crawl_delta_manifest.json').read_text())
if path.suffix=='.gz':
    payload=path.read_bytes()
    assert hashlib.sha256(payload).hexdigest()==BATCH_MANIFEST['payload_sha256']
    raw=gzip.decompress(payload)
else:
    raw=path.read_bytes()
    payload=gzip.compress(raw,mtime=0)
assert hashlib.sha256(raw).hexdigest()=='__EXPECTED_RAW_SHA__'
EXPECTED_PAYLOAD_SHA=hashlib.sha256(payload).hexdigest()
PAYLOAD_B64=base64.b64encode(payload).decode()
"""
if __name__=='__main__':main()
