"""Generate a native Kaggle check of the residual adapters using existing samples."""
from pathlib import Path
import json,hashlib
ROOT=Path(__file__).resolve().parents[1]
CHECK=r'''
import zipfile,hashlib
archive_paths=list(Path('/kaggle/input').rglob('residual_samples.zip'))
if archive_paths:
 z=zipfile.ZipFile(archive_paths[0]);names=[n for n in z.namelist() if n.endswith('.json')]
 def read(n):return z.read(n)
else:
 names=list(Path('/kaggle/input').rglob('*.sqlite__*.json'))
 def read(n):return Path(n).read_bytes()
assert len(names)==18,len(names)
rows=[]
for name in names:
 m=json.loads(read(name));binary=str(name)[:-5]+'.bin'
 result=worker.parse_html((m['doc_id'],m['url'],m['host'],m['lang'],m['final_url'],m['content_type'],read(binary),m['source_path']))
 expected=m['doc_id'] not in {2474097,6693553,7414111}
 assert result['accepted']==expected,(m['doc_id'],result)
 if m['host']=='pubmed.ncbi.nlm.nih.gov' and expected:
  bad=worker.parse_html((m['doc_id'],'https://pubmed.ncbi.nlm.nih.gov/999999999/',m['host'],m['lang'],m['final_url'],m['content_type'],read(binary),m['source_path']))
  assert not bad['accepted'] and bad['error_reason']=='pubmed_pmid_mismatch'
 rows.append({k:result.get(k) for k in ['doc_id','host','accepted','status','text_chars','text_sha256','warnings','error_reason']})
assert not worker.known_mirror('https://zysjonline.com/books/book_a/12/','http://www.zysj.com.cn/lilunshuji/book_b/2.html')
try:worker.effective_url('https://web.archive.org/web/20200101120000/https://other.test/wrong','https://laodong.vn/story.ldo','laodong.vn')
except ValueError:pass
else:raise AssertionError('Unrelated archive host accepted')
report=dict(passed=True,samples=len(rows),accepted=sum(r['accepted'] for r in rows),
            modules={name:hashlib.sha256(code.encode()).hexdigest() for name,code in MODULES.items()},records=rows)
Path('/kaggle/working/parser_check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('PASS',report['samples'],'real samples;',report['accepted'],'accepted; XML PMID and archive guards')
'''

def main():
 modules={n:(ROOT/'scripts'/n).read_text(encoding='utf-8') for n in ['content_parser.py','text_clean.py','offline_recovery_parser.py']}
 setup='MODULES='+repr(modules)+"\nfrom pathlib import Path\nimport sys,json\np=Path('/kaggle/working/check_modules');p.mkdir()\nfor name,code in MODULES.items():(p/name).write_text(code,encoding='utf-8')\nsys.path.insert(0,str(p))\nimport offline_recovery_parser as worker\n"
 code=dict(cell_type='code',id='native-adapter-check',metadata={},execution_count=None,outputs=[],source=(setup+CHECK).splitlines(True))
 nb=dict(cells=[code],metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5)
 target=ROOT/'src/notebooks/kaggle_recovery_parser_check.ipynb';target.write_text(json.dumps(nb,ensure_ascii=False,indent=1),encoding='utf-8')
 summary=ROOT/'runs/smoke/recovery-residual-probe-20261029-v1/artifacts/residual_probe/summary.json'
 pin=hashlib.sha256(summary.read_bytes()).hexdigest()
 config='''kernel:
  id: ntklinhfitus/medxir-recovery-parser-check
  title: medxir-recovery-parser-check
  code_file: src/notebooks/kaggle_recovery_parser_check.ipynb
  kernel_type: notebook
  enable_gpu: false
  enable_internet: false
  kernel_sources: [ntklinhfitus/medxir-recovery-residual-probe]
  kernel_source_expectations:
    - kernel: ntklinhfitus/medxir-recovery-residual-probe
      version: 1
      manifest: residual_probe/summary.json
      manifest_sha256: '''+pin+'''
  timeout_seconds: 600
  poll_interval_seconds: 15
  fetch_files: [parser_check.json]
  required_outputs: [parser_check.json]
'''
 (ROOT/'configs/kaggle/recovery_parser_check_cpu.yaml').write_text(config,encoding='utf-8')
 print(target)

if __name__=='__main__':main()
