from pathlib import Path
import json
from build_source_inventory_notebook import RUN
ROOT=Path(__file__).resolve().parents[1]
def main():
    def cell(text):return dict(cell_type='code',metadata={},execution_count=None,outputs=[],source=text.splitlines(True))
    parser=(ROOT/'scripts/content_parser.py').read_text(encoding='utf-8').replace("if __name__ == '__main__':",'if False:')
    run=RUN.replace("files.sort(key=", "files=[f for f in files if f['path'] in ['db/www.familydoctor.com.cn.sqlite','db_cc/nhathuoclongchau.com.vn.sqlite','db/laodong.vn.sqlite']]\nfiles.sort(key=",1)
    run=run.replace("Path('/kaggle/working/source_inventory_20261029')", "Path('/kaggle/working/recovery_parser_probe')")
    run=run.replace("for i in hits[:2]:", "for i in hits[:3]:")
    run=run.replace("record=dict(doc_id=i,url=u,host=host,final_url=final,state='html_response',content_type='text/html; charset=utf-8')", "record=dict(doc_id=i,url=u,host=host,final_url=final,state='html_response',content_type='text/html; charset=utf-8')\n                    sample_dir=out/'html';sample_dir.mkdir(exist_ok=True)\n                    (sample_dir/f'{i}.html').write_bytes(raw)\n                    (sample_dir/f'{i}.json').write_text(json.dumps(record,ensure_ascii=False))")
    run += "\nimport zipfile\nwith zipfile.ZipFile(out/'sample_html.zip','w',zipfile.ZIP_DEFLATED) as archive:\n    for p in (out/'html').glob('*'): archive.write(p,p.name)\n"
    nb=dict(cells=[dict(cell_type='markdown',metadata={},source=['# Offline parser probe; three public source files, 3 samples each.']),cell(parser),cell(run)],metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5)
    target=ROOT/'src/notebooks/kaggle_recovery_parser_probe.ipynb';target.write_text(json.dumps(nb,ensure_ascii=False,indent=1),encoding='utf-8');print(target)
if __name__=='__main__':main()
