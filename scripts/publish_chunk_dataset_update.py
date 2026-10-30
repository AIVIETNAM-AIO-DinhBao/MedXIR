"""Verify the prepared payload, then create one additive dataset version."""
from pathlib import Path
import argparse,hashlib,json,tempfile,time,zipfile,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.kaggle.accounts import account_session

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'artifacts/chunk_dataset_update_20261030'
ART=ROOT/'runs/smoke/chunk-dataset-update-20261030-v1/artifacts'
REF='ntklinhfitus/medxir-chunk-corpus'


def sha(path):
    with path.open('rb') as h:return hashlib.file_digest(h,'sha256').hexdigest()


def view(api,ref=REF):
    from kagglesdk.datasets.types.dataset_api_service import ApiGetDatasetRequest
    r=ApiGetDatasetRequest();r.owner_slug,r.dataset_slug=ref.split('/',1)
    with api.build_kaggle_client() as c:return c.datasets.dataset_api_client.get_dataset(r).to_dict()


def prepare():
    report=json.loads((ART/'chunk_dataset_update_manifest.json').read_text())
    assert report['complete'] and report['total_chunks']==2891958 and report['chunk_parts']==12
    archive=ART/'dataset_payload.zip'
    assert archive.stat().st_size==report['archive']['bytes'] and sha(archive)==report['archive']['sha256']
    folder=AUDIT/'payload';folder.mkdir(exist_ok=True)
    existing_ok=all((folder/name).is_file() and (folder/name).stat().st_size==meta['bytes'] and sha(folder/name)==meta['sha256'] for name,meta in report['files'].items())
    if not existing_ok:
        with zipfile.ZipFile(archive) as z:
            assert set(z.namelist())==set(report['files'])
            for name in z.namelist():
                target=(folder/name).resolve();assert target.is_relative_to(folder.resolve())
            z.extractall(folder)
    for name,meta in report['files'].items():
        path=folder/name
        assert path.stat().st_size==meta['bytes'] and sha(path)==meta['sha256'],name
    before=json.loads((AUDIT/'before_view.json').read_text())
    metadata=dict(id=REF,title=before['title'],description=before.get('description') or '',
        keywords=[tag['id'] for tag in before.get('tags',[])])
    if before.get('subtitle'):metadata['subtitle']=before['subtitle']
    (folder/'dataset-metadata.json').write_text(json.dumps(metadata),encoding='utf-8')
    (AUDIT/'local_payload_validation.json').write_text(json.dumps(dict(complete=True,files_verified=len(report['files']),
        old_chunk_files_preserved=True,total_chunks=report['total_chunks'],pending_aliases=report['pending_alias_document_ids']),indent=2))
    print('PAYLOAD VERIFIED:',len(report['files']),'files; 12 chunk parts; old files unchanged',flush=True)
    return folder


def structured_publish(api,folder,notes,by_id=False,new_ref=None,upload_report=None,description=None):
    # Same request tree as KaggleHub's datasets_helpers._create_dataset_version.
    # Individual files retain their directory names; no TAR/ZIP conversion.
    from kaggle.api.kaggle_api_extended import ResumableUploadContext
    from kagglesdk.blobs.types.blob_api_service import ApiBlobType
    from kagglesdk.datasets.types.dataset_api_service import ApiUploadDirectoryInfo,ApiCreateDatasetVersionRequest,ApiCreateDatasetVersionByIdRequest,ApiCreateDatasetVersionRequestBody,ApiCreateDatasetRequest
    temp=AUDIT/'upload_tmp';temp.mkdir(exist_ok=True);tempfile.tempdir=str(temp)
    report=upload_report if upload_report is not None else json.loads((ART/'chunk_dataset_update_manifest.json').read_text())
    root=ApiUploadDirectoryInfo();root.name='root';uploaded={}
    with ResumableUploadContext() as context:
        def upload(name):
            value=api._upload_file(Path(name).name,str(folder/name),ApiBlobType.DATASET,context,True,None)
            assert value is not None,name
            return api._new_file(value)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures={pool.submit(upload,name):name for name in report['files']}
            for future in as_completed(futures):
                name=futures[future];uploaded[name]=future.result()
                print(f'UPLOADED {len(uploaded)}/{len(futures)}: {name}',flush=True)
                (AUDIT/'structured_upload_progress.json').write_text(json.dumps(dict(files_done=len(uploaded),files_total=len(futures),completed_names=sorted(uploaded)),indent=2))
        for name,value in sorted(uploaded.items()):
            directory=root
            for component in Path(name).parts[:-1]:
                child=next((x for x in directory.directories if x.name==component),None)
                if child is None:
                    child=ApiUploadDirectoryInfo();child.name=component;directory.directories.append(child)
                directory=child
            directory.files.append(value)
        if new_ref:
            request=ApiCreateDatasetRequest();request.owner_slug,request.slug=new_ref.split('/',1)
            request.title=request.slug;request.is_private=True
            request.description=description or 'Complete corpus v2: nine unchanged baseline chunk parts plus three verified VI/EN/ZH additions. 2,891,958 chunks, clean-v1, 350/50/40. Original chunk IDs preserved. 196 aliases to existing text retained separately pending mapping review. Embeddings are separate.'
            request.files=root.files;request.directories=root.directories
        elif by_id:
            request=ApiCreateDatasetVersionByIdRequest();request.id=json.loads((AUDIT/'before_view.json').read_text())['id']
        else:
            request=ApiCreateDatasetVersionRequest();request.owner_slug='ntklinhfitus';request.dataset_slug='medxir-chunk-corpus'
        if not new_ref:
            body=ApiCreateDatasetVersionRequestBody();body.version_notes=notes;body.delete_old_versions=False
            body.files=root.files;body.directories=root.directories;request.body=body
        with api.build_kaggle_client() as client:
            if new_ref:result=client.datasets.dataset_api_client.create_dataset(request)
            else:result=client.datasets.dataset_api_client.create_dataset_version_by_id(request) if by_id else client.datasets.dataset_api_client.create_dataset_version(request)
        (AUDIT/('new_dataset_publish_receipt.json' if new_ref else 'publish_receipt.json')).write_text(json.dumps(result.to_dict(),default=str,indent=2))
        assert not result.error,result.error
        return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--publish',action='store_true');p.add_argument('--wait',action='store_true');p.add_argument('--structured',action='store_true');p.add_argument('--by-id',action='store_true');p.add_argument('--new-dataset');args=p.parse_args()
    folder=prepare()
    if not args.publish and not args.wait:return
    with account_session('ntklinhfitus'):
        from kaggle.api.kaggle_api_extended import KaggleApi
        api=KaggleApi();api.authenticate()
        before=json.loads((AUDIT/'before_view.json').read_text());current=view(api)
        if args.new_dataset:
            assert args.new_dataset.startswith('ntklinhfitus/') and args.new_dataset!=REF
            assert current['currentVersionNumber']==1 and current['isPrivate'] is True
            receipt=AUDIT/'new_dataset_publish_receipt.json'
            if args.publish:
                assert not receipt.exists(),'Creation already recorded; use --wait'
                structured_publish(api,folder,'',new_ref=args.new_dataset)
                print('NEW PRIVATE DATASET CREATED',flush=True)
            if args.wait:
                for attempt in range(120):
                    try:created=view(api,args.new_dataset)
                    except Exception as exc:
                        if getattr(getattr(exc,'response',None),'status_code',None) in (403,404):
                            print('New dataset is still being initialized',flush=True);time.sleep(10);continue
                        raise
                    version=next((v for v in created.get('versions',[]) if v['versionNumber']==1),None)
                    status=version.get('status') if version else 'not listed yet'
                    print('New dataset version 1:',status,flush=True)
                    if str(status).lower()=='ready':
                        assert created['isPrivate'] is True and created['currentVersionNumber']==1
                        files=api.dataset_list_files(args.new_dataset,page_size=100).to_dict()
                        expected=json.loads((ART/'chunk_dataset_update_manifest.json').read_text())['files']
                        actual={f['name']:f['totalBytes'] for f in files['datasetFiles']}
                        assert actual=={k:v['bytes'] for k,v in expected.items()},actual
                        (AUDIT/'new_dataset_view.json').write_text(json.dumps(created,default=str,indent=2))
                        (AUDIT/'new_dataset_files.json').write_text(json.dumps(files,default=str,indent=2))
                        assert view(api)['currentVersionNumber']==1
                        print('NEW DATASET READY: 17 files match; private; original dataset unchanged',flush=True)
                        return
                    if any(s in str(status).lower() for s in ['error','fail']):raise RuntimeError(str(status))
                    time.sleep(10)
                raise TimeoutError('New dataset still processing; do not create again')
            return
        receipt=AUDIT/'publish_receipt.json'
        notes='Append verified VI part-007 (95,938), EN part-001 (14), ZH part-001 (174,453). Preserve all nine baseline chunk files and IDs; merge novel hash groups; retain 196 aliases separately for mapping review.'
        if args.publish:
            if args.structured and receipt.exists():
                failed=json.loads(receipt.read_text())
                assert failed.get('error')=='Incompatible Dataset Type' and current['currentVersionNumber']==1
                receipt.rename(AUDIT/f'failed_publish_receipt_{int(time.time())}.json')
            assert not receipt.exists(),'Publication already recorded; use --wait, never submit twice'
            assert current['currentVersionNumber']==before['currentVersionNumber']==1,'Dataset changed since snapshot'
            assert current['isPrivate']==before['isPrivate'] is True
            files=api.dataset_list_files(REF,page_size=100).to_dict()
            old=json.loads((AUDIT/'before_files.json').read_text())
            assert {(x['name'],x['totalBytes']) for x in files['datasetFiles']}=={(x['name'],x['totalBytes']) for x in old['datasetFiles']}
            (AUDIT/'publication_intent.json').write_text(json.dumps(dict(ref=REF,from_version=1,expected_version=2,version_notes=notes,delete_old_versions=False),indent=2))
            temp=AUDIT/'upload_tmp';temp.mkdir(exist_ok=True);tempfile.tempdir=str(temp)
            result=structured_publish(api,folder,notes,args.by_id) if args.structured else api.dataset_create_version(str(folder),version_notes=notes,quiet=False,convert_to_csv=False,
                delete_old_versions=False,dir_mode='tar')
            receipt.write_text(json.dumps(result.to_dict(),default=str,indent=2))
            assert not result.error,result.error
            print('VERSION REQUEST ACCEPTED',flush=True)
        if args.wait:
            for attempt in range(120):
                current=view(api);versions=current.get('versions',[])
                v2=next((v for v in versions if v['versionNumber']==2),None)
                status=v2.get('status') if v2 else 'not listed yet'
                print('Version 2:',status,flush=True)
                if v2 and str(status).lower()=='ready':
                    assert current['currentVersionNumber']==2 and current['isPrivate'] is True
                    assert any(v['versionNumber']==1 for v in versions),'Old version unexpectedly missing'
                    files=api.dataset_list_files(REF,page_size=100).to_dict()
                    expected=json.loads((ART/'chunk_dataset_update_manifest.json').read_text())['files']
                    actual={f['name']:f['totalBytes'] for f in files['datasetFiles']}
                    assert actual=={k:v['bytes'] for k,v in expected.items()},(actual,set(expected))
                    (AUDIT/'after_view.json').write_text(json.dumps(current,default=str,indent=2))
                    (AUDIT/'after_files.json').write_text(json.dumps(files,default=str,indent=2))
                    print('LIVE VERSION 2 READY: all file paths/sizes match; private; version 1 retained',flush=True)
                    return
                if v2 and any(s in str(status).lower() for s in ['error','fail']):raise RuntimeError('Dataset ingestion failed: '+str(status))
                time.sleep(10)
            raise TimeoutError('Version processing has not completed; do not resubmit')


if __name__=='__main__':main()
