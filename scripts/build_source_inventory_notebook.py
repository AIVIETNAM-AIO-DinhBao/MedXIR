"""CPU-only inventory of public SQLite sources for IDs missing from corpus v1."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
RUN=r"""
import json,time,hashlib,shutil,sqlite3,urllib.request,urllib.parse,gzip,zlib
from pathlib import Path
from collections import Counter,defaultdict
import pyarrow as pa
import pyarrow.parquet as pq
try:
    import zstandard
except ImportError:
    zstandard=None

root=next(Path('/kaggle/input').rglob('baseline_missing.parquet')).parent
spec=json.loads((root/'inventory_input_manifest.json').read_text())
source=json.loads((root/'source.json').read_text())
assert hashlib.sha256((root/'baseline_missing.parquet').read_bytes()).hexdigest()==spec['sha256']
records=pq.read_table(root/'baseline_missing.parquet').to_pylist()
assert len(records)==spec['candidate_rows']
remaining={r['doc_id']:r for r in records};original=dict(remaining)
by_host=defaultdict(set)
for r in records:by_host[r['host']].add(r['doc_id'])
files=json.loads((root/'relevant_source_files.json').read_text())
# Prioritize many missing IDs per byte; fallback stores are processed only while IDs remain.
files.sort(key=lambda f:len(by_host[Path(f['path']).stem])/max(1,f['size']),reverse=True)
out=Path('/kaggle/working/source_inventory_20261029');out.mkdir(exist_ok=True)
cache=Path('/kaggle/working/sqlite_inventory_cache');cache.mkdir(exist_ok=True)
started=time.monotonic();matches=[];checks=[];samples=[];downloaded=0
MAX_SECONDS=4800

def write_outputs():
    if matches:pq.write_table(pa.Table.from_pylist(matches),out/'source_matches.parquet',compression='zstd')
    residual=[original[i] for i in sorted(remaining)]
    if residual:pq.write_table(pa.Table.from_pylist(residual),out/'unresolved.parquet',compression='zstd')
    (out/'file_audit.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
    (out/'parser_samples.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2))
    counts=Counter(r['host'] for r in matches)
    summary=dict(candidate_ids=len(original),matched_html_ids=len(matches),unresolved_ids=len(remaining),
        hosts=dict(counts),file_statuses=dict(Counter(c['status'] for c in checks)),
        processed_files=len(checks),source_files=len(files),downloaded_bytes=downloaded,
        baseline=spec,source=source,elapsed_seconds=round(time.monotonic()-started,1),
        complete_file_inventory=len(checks)==len(files),
        limitation='A match proves official ID+URL and a stored HTTP 200 nonempty HTML blob; full text quality and novelty require separate parsing/hash checks.')
    summary['files']={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in out.iterdir() if p.is_file() and p.name!='summary.json'}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

for number,entry in enumerate(files,1):
    host=Path(entry['path']).stem;targets=sorted(by_host[host]&remaining.keys())
    audit=dict(path=entry['path'],bytes=entry['size'],target_ids=len(targets))
    if not targets:
        audit['status']='not_needed';checks.append(audit);continue
    if time.monotonic()-started>MAX_SECONDS:
        audit['status']='time_budget';checks.append(audit);break
    # The largest input is 14.75 GB, processed alone and deleted before the next one.
    if shutil.disk_usage(cache).free<entry['size']+1024**3:
        audit['status']='insufficient_scratch_space';checks.append(audit);continue
    path=cache/'source.sqlite';hits=[];t=time.monotonic()
    print(f"{number}/{len(files)} {entry['path']} targets={len(targets)} size={entry['size']/1e9:.2f}GB",flush=True)
    try:
        url='https://huggingface.co/datasets/'+source['repository']+'/resolve/'+source['revision']+'/'+urllib.parse.quote(entry['path'],safe='/')+'?download=true'
        h=hashlib.sha256();size_bytes=0
        with urllib.request.urlopen(url,timeout=120) as response,path.open('wb') as f:
            while True:
                block=response.read(4*1024*1024)
                if not block:break
                f.write(block);h.update(block);size_bytes+=len(block)
                if time.monotonic()-started>MAX_SECONDS:raise TimeoutError('job download budget')
        downloaded+=size_bytes
        assert size_bytes==entry['size']
        if entry.get('lfs'):assert h.hexdigest()==entry['lfs']['oid']
        audit['sha256']=h.hexdigest()
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
        try:
            db.execute('PRAGMA query_only=ON');db.execute('PRAGMA cache_size=-32768')
            columns={r[1] for r in db.execute('PRAGMA table_info(pages)')}
            assert {'id','url','status','html'}<=columns,columns
            for pos in range(0,len(targets),400):
                subset=targets[pos:pos+400];sql='SELECT id,url,status,length(html) FROM pages WHERE id IN ('+','.join('?' for _ in subset)+')'
                for i,u,status,nbytes in db.execute(sql,subset):
                    if i not in remaining:continue
                    expected=remaining[i]
                    if u!=expected['url']:
                        audit['url_mismatch']=audit.get('url_mismatch',0)+1;continue
                    if status!=200 or not nbytes:continue
                    matches.append(dict(doc_id=i,url=u,host=host,lang=expected['lang'],baseline_state=expected['state'],source_path=entry['path'],http_status=status,compressed_html_bytes=nbytes))
                    hits.append(i);del remaining[i]
            for i in hits[:2]:
                u,final,blob=db.execute('SELECT url,final_url,html FROM pages WHERE id=?',(i,)).fetchone()
                sample=dict(doc_id=i,host=host,source_path=entry['path'])
                try:
                    if blob[:4]==bytes.fromhex('28b52ffd'):
                        if zstandard is None:raise ValueError('zstandard decoder unavailable')
                        raw=zstandard.ZstdDecompressor().decompress(blob)
                    elif blob[:2]==bytes.fromhex('1f8b'):raw=gzip.decompress(blob)
                    else:raw=blob
                    record=dict(doc_id=i,url=u,host=host,final_url=final,state='html_response',content_type='text/html; charset=utf-8')
                    document=parse_page(record,raw)
                    sample.update(status=document['status'],title=document.get('title'),text_chars=len(document.get('text','')),warnings=document.get('warnings'))
                except Exception as exc:sample['error']=type(exc).__name__
                samples.append(sample)
        finally:db.close()
        audit.update(status='checked',matched_ids=len(hits),seconds=round(time.monotonic()-t,1))
        print('  matched',len(hits),'remaining',len(remaining),flush=True)
    except Exception as exc:
        audit.update(status='error',error_type=type(exc).__name__,http_code=getattr(exc,'code',None))
        print('  error',audit['error_type'],flush=True)
    finally:
        if path.exists():path.unlink()
    checks.append(audit);write_outputs()
summary=write_outputs()
print(json.dumps({k:summary[k] for k in ['matched_html_ids','unresolved_ids','hosts','file_statuses','complete_file_inventory','elapsed_seconds']},indent=2))
"""
def main():
    parser=(ROOT/'scripts/content_parser.py').read_text(encoding='utf-8').replace("if __name__ == '__main__':",'if False:')
    def cell(text):return dict(cell_type='code',metadata={},execution_count=None,outputs=[],source=text.splitlines(True))
    notebook=dict(cells=[dict(cell_type='markdown',metadata={},source=['# Public source inventory for corpus v1 gaps\nCPU, Internet ON. Read-only source audit. No bulk parsing, chunking, embedding, or submission.']),cell(parser),cell(RUN)],metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5)
    target=ROOT/'src/notebooks/kaggle_source_inventory_20261029.ipynb';target.write_text(json.dumps(notebook,ensure_ascii=False,indent=1),encoding='utf-8');print(target)
if __name__=='__main__':main()
