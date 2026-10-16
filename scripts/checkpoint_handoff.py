"""Publish or fetch a verified crawler checkpoint for a stopped shard.

The raw/document batches remain under the language storage root.  This tool
only transfers the SQLite state that tells another machine which IDs are done.
"""
import argparse
import fcntl
import hashlib
import json
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from storage import Storage, command, destination_root, sha256, validate_destination


def utc_now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


@contextmanager
def shard_lock(root):
    """Refuse to snapshot or replace a shard while its crawler owns the lock."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f'Crawler is still running for {root}; stop it before handoff') from error
        yield


def checkpoint_identity(path):
    with sqlite3.connect(path) as db:
        result = db.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise ValueError(f'Invalid SQLite checkpoint: integrity_check={result}')
        row = db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if not row:
            raise ValueError('Checkpoint has no identity metadata')
        identity = json.loads(row[0])
        saved = db.execute('SELECT count(*) FROM jobs WHERE page IS NOT NULL').fetchone()[0]
        planned = db.execute('SELECT count(*) FROM jobs').fetchone()[0]
    required = {'plan_sha256', 'shard_count', 'shard_id'}
    if not required <= identity.keys():
        raise ValueError('Checkpoint identity is incomplete')
    return identity, saved, planned


def snapshot_checkpoint(source, destination):
    """Create a standalone backup that never relies on source WAL files."""
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target_db:
        source_db.backup(target_db)
    return checkpoint_identity(destination)


def handoff_prefix(identity):
    return (f"handoffs/{identity['plan_sha256']}/"
            f"shard-{identity['shard_id']:03d}-of-{identity['shard_count']:03d}")


def remote_path(config, relative):
    return destination_root(config).rstrip('/') + '/' + relative


def config_hash(path):
    canonical = json.dumps(json.loads(path.read_text()), sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(canonical).hexdigest()


def publish(args):
    if not (args.root / 'state.sqlite').is_file():
        raise ValueError(f'No checkpoint at {args.root}')
    config = json.loads(args.config.read_text())
    validate_destination(config)
    with shard_lock(args.root):
        # This assigns every completed URL to a verified cloud batch before the
        # checkpoint is handed to another machine.  The recipient can recover
        # archived raw data from Drive without copying local raw/ or batches/.
        storage = Storage(args.root, args.config, args.batch_docs, args.batch_mb, args.prune_local)
        storage.flush(force=True)
        with tempfile.TemporaryDirectory(prefix='medxir-handoff-') as temporary:
            snapshot = Path(temporary) / 'state.sqlite'
            identity, saved, planned = snapshot_checkpoint(args.root / 'state.sqlite', snapshot)
            if args.plan and sha256(args.plan) != identity['plan_sha256']:
                raise ValueError('Plan checksum does not match checkpoint identity')
            prefix = handoff_prefix(identity)
            command('mkdir', remote_path(config, prefix))
            handoff_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex}"
            relative = f'{prefix}/{handoff_id}'
            state_relative = relative + '/state.sqlite'
            manifest = dict(format_version=1, created_at=utc_now(), handoff_id=handoff_id,
                            identity=identity, saved=saved, planned=planned,
                            state=dict(path=state_relative, bytes=snapshot.stat().st_size,
                                       sha256=sha256(snapshot)),
                            storage_config_sha256=config_hash(args.config))
            manifest_path = Path(temporary) / 'manifest.json'
            save_json(manifest_path, manifest)
            manifest_info = dict(path=relative + '/manifest.json', sha256=sha256(manifest_path))
            # The immutable payload is fully verified before latest.json points to it.
            command('copyto', snapshot, remote_path(config, state_relative), '--immutable')
            storage.verify_remote(remote_path(config, state_relative), manifest['state']['sha256'])
            command('copyto', manifest_path, remote_path(config, manifest_info['path']), '--immutable')
            storage.verify_remote(remote_path(config, manifest_info['path']), manifest_info['sha256'])
            latest = dict(format_version=1, updated_at=utc_now(), manifest=manifest_info)
            latest_path = Path(temporary) / 'latest.json'
            save_json(latest_path, latest)
            command('copyto', latest_path, remote_path(config, prefix + '/latest.json'))
            storage.verify_remote(remote_path(config, prefix + '/latest.json'), sha256(latest_path))
    print(json.dumps(dict(published=True, remote=remote_path(config, prefix), saved=saved,
                          planned=planned, handoff_id=handoff_id), ensure_ascii=False))


def fetch(args):
    if (args.root / 'state.sqlite').exists():
        raise ValueError(f'{args.root / "state.sqlite"} already exists; refuse to replace a local checkpoint')
    if not args.plan.is_file():
        raise ValueError(f'Missing plan: {args.plan}')
    config = json.loads(args.config.read_text())
    validate_destination(config)
    expected_plan = sha256(args.plan)
    identity = dict(plan_sha256=expected_plan, shard_count=args.shard_count, shard_id=args.shard_id)
    prefix = handoff_prefix(identity)
    with shard_lock(args.root), tempfile.TemporaryDirectory(prefix='medxir-handoff-') as temporary:
        temporary = Path(temporary)
        latest_path = temporary / 'latest.json'
        command('copyto', remote_path(config, prefix + '/latest.json'), latest_path)
        latest = json.loads(latest_path.read_text())
        manifest_info = latest.get('manifest', {})
        if not manifest_info.get('path') or not manifest_info.get('sha256'):
            raise ValueError('Invalid handoff latest.json')
        manifest_path = temporary / 'manifest.json'
        command('copyto', remote_path(config, manifest_info['path']), manifest_path)
        if sha256(manifest_path) != manifest_info['sha256']:
            raise ValueError('Handoff manifest checksum mismatch')
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('identity') != identity:
            raise ValueError('Handoff is for another plan or shard')
        if manifest.get('storage_config_sha256') != config_hash(args.config):
            raise ValueError('Handoff uses a different storage configuration')
        state = manifest.get('state', {})
        if not state.get('path') or not state.get('sha256'):
            raise ValueError('Invalid handoff state manifest')
        downloaded = temporary / 'state.sqlite'
        command('copyto', remote_path(config, state['path']), downloaded)
        if downloaded.stat().st_size != state.get('bytes') or sha256(downloaded) != state['sha256']:
            raise ValueError('Handoff checkpoint checksum mismatch')
        downloaded_identity, saved, planned = checkpoint_identity(downloaded)
        if downloaded_identity != identity:
            raise ValueError('Downloaded checkpoint identity mismatch')
        target = args.root / 'state.sqlite'
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded.replace(target)
    print(json.dumps(dict(fetched=True, root=str(args.root), saved=saved, planned=planned,
                          handoff_id=manifest['handoff_id']), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['publish', 'fetch'])
    parser.add_argument('--root', type=Path, required=True, help='Language shard directory')
    parser.add_argument('--config', type=Path, required=True, help='Language storage config')
    parser.add_argument('--plan', type=Path, required=True, help='Unchanged language plan')
    parser.add_argument('--shard-count', type=int, required=True)
    parser.add_argument('--shard-id', type=int, required=True)
    parser.add_argument('--batch-docs', type=int, default=2000)
    parser.add_argument('--batch-mb', type=int, default=128)
    parser.add_argument('--prune-local', action='store_true')
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_id < args.shard_count:
        parser.error('Invalid shard')
    if args.batch_docs < 1 or args.batch_mb < 1:
        parser.error('Invalid batch limits')
    try:
        if args.action == 'publish':
            publish(args)
        else:
            fetch(args)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
