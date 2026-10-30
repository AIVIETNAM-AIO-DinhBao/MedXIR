"""Publish a verified, separate private Qwen4B additions dataset."""
from pathlib import Path
import hashlib,json,sys,time,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.kaggle.accounts import account_session
import publish_chunk_dataset_update as uploader

ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'runs/smoke/vector-additions-dataset-20261030-v1/artifacts'
AUDIT=ROOT/'artifacts/vector_additions_dataset_20261030'
REF='ntklinhfitus/medxir-vectors-qwen3-4b-additions'


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    AUDIT.mkdir(parents=True,exist_ok=True)
    report=json.loads((ART/'vector_repack_validation.json').read_text())
    assert report['complete'] and report['chunks']==270405 and report['parts']==3
    assert report['new_model_forward_inputs']==0 and report['vectors_bitwise_preserved'] and report['all_ids_match_corpus_part_order']
    archive=ART/'vector_payload.zip';assert archive.stat().st_size==report['archive']['bytes'] and sha(archive)==report['archive']['sha256']
    folder=AUDIT/'payload';folder.mkdir(exist_ok=True)
    if not all((folder/n).is_file() and (folder/n).stat().st_size==m['bytes'] and sha(folder/n)==m['sha256'] for n,m in report['files'].items()):
        with zipfile.ZipFile(archive) as z:
            assert set(z.namelist())==set(report['files'])
            for name in z.namelist():assert (folder/name).resolve().is_relative_to(folder.resolve())
            z.extractall(folder)
    for name,meta in report['files'].items():
        f=folder/name;assert f.stat().st_size==meta['bytes'] and sha(f)==meta['sha256'],name
    (AUDIT/'payload_validation.json').write_text(json.dumps(dict(complete=True,files_verified=len(report['files']),chunks=270405),indent=2))
    print('Verified payload:',len(report['files']),'files, 270405 vectors',flush=True)
    receipt=AUDIT/'new_dataset_publish_receipt.json'
    uploader.AUDIT=AUDIT
    with account_session('ntklinhfitus'):
        from kaggle.api.kaggle_api_extended import KaggleApi
        api=KaggleApi();api.authenticate()
        if not receipt.exists():
            (AUDIT/'publication_intent.json').write_text(json.dumps(dict(dataset=REF,private=True,source='existing verified embeddings',new_model_forward_inputs=0),indent=2))
            uploader.structured_publish(api,folder,'',new_ref=REF,upload_report=report,description='Qwen3-Embedding-4B additions for medxir-chunk-corpus-v2: 270,405 float16 vectors in VI 007, EN 001 and ZH 001 parts. Repackaged from verified existing embeddings with no additional inference; IDs align with the matching chunk parts.')
            print('New private dataset creation accepted',flush=True)
        else:
            previous=json.loads(receipt.read_text());assert previous.get('status')=='Ok' and not previous.get('error'),previous
            print('Creation already recorded; checking status without resubmitting',flush=True)
        for attempt in range(180):
            try:v=uploader.view(api,REF)
            except Exception as exc:
                if getattr(getattr(exc,'response',None),'status_code',None) in (403,404):
                    print('Dataset initializing',flush=True);time.sleep(10);continue
                raise
            version=next((x for x in v.get('versions',[]) if x['versionNumber']==1),None)
            status=version.get('status') if version else 'not listed'
            print('Dataset version 1:',status,flush=True)
            if str(status).lower()=='ready':
                assert v['isPrivate'] and v['currentVersionNumber']==1
                files=api.dataset_list_files(REF,page_size=100).to_dict()
                actual={x['name']:x['totalBytes'] for x in files['datasetFiles']}
                assert actual=={n:m['bytes'] for n,m in report['files'].items()},actual
                (AUDIT/'dataset_view.json').write_text(json.dumps(v,default=str,indent=2))
                (AUDIT/'dataset_files.json').write_text(json.dumps(files,default=str,indent=2))
                print('READY: all seven file paths and sizes match; private',flush=True);return
            if any(x in str(status).lower() for x in ['error','fail']):raise RuntimeError(str(status))
            time.sleep(10)
        raise TimeoutError('Dataset still processing; never create it again')


if __name__=='__main__':main()
