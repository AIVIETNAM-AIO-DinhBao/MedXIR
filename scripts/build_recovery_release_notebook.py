from pathlib import Path
import json
import argparse
ROOT=Path(__file__).resolve().parents[1]
def cell(source):return dict(cell_type='code',metadata={},execution_count=None,outputs=[],source=source.splitlines(True))
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--finalize',action='store_true')
    args=parser.parse_args()
    names=['content_parser.py','chunking.py','text_clean.py','dedup_groups.py','offline_recovery_parser.py','recovery_release_job.py']
    modules={n:(ROOT/'scripts'/n).read_text(encoding='utf-8') for n in names}
    options=''
    if args.finalize:
        ids=[r['doc_id'] for r in json.loads((ROOT/'artifacts/data_inventory_20261029/older_crawl_candidates.json').read_text())]
        assert len(set(ids))==82
        extra=(ROOT/'artifacts/data_inventory_20261029/kaggle_input/relevant_source_files.json').read_text(encoding='utf-8')
        assert len(json.loads(extra))==85
        options="import os\nos.environ['MEDXIR_PARENT_RELEASE']='1'\nos.environ['MEDXIR_STUB_IDS']="+repr(json.dumps(ids))+"\nextra=p/'extra_source_files.json'\nextra.write_text("+repr(extra)+",encoding='utf-8')\nos.environ['MEDXIR_EXTRA_SOURCE_FILES']=str(extra)\n"
    setup='MODULES = '+repr(modules)+"\nfrom pathlib import Path\nimport sys,subprocess,time\np=Path('/kaggle/working/recovery_modules');p.mkdir(exist_ok=True)\nfor name,text in MODULES.items():(p/name).write_text(text,encoding='utf-8')\n"+options+"RUNNER_LOG=Path('/kaggle/working/recovery_runner.log')\nLOG_HANDLE=RUNNER_LOG.open('w')\nPROCESS=subprocess.Popen([sys.executable,'-u',str(p/'recovery_release_job.py')],stdout=LOG_HANDLE,stderr=subprocess.STDOUT)\nprint('CPU recovery pid:',PROCESS.pid)\n"
    poll=r"""deadline=time.monotonic()+590
while PROCESS.poll() is None and time.monotonic()<deadline:
    time.sleep(20)
    if RUNNER_LOG.exists():
        with RUNNER_LOG.open('rb') as f:
            f.seek(max(0,RUNNER_LOG.stat().st_size-1500))
            lines=f.read().decode('utf-8',errors='replace').splitlines()
        print('\n'.join(lines[-3:]),flush=True)
if PROCESS.poll() is not None:print('CPU recovery exit:',PROCESS.returncode)
"""
    finish="""if PROCESS.poll() is None:
    PROCESS.terminate()
    raise TimeoutError('CPU recovery exceeded its eight-hour notebook budget')
LOG_HANDLE.close()
assert PROCESS.returncode==0,RUNNER_LOG.read_text(errors='replace')[-5000:]
summary=Path('/kaggle/working/recovery_release_20261029_v2/release_manifest.json')
assert summary.is_file()
print(summary.read_text()[:5000])
"""
    cells=[dict(cell_type='markdown',metadata={},source=['# Corpus additions v2 - supervised CPU script\nNo GPU or leaderboard. Each poll cell stays below ten minutes; total runtime capped at eight hours.']),cell(setup)]
    cells += [cell(poll) for _ in range(48)]
    cells.append(cell(finish))
    for i,c in enumerate(cells):c['id']=f'recovery-{i}'
    nb=dict(cells=cells,metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5)
    filename='kaggle_recovery_finalize_20261029_v2.ipynb' if args.finalize else 'kaggle_recovery_release_20261029_v2.ipynb'
    target=ROOT/'src/notebooks'/filename;target.write_text(json.dumps(nb,ensure_ascii=False,indent=1),encoding='utf-8');print(target)
if __name__=='__main__':main()
