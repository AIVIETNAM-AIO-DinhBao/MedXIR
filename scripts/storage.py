"""Batch storage for the crawler. Credentials stay in rclone's local config."""
import argparse
import configparser
import gzip
import hashlib
import json
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def rclone():
    binary = shutil.which('rclone') or str(Path.home() / '.local/bin/rclone')
    if not Path(binary).is_file():
        raise ValueError('Install rclone first: https://rclone.org/install/')
    return binary


def command(*args):
    # Capture output: config commands must never print credentials.
    result = subprocess.run([rclone(), *map(str, args)], capture_output=True, timeout=600)
    if result.returncode:
        raise ValueError(f'rclone {args[0]} failed; local data retained. '
                         + result.stderr.decode(errors='replace')[-1500:])
    return result.stdout


def remote_settings(remote):
    settings = configparser.ConfigParser(interpolation=None)
    settings.read_string(command('config', 'redacted', remote).decode())
    if remote not in settings or settings[remote].get('type') not in ('onedrive', 'drive'):
        raise ValueError(f'{remote}: must be a directly configured Google Drive/OneDrive remote')
    # Reject environment overrides, which could redirect an otherwise bound remote.
    import os
    if any(key.startswith(('RCLONE_CONFIG_', 'RCLONE_ONEDRIVE_', 'RCLONE_DRIVE_'))
           for key in os.environ):
        raise ValueError('Remove RCLONE_CONFIG_*/RCLONE_ONEDRIVE_*/RCLONE_DRIVE_* overrides')
    return settings[remote]


def destination_root(config):
    if config.get('backend') == 'drive':
        # Pin the folder ID: identical names on two accounts must not mean two destinations.
        remote = config['remote'].partition(':')[0]
        return (f"{remote},root_folder_id={config['folder_id']},"
                f"team_drive={config.get('team_drive', '')}:")
    return config['remote'].rstrip('/')


def folder_info(destination):
    """Stat of a Drive root is synthetic; get its ID from the parent's listing."""
    remote, _, folder = destination.partition(':')
    parent, _, name = folder.strip('/').rpartition('/')
    entries = json.loads(command('lsjson', remote + ':' + parent, '--dirs-only', '--no-modtime'))
    matches = [entry for entry in entries if entry['Name'] == name]
    if not matches:
        raise ValueError('directory not found')
    if len(matches) != 1:
        raise ValueError('Multiple destination folders with the same name; resolve duplicates first')
    return matches[0]


def validate_destination(config):
    remote, separator, folder = config['remote'].partition(':')
    if not separator or not folder.strip('/') or any(p in ('.', '..') for p in folder.split('/')):
        raise ValueError('Storage remote must include a folder, e.g. gdrive:MedXIR')
    settings = remote_settings(remote)
    if config.get('backend') == 'drive':
        folder_id = config.get('folder_id', '')
        if settings['type'] != 'drive' or not folder_id or any(
                c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in folder_id):
            raise ValueError('Invalid Google Drive backend/folder_id in shared storage.json')
        team_drive = config.get('team_drive', '')
        if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in team_drive):
            raise ValueError('Invalid team_drive in shared storage.json')
        # The inline root_folder_id controls every request, independently of My Drive paths.
        command('lsjson', destination_root(config), '--dirs-only', '--no-modtime')
    elif (settings['type'] != 'onedrive' or not config.get('drive_id')
          or settings.get('drive_id') != config['drive_id']):
        raise ValueError('OneDrive drive_id mismatch/missing: use the shared storage.json')


def prepare_destination(config, plan, shard_count):
    """Run once before machines start: Drive permits concurrent duplicate folders."""
    validate_destination(config)
    prefix = destination_root(config).rstrip('/') + '/runs/' + sha256(plan)
    for shard_id in range(shard_count):
        path = f'{prefix}/shard-{shard_id:03d}-of-{shard_count:03d}/batches'
        command('mkdir', path)
    print(f'Prepared shared cloud directories for {shard_count} shards.', flush=True)


class Storage:
    def __init__(self, root, config=None, batch_docs=10000, batch_mb=256, prune=False):
        self.root = Path(root)
        self.config = json.loads(Path(config).read_text()) if config else None
        self.batch_docs = batch_docs
        self.batch_bytes = batch_mb * 1024 * 1024
        self.prune = prune
        self._verified_files = {}
        self._raw_archive = None
        with self.connect() as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'archive' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN archive TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS archived_jobs ON jobs(archive) WHERE archive IS NOT NULL')
            db.execute('CREATE INDEX IF NOT EXISTS unpacked_jobs ON jobs(doc_id) '
                       'WHERE page IS NOT NULL AND archive IS NULL')
            db.execute('CREATE TABLE IF NOT EXISTS storage_batches('
                       'batch_id TEXT PRIMARY KEY, manifest TEXT, verified INTEGER DEFAULT 0)')
            self.identity = json.loads(db.execute(
                "SELECT value FROM metadata WHERE key='identity'").fetchone()[0])
            self.prefix = (f"runs/{self.identity['plan_sha256']}/"
                           f"shard-{self.identity['shard_id']:03d}-of-{self.identity['shard_count']:03d}")
            if self.config:
                self.validate()
                if self.config.get('backend') == 'drive':
                    try:
                        command('lsjson', self.remote_path(self.prefix + '/batches'), '--stat')
                    except ValueError as error:
                        raise ValueError('Prepare shared shard directories once before crawling: '
                                         'storage.py prepare --plan PLAN --shard-count N') from error
                binding = json.dumps(self.config, sort_keys=True)
                previous = db.execute("SELECT value FROM metadata WHERE key='storage'").fetchone()
                if previous and previous[0] != binding:
                    raise ValueError('Checkpoint is already bound to another storage destination')
                db.execute("INSERT OR IGNORE INTO metadata VALUES ('storage', ?)", [binding])

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'state.sqlite', timeout=60)
        try:
            with db:
                yield db
        finally:
            db.close()

    def validate(self):
        validate_destination(self.config)

    def remote_path(self, relative):
        if not self.config:
            raise ValueError('This batch needs cloud storage: pass --storage-config storage.json')
        return destination_root(self.config).rstrip('/') + '/' + relative

    def seal(self, force=False):
        """Write files first, then atomically assign jobs to the immutable batch."""
        with self.connect() as db:
            rows = db.execute('SELECT doc_id,page FROM jobs WHERE page IS NOT NULL '
                              'AND archive IS NULL ORDER BY doc_id LIMIT ?', [self.batch_docs]).fetchall()
        selected, size = [], 0
        for row in rows:
            page = json.loads(row[1])
            path = self.root / page['raw_path'] if page.get('raw_path') else None
            if path and not path.is_file():
                raise ValueError(f'Missing raw: {path}; cannot archive safely')
            size += path.stat().st_size if path else 0
            selected.append(row)
            if size >= self.batch_bytes:
                break
        if not selected or (not force and len(selected) < self.batch_docs and size < self.batch_bytes):
            return False
        # Only load the large document JSON when a batch is actually ready.
        with self.connect() as db:
            selected = [(doc_id, page, db.execute('SELECT document FROM jobs WHERE doc_id=?',
                         [doc_id]).fetchone()[0]) for doc_id, page in selected]
        batch_id = uuid.uuid4().hex
        relative = f'{self.prefix}/batches/{batch_id}'
        folder = self.root / 'batches' / batch_id
        folder.mkdir(parents=True)
        try:
            with tarfile.open(folder / 'raw.tar', 'w') as archive, \
                    gzip.open(folder / 'pages.jsonl.gz', 'wt', encoding='utf-8') as pages, \
                    gzip.open(folder / 'documents.jsonl.gz', 'wt', encoding='utf-8') as documents:
                for doc_id, page_json, document_json in selected:
                    page = json.loads(page_json)
                    if page.get('raw_path'):
                        source = self.root / page['raw_path']
                        # Verify the original raw digest before making it eligible for eviction.
                        with gzip.open(source, 'rb') as raw:
                            digest = hashlib.sha256()
                            while chunk := raw.read(1024 * 1024):
                                digest.update(chunk)
                        if digest.hexdigest() != page['sha256']:
                            raise ValueError(f'Raw SHA-256 mismatch: {source}')
                        archive.add(source, arcname=page['raw_path'], recursive=False)
                    pages.write(page_json + '\n')
                    documents.write(document_json + '\n')
            files = {name: dict(bytes=(folder / name).stat().st_size, sha256=sha256(folder / name))
                     for name in ('raw.tar', 'pages.jsonl.gz', 'documents.jsonl.gz')}
            manifest = dict(format_version=1, batch_id=batch_id, path=relative,
                            created_at=time.time(), identity=self.identity,
                            count=len(selected), files=files)
            (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2))
            with self.connect() as db:
                db.execute('INSERT INTO storage_batches(batch_id,manifest) VALUES (?,?)',
                           [batch_id, json.dumps(manifest)])
                db.executemany('UPDATE jobs SET archive=? WHERE doc_id=?',
                               [(batch_id, row[0]) for row in selected])
        except BaseException:
            # Unregistered partial batch is disposable; source files were never modified.
            with self.connect() as db:
                registered = db.execute('SELECT 1 FROM storage_batches WHERE batch_id=?',
                                        [batch_id]).fetchone()
            if not registered:
                shutil.rmtree(folder)
            raise
        print(f'Sealed batch {batch_id}: {len(selected):,} documents, {size/1024**2:.1f} MiB raw',
              flush=True)
        return True

    def upload(self, manifest):
        self.validate()
        folder = self.root / 'batches' / manifest['batch_id']
        remote = self.remote_path(manifest['path'])
        # A manifest is the commit marker; publish it only after all payloads verified.
        for name, expected in manifest['files'].items():
            path = folder / name
            if sha256(path) != expected['sha256']:
                raise ValueError(f'Local batch corrupted: {path}')
            print(f'Upload {manifest["batch_id"]}/{name}', flush=True)
            command('copyto', path, remote + '/' + name, '--immutable', '--retries', '3')
            self.verify_remote(remote + '/' + name, expected['sha256'])
        command('copyto', folder / 'manifest.json', remote + '/manifest.json', '--immutable')
        self.verify_remote(remote + '/manifest.json', sha256(folder / 'manifest.json'))
        with self.connect() as db:
            db.execute('UPDATE storage_batches SET verified=1 WHERE batch_id=?',
                       [manifest['batch_id']])
        print(f'Verified on cloud storage: {manifest["batch_id"]}', flush=True)

    def verify_remote(self, remote, expected):
        # Verify bytes independently of the backend's advertised checksum algorithms.
        process = subprocess.Popen([rclone(), 'cat', remote, '--retries', '3', '--timeout', '60s'],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        digest = hashlib.sha256()
        try:
            while chunk := process.stdout.read(1024 * 1024):
                digest.update(chunk)
            if process.wait() or digest.hexdigest() != expected:
                raise ValueError(f'Remote SHA-256 mismatch/read failure: {remote}')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()

    def evict(self, manifest):
        """Keep status/provenance locally; full documents and raw remain in the batch."""
        batch_id = manifest['batch_id']
        with self.connect() as db:
            verified = db.execute('SELECT verified FROM storage_batches WHERE batch_id=?',
                                  [batch_id]).fetchone()[0]
            if not verified:
                raise ValueError('Refusing to evict an unverified batch')
            rows = db.execute('SELECT doc_id,page,document FROM jobs WHERE archive=?',
                              [batch_id]).fetchall()
            for doc_id, page_json, doc_json in rows:
                page, doc = json.loads(page_json), json.loads(doc_json)
                if not doc.get('_archived'):
                    compact = {key: doc[key] for key in ('doc_id', 'url', 'host', 'status',
                               'warnings', 'title', 'parser_version') if key in doc}
                    compact.update(_archived=batch_id, has_text=bool(doc.get('text', '').strip()))
                    db.execute('UPDATE jobs SET document=? WHERE doc_id=?',
                               [json.dumps(compact, ensure_ascii=False), doc_id])
            # Commit locators before deleting any file; rerunning eviction is safe.
        for _, page_json, _ in rows:
            page = json.loads(page_json)
            if page.get('raw_path'):
                (self.root / page['raw_path']).unlink(missing_ok=True)
        shutil.rmtree(self.root / 'batches' / batch_id, ignore_errors=True)
        with self.connect() as db:
            db.execute('UPDATE storage_batches SET verified=2 WHERE batch_id=?', [batch_id])
        # Free database pages are reused by future crawl jobs; VACUUM is a manual operation.

    def flush(self, force=False):
        def pending_uploads():
            with self.connect() as db:
                batches = db.execute('SELECT manifest,verified FROM storage_batches WHERE verified<2').fetchall()
            for manifest_json, verified in batches:
                manifest = json.loads(manifest_json)
                if self.config and not verified:
                    self.upload(manifest)
                    verified = 1
                if self.prune and verified == 1:
                    self.evict(manifest)
        pending_uploads()
        while self.seal(force):
            # Drain each batch before sealing more, especially when resuming a backlog.
            pending_uploads()
            if not force:
                break

    def cache_bytes(self):
        total = 0
        for name in ('raw', 'batches'):
            for path in (self.root / name).rglob('*'):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except FileNotFoundError:
                    pass  # Uploader may have just evicted this file.
        return total

    def cached_file(self, manifest, name):
        """Cache only the required payload, never extract a whole raw archive."""
        path = self.root / 'batches' / manifest['batch_id'] / name
        expected = manifest['files'][name]['sha256']
        if path.is_file():
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns, expected)
            if self._verified_files.get(path) == signature or sha256(path) == expected:
                self._verified_files[path] = signature
                return path
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.download')
        command('copyto', self.remote_path(manifest['path']) + '/' + name, temporary)
        if sha256(temporary) != expected:
            temporary.unlink(missing_ok=True)
            raise ValueError(f'Download SHA-256 mismatch: {name}')
        temporary.replace(path)
        stat = path.stat()
        self._verified_files[path] = (stat.st_size, stat.st_mtime_ns, expected)
        return path

    def manifest(self, batch_id):
        with self.connect() as db:
            row = db.execute('SELECT manifest FROM storage_batches WHERE batch_id=?',
                             [batch_id]).fetchone()
        if not row:
            raise ValueError(f'Unknown batch: {batch_id}')
        return json.loads(row[0])

    def raw(self, page, batch_id=None):
        if not page.get('raw_path'):
            return b''
        path = self.root / page['raw_path']
        if path.is_file():
            raw = gzip.decompress(path.read_bytes())
            if hashlib.sha256(raw).hexdigest() != page['sha256']:
                raise ValueError(f'Raw SHA-256 mismatch: {page["doc_id"]}')
            return raw
        previous = getattr(self, '_last_raw_batch', None)
        if previous and previous != batch_id:
            if self._raw_archive:
                self._raw_archive.close()
                self._raw_archive = None
            with self.connect() as db:
                evicted = db.execute('SELECT verified FROM storage_batches WHERE batch_id=?',
                                     [previous]).fetchone()[0] == 2
            if evicted:
                (self.root / 'batches' / previous / 'raw.tar').unlink(missing_ok=True)
        self._last_raw_batch = batch_id
        manifest = self.manifest(batch_id)
        archive_path = self.cached_file(manifest, 'raw.tar')
        if self._raw_archive is None:
            self._raw_archive = tarfile.open(archive_path)
            self._raw_members = {member.name: member for member in self._raw_archive.getmembers()}
        with self._raw_archive.extractfile(self._raw_members[page['raw_path']]) as source:
            raw = gzip.decompress(source.read())
        if hashlib.sha256(raw).hexdigest() != page['sha256']:
            raise ValueError(f'Raw SHA-256 mismatch: {page["doc_id"]}')
        return raw

    def documents(self, manifest):
        path = self.cached_file(manifest, 'documents.jsonl.gz')
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as source:
                for line in source:
                    yield json.loads(line)
        finally:
            with self.connect() as db:
                evicted = db.execute('SELECT verified FROM storage_batches WHERE batch_id=?',
                                     [manifest['batch_id']]).fetchone()[0] == 2
            if evicted:
                path.unlink(missing_ok=True)

    def results(self, db):
        """Full current results, batch by batch; safe after local eviction/reparse."""
        for page_json, document_json in db.execute(
                "SELECT page,document FROM jobs WHERE page IS NOT NULL "
                "AND coalesce(json_extract(document,'$._archived'),'')='' ORDER BY doc_id"):
            yield json.loads(page_json), json.loads(document_json)
        for (batch_id,) in db.execute('SELECT DISTINCT archive FROM jobs WHERE archive IS NOT NULL'):
            current = {doc_id: json.loads(page) for doc_id, page in db.execute(
                "SELECT doc_id,page FROM jobs WHERE archive=? "
                "AND json_extract(document,'$._archived') IS NOT NULL", [batch_id])}
            if not current:
                continue
            for document in self.documents(self.manifest(batch_id)):
                if document['doc_id'] in current:
                    yield current[document['doc_id']], document


def export_remote(config_path, plan_sha, destination):
    """Read all shards without their SQLite files; newest committed doc_id wins."""
    config = json.loads(config_path.read_text())
    validate_destination(config)
    if not plan_sha or len(plan_sha) != 64 or any(c not in '0123456789abcdef' for c in plan_sha):
        raise ValueError('Use --plan-sha with the full SHA-256 of the shared plan')
    root = destination_root(config).rstrip('/') + '/runs/' + plan_sha
    paths = command('lsf', root, '--recursive', '--files-only', '--include', 'manifest.json').decode().splitlines()
    manifests = []
    for path in paths:
        manifest = json.loads(command('cat', root + '/' + path))
        if manifest['identity']['plan_sha256'] != plan_sha:
            raise ValueError(f'Unexpected plan in {path}')
        manifests.append((path.rsplit('/', 1)[0], manifest))
    if not manifests:
        raise ValueError('No committed batches on cloud storage for this plan')
    # Disk-based ID set keeps memory bounded for millions of documents.
    with tempfile.TemporaryDirectory(prefix='medxir-read-') as temporary:
        with sqlite3.connect(Path(temporary) / 'seen.sqlite') as seen, \
                gzip.open(destination, 'wt', encoding='utf-8') as output:
            seen.execute('CREATE TABLE seen(doc_id INTEGER PRIMARY KEY)')
            for folder, manifest in sorted(manifests, key=lambda pair: pair[1]['created_at'], reverse=True):
                path = Path(temporary) / 'documents.jsonl.gz'
                command('copyto', root + '/' + folder + '/documents.jsonl.gz', path)
                if sha256(path) != manifest['files']['documents.jsonl.gz']['sha256']:
                    raise ValueError(f'Corrupted document batch: {folder}')
                with gzip.open(path, 'rt', encoding='utf-8') as source:
                    for line in source:
                        doc_id = json.loads(line)['doc_id']
                        if seen.execute('INSERT OR IGNORE INTO seen VALUES (?)', [doc_id]).rowcount:
                            output.write(line)
                seen.commit()
                path.unlink()
                print(f'Read {folder}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'prepare', 'pack', 'upload', 'status', 'documents', 'raw', 'export-remote'])
    parser.add_argument('--root', type=Path, default=Path('artifacts/crawl/shard-000-of-001'))
    parser.add_argument('--config', type=Path, default=Path('storage.json'))
    parser.add_argument('--remote', default='gdrive:MedXIR')
    parser.add_argument('--batch-docs', type=int, default=10000)
    parser.add_argument('--batch-mb', type=int, default=256)
    parser.add_argument('--prune-local', action='store_true')
    parser.add_argument('--doc-id', type=int)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--plan-sha', help='Plan SHA-256 for export-remote')
    parser.add_argument('--plan', type=Path, default=Path('artifacts/crawl/plan.jsonl'))
    parser.add_argument('--shard-count', type=int, default=1)
    args = parser.parse_args()
    if args.batch_docs < 1 or args.batch_mb < 1 or args.shard_count < 1:
        parser.error('Batch/shard limits must be positive')
    try:
        if args.action == 'init':
            if args.config.exists():
                value = json.loads(args.config.read_text())
                validate_destination(value)
                print('Existing shared storage configuration matches the cloud destination.')
            else:
                remote = args.remote.partition(':')[0]
                settings = remote_settings(remote)
                value = dict(remote=args.remote.rstrip('/'))
                if ':' not in value['remote'] or not value['remote'].partition(':')[2].strip('/'):
                    raise ValueError('--remote must include a folder, e.g. gdrive:MedXIR')
                if settings['type'] == 'drive':
                    try:
                        info = folder_info(value['remote'])
                    except ValueError as error:
                        if 'directory not found' not in str(error):
                            raise
                        command('mkdir', value['remote'])
                        info = folder_info(value['remote'])
                    if not info.get('IsDir') or not info.get('ID'):
                        raise ValueError('Destination must be a Google Drive folder with a stable ID')
                    value.update(backend='drive', folder_id=info['ID'],
                                 team_drive=settings.get('team_drive', ''))
                else:
                    value['drive_id'] = settings.get('drive_id')
                # Validate before persisting the shared non-secret destination.
                validate_destination(value)
                args.config.write_text(json.dumps(value, indent=2) + '\n')
                print(f'Created {args.config}; copy this same file to all machines. No tokens included.')
            return
        if args.action == 'prepare':
            prepare_destination(json.loads(args.config.read_text()), args.plan, args.shard_count)
            return
        if args.action == 'export-remote':
            if not args.destination:
                raise ValueError('Use --destination corpus.jsonl.gz')
            export_remote(args.config, args.plan_sha, args.destination)
            return
        if not (args.root / 'state.sqlite').is_file():
            raise ValueError(f'No crawler checkpoint at {args.root}')
        storage = Storage(args.root, args.config if args.action not in ('pack', 'status') else None,
                          args.batch_docs, args.batch_mb, args.prune_local)
        if args.action in ('pack', 'upload'):
            if args.action == 'pack' and args.prune_local:
                raise ValueError('--prune-local requires upload, never pack-only')
            import fcntl
            with (args.root / '.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                storage.flush(force=True)
        elif args.action == 'status':
            with storage.connect() as db:
                print('Batches:', dict(Counter('verified' if row[0] else 'pending'
                      for row in db.execute('SELECT verified FROM storage_batches'))))
                print('Not yet packed:', db.execute('SELECT count(*) FROM jobs WHERE page IS NOT NULL '
                                                    'AND archive IS NULL').fetchone()[0])
        elif args.action == 'documents':
            if not args.destination:
                raise ValueError('Use --destination output.jsonl.gz')
            with storage.connect() as db, gzip.open(args.destination, 'wt', encoding='utf-8') as output:
                for _, document in storage.results(db):
                    output.write(json.dumps(document, ensure_ascii=False) + '\n')
        elif args.action == 'raw':
            if args.doc_id is None or not args.destination:
                raise ValueError('Use --doc-id ID --destination page.html')
            with storage.connect() as db:
                row = db.execute('SELECT page,archive FROM jobs WHERE doc_id=?', [args.doc_id]).fetchone()
            if not row or not row[0]:
                raise ValueError('doc_id has no saved result')
            args.destination.write_bytes(storage.raw(json.loads(row[0]), row[1]))
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
