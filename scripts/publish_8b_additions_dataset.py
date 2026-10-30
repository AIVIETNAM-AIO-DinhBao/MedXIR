"""Publish one verified private 8B additions dataset with bounded local staging."""
import argparse
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.kaggle.accounts import account_session
from build_8b_delta_notebooks import FAMILIES
import publish_chunk_dataset_update as uploader


def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def stream_verified_file(fetch_url, target, meta, attempts=8):
    """Bounded-memory downloads; resume disconnects but accept only the full expected SHA."""
    import requests
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    failures, deadline = 0, time.monotonic() + 3 * 3600
    while failures < attempts and time.monotonic() < deadline:
        if target.exists() and target.stat().st_size == meta['bytes'] and sha(target) == meta['sha256']:
            return
        start = target.stat().st_size if target.exists() and target.stat().st_size < meta['bytes'] else 0
        mode = None
        try:
            headers = {'Range': f'bytes={start}-'} if start else {}
            with requests.get(fetch_url(), stream=True, timeout=(15, 90), headers=headers) as response:
                response.raise_for_status()
                if start and response.status_code == 206:
                    assert response.headers.get('Content-Range', '').startswith(f'bytes {start}-')
                    mode = 'ab'
                else:
                    assert response.status_code == 200
                    mode = 'wb'  # Server ignored Range; never append a complete body to a partial file.
                with target.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=8 * 1024**2):
                        if chunk:
                            handle.write(chunk)
                            if handle.tell() > meta['bytes']:
                                raise ValueError('Remote file larger than declared size')
            if target.stat().st_size != meta['bytes']:
                raise OSError('Incomplete download')
            if sha(target) != meta['sha256']:
                target.write_bytes(b'')  # A corrupt prefix must not be reused by the next attempt.
                raise ValueError('Download checksum mismatch')
            print('DOWNLOADED + SHA VERIFIED:', target.name, meta['bytes'], flush=True)
            return
        except (requests.RequestException, OSError, ValueError) as exc:
            print('Streaming download retry:', target.name, type(exc).__name__, flush=True)
            size_now = target.stat().st_size if target.exists() else 0
            made_progress = size_now > start and (start == 0 or mode == 'ab')
            failures = 0 if made_progress else failures + 1
            if failures >= attempts:
                raise RuntimeError('Could not verify download: ' + target.name) from None
            time.sleep(5)
    raise TimeoutError('Download deadline reached: ' + target.name)


def publish(family):
    audit = ROOT / 'artifacts/embedding_8b_inventory_20261030/releases' / family
    audit.mkdir(parents=True, exist_ok=True)
    report = json.loads((ROOT / f'runs/smoke/{family}-repack-20261030-v1/artifacts/vector_repack_validation.json').read_text())
    assert report['complete'] and report['model'] == FAMILIES[family][0] and report['dim'] == 4096
    assert report['chunks'] == 270405 and report['all_ids_match_corpus_part_order'] and report['vectors_bitwise_preserved']
    assert len(report['files']) == 7
    # Kaggle's resumable cache embeds the full upload path in its filename.
    # Keep both paths short enough for Windows' default MAX_PATH limit.
    short_root = ROOT.parent / '_8bjob' / ('o' if family == 'octen8b' else 'q')
    folder = short_root / 'p'
    missing = []
    needed = 0
    for name, meta in report['files'].items():
        p = folder / name
        assert p.resolve().is_relative_to(folder.resolve()), name
        if not p.is_file() or p.stat().st_size != meta['bytes'] or sha(p) != meta['sha256']:
            missing.append('vector_payload/' + name)
            # The installed CLI truncates the destination before streaming a retry.
            needed += max(0, meta['bytes'] - (p.stat().st_size if p.is_file() else 0))
    if missing:
        assert shutil.disk_usage(ROOT).free > needed + 512 * 1024**2, 'Insufficient staging disk space; leave existing files intact'
        # Installed Kaggle kernels_output writes response.content (whole file in RAM).
        # Use the same authorized SDK output URLs, streamed in 8 MiB chunks instead.
        with account_session('ntklinhfitus'):
            from kaggle.api.kaggle_api_extended import KaggleApi
            from kagglesdk.kernels.types.kernels_api_service import ApiListKernelSessionOutputRequest
            api = KaggleApi(); api.authenticate()
            def urls():
                request = ApiListKernelSessionOutputRequest()
                request.user_name = 'ntklinhfitus'
                request.kernel_slug = f'medxir-{family}-repack-20261030'
                api._set_paging(request, 100, None)
                with api.build_kaggle_client() as client:
                    response = client.kernels.kernels_api_client.list_kernel_session_output(request)
                assert not response.next_page_token, 'Unexpected extra output page'
                return {f.file_name: f.url for f in response.files}
            for remote_name in missing:
                name = remote_name.removeprefix('vector_payload/')
                stream_verified_file(lambda: urls()[remote_name], folder / name, report['files'][name])
    for name, meta in report['files'].items():
        p = folder / name
        assert p.stat().st_size == meta['bytes'] and sha(p) == meta['sha256'], name
    print('VERIFIED local staging:', family, '7 files / 270405 rows', flush=True)
    ref = 'ntklinhfitus/medxir-vectors-' + ('qwen3-8b' if family == 'qwen8b' else 'octen-8b') + '-additions'
    receipt = short_root / 'a/new_dataset_publish_receipt.json'
    uploader.AUDIT = short_root / 'a'
    uploader.AUDIT.mkdir(parents=True, exist_ok=True)
    with account_session('ntklinhfitus'):
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi(); api.authenticate()
        # This connection has repeatedly dropped mid-file. The SDK resumes from
        # its saved server offset; allow more attempts before giving up.
        api.MAX_UPLOAD_RESUME_ATTEMPTS = 100
        if not receipt.exists():
            # Record intent before the only mutating call; never overwrite an existing dataset.
            try:
                existing = uploader.view(api, ref)
            except Exception as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status not in (403, 404):
                    raise
                existing = None
            if existing is not None:
                raise RuntimeError('Dataset already exists without a local creation receipt. Verify it; do not create or overwrite it again.')
            (audit / 'publication_intent.json').write_text(json.dumps(dict(dataset=ref, private=True, chunks=270405, model=report['model']), indent=2))
            # The SDK may print resumable URLs in network exceptions. Keep those
            # in the private, temporary cache rather than exposing them in logs.
            try:
                with (uploader.AUDIT / 'upload.log').open('a', encoding='utf-8') as log, redirect_stdout(log):
                    uploader.structured_publish(api, folder, '', new_ref=ref, upload_report=report,
                        description=f"{report['model']} incremental additions for medxir-chunk-corpus-v2. 270,405 float16, 4096-dimensional L2-normalized vectors in VI part 007, EN part 001 and ZH part 001 with ordered chunk_id sidecars. Existing vectors unchanged. Model contract and source checksums in embed_manifest.json.")
            except Exception as exc:
                raise RuntimeError('Dataset upload failed (' + type(exc).__name__ + '); local resumable cache retained') from None
        else:
            previous = json.loads(receipt.read_text())
            assert not previous.get('error'), previous
        (audit / 'new_dataset_publish_receipt.json').write_text(receipt.read_text(encoding='utf-8'), encoding='utf-8')
        for _ in range(180):
            try:
                view = uploader.view(api, ref)
            except Exception as exc:
                if getattr(getattr(exc, 'response', None), 'status_code', None) in (403, 404):
                    print(ref, 'initializing after accepted creation', flush=True)
                    time.sleep(10)
                    continue
                raise
            version = next((v for v in view.get('versions', []) if v['versionNumber'] == 1), None)
            status = version.get('status') if version else 'initializing'
            print(ref, 'version 1:', status, flush=True)
            if str(status).lower() == 'ready':
                assert view['isPrivate'] and view['currentVersionNumber'] == 1
                files = api.dataset_list_files(ref, page_size=100).to_dict()
                assert {f['name']: f['totalBytes'] for f in files['datasetFiles']} == {n: m['bytes'] for n, m in report['files'].items()}
                (audit / 'dataset_view.json').write_text(json.dumps(view, indent=2, default=str))
                (audit / 'dataset_files.json').write_text(json.dumps(files, indent=2, default=str))
                return
            assert not any(s in str(status).lower() for s in ('error', 'fail')), status
            time.sleep(10)
        raise TimeoutError('Creation accepted but dataset still processing; do not create it again')


def cleanup(family):
    report = json.loads((ROOT / f'runs/smoke/{family}-verify-20261030-v1/artifacts/published_vectors_validation.json').read_text())
    assert report['complete'] and report['rows'] == 270405 and report['all_file_checksums_verified']
    expected_ref = 'ntklinhfitus/medxir-vectors-' + ('qwen3-8b' if family == 'qwen8b' else 'octen-8b') + '-additions'
    assert report['dataset'] == expected_ref and report['model'] == FAMILIES[family][0] and report['dim'] == 4096
    audit = (ROOT / 'artifacts/embedding_8b_inventory_20261030/releases' / family).resolve()
    base = (ROOT.parent / '_8bjob' / ('o' if family == 'octen8b' else 'q')).resolve()
    assert base.is_relative_to(ROOT.parent.resolve()) and base.parent == (ROOT.parent / '_8bjob').resolve()
    for name in ('p', 'a'):
        target = (base / name).resolve()
        assert target.parent == base and target.name == name
        if target.exists():
            shutil.rmtree(target)  # Only this job's stage/cache, after remote checksum verification.
    (audit / 'STATE.json').write_text(json.dumps(dict(status='COMPLETE', dataset=report['dataset'],
        remote_verification=True, local_staging_removed=True, rows=270405), indent=2))
    print('COMPLETE:', report['dataset'], 'remote verified; local staging released', flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', choices=FAMILIES, required=True)
    p.add_argument('--cleanup', action='store_true')
    args = p.parse_args()
    cleanup(args.model) if args.cleanup else publish(args.model)
