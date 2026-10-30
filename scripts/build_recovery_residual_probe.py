"""Collect a few raw samples for residual parser failures on Kaggle CPU."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
RUN=r'''
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import quote
import gzip,hashlib,io,json,shutil,sqlite3,zipfile,time
import zstandard
import pyarrow.parquet as pq
root=next(Path('/kaggle/input').rglob('recovery_manifest.json')).parent
spec=json.loads((root/'recovery_manifest.json').read_text())
parent=next(Path('/kaggle/input').rglob('release_manifest.json')).parent
review=parent/'needs_review.jsonl.gz'
if review.exists():f=gzip.open(review,'rt',encoding='utf-8')
else:f=(parent/'needs_review.jsonl').open(encoding='utf-8')
hosts={'zysjonline.com','pubmed.ncbi.nlm.nih.gov','vietnamnet.vn','article.iiyi.com','baidianfeng.familydoctor.com.cn'}
rejected={r['doc_id'] for r in map(json.loads,f) if r['host'] in hosts}
f.close()
matches=[r for r in pq.read_table(root/'source_matches.parquet').to_pylist() if r['doc_id'] in rejected]
files={r['path']:r for r in json.loads((root/'source_files.json').read_text())}
by_file={}
for r in matches:by_file.setdefault(r['source_path'],[]).append(r)
out=Path('/kaggle/working/residual_probe');out.mkdir(exist_ok=True)
local=Path('/kaggle/working/residual_source.sqlite')
summary=[]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
with zipfile.ZipFile(out/'residual_samples.zip','w',zipfile.ZIP_DEFLATED) as archive:
 for name,rows in by_file.items():
  entry=files[name];print('Sample',name,len(rows),flush=True)
  url='https://huggingface.co/datasets/'+spec['source_repository']+'/resolve/'+spec['source_revision']+'/'+quote(name,safe='/')+'?download=true'
  for attempt in range(3):
   try:
    with urlopen(url,timeout=120) as response,local.open('wb') as f:shutil.copyfileobj(response,f,1024*1024)
    assert local.stat().st_size==entry['size']
    if entry.get('lfs'):assert sha(local)==entry['lfs']['oid']
    break
   except Exception:
    if attempt==2:raise
    time.sleep(3)
  db=sqlite3.connect(local.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
  selected=[rows[i] for i in sorted({0,len(rows)//2,len(rows)-1})]
  for m in selected:
   rec=db.execute('SELECT id,url,final_url,ctype,html FROM pages WHERE id=?',(m['doc_id'],)).fetchone()
   i,u,landed,ctype,blob=rec;assert u==m['url']
   if blob[:4]==bytes.fromhex('28b52ffd'):
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(blob)) as reader:raw=reader.read(8*1024*1024+1)
   elif blob[:2]==bytes.fromhex('1f8b'):
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as reader:raw=reader.read(8*1024*1024+1)
   else:raw=bytes(blob)
   assert len(raw)<=8*1024*1024
   meta=dict(m,final_url=landed,content_type=ctype,source_path=name,raw_bytes=len(raw))
   key=name.replace('/','__')+'__'+str(i)
   archive.writestr(key+'.bin',raw);archive.writestr(key+'.json',json.dumps(meta,ensure_ascii=False))
   summary.append(meta)
  db.close();local.unlink()
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print('Saved samples',len(summary))
'''

def main():
    code=dict(cell_type='code',id='residual-samples',metadata={},execution_count=None,outputs=[],source=RUN.splitlines(True))
    nb=dict(cells=[code],metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5)
    target=ROOT/'src/notebooks/kaggle_recovery_residual_probe.ipynb'
    target.write_text(json.dumps(nb,ensure_ascii=False,indent=1),encoding='utf-8')
    print(target)

if __name__=='__main__':main()
