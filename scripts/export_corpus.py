"""Export parsed documents to one parquet per language (CPU only, read-only).

Reads every local checkpoint of the chosen languages: crawl shards
(<root>/<lang>/shard-*/state.sqlite) and the Europe PMC store for EN. Documents
archived to cloud storage keep only a stub locally; their full text is read
back from the batch documents.jsonl.gz (SHA-256 checked) into a temp folder.
Checkpoints are opened read-only, so a running crawl is not disturbed.

    uv run python scripts/export_corpus.py --langs en zh
"""
import argparse
import gzip
import itertools
import json
import sqlite3
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from storage import command, destination_root, sha256

SCHEMA = pa.schema([
    ('doc_id', pa.int64()), ('lang', pa.string()), ('url', pa.string()), ('host', pa.string()),
    ('title', pa.string()), ('status', pa.string()), ('parser_version', pa.string()),
    ('source', pa.string()), ('text', pa.string()),
])
# Where a document came from: the live crawl, Europe PMC, or a recovery store.
RECOVERY_SOURCES = {'cc_v1': 'cc', 'wayback_v1': 'wayback', 'pharmacity_v1': 'pharmacity'}
# Pages that parsed but are not articles; never worth indexing.
SKIP_STATUS = {'homepage', 'redirected_home', 'non_html', 'http_error', 'fetch_error',
               'source_error', 'empty_content', 'empty_response', 'unknown_template', 'not_found'}


def checkpoints(root, lang):
    for path in sorted((root / lang).glob('shard-*/state.sqlite')):
        yield path, 'jobs'
    # Retry runs (scripts/retry_plan.py) come after the shards: a failed original page
    # is skipped, so the retried page of the same doc_id is the one exported.
    for path in sorted((root / lang).glob('retry_*/shard-*/state.sqlite')):
        yield path, 'jobs'
    if (root / lang / 'europepmc/state.sqlite').is_file():
        yield root / lang / 'europepmc/state.sqlite', 'items'


def recovered_documents(root, lang):
    """Pages recovered from Common Crawl (cc_v1, scripts/cc_fetch.py), the Internet
    Archive (wayback_v1, scripts/wayback_fetch.py) and the announced Pharmacity address
    (pharmacity_v1, scripts/pharmacity_fetch.py); their doc_ids are pending locally."""
    for name, source_name in RECOVERY_SOURCES.items():
        path = root / name / 'documents.jsonl.gz'
        if not path.is_file():
            continue
        with gzip.open(path, 'rt', encoding='utf-8') as source:
            for line in source:
                document = json.loads(line)
                if document.get('lang') == lang:
                    yield source_name, document


def remote_batch_documents(remote, scratch):
    """Documents of a shard that ran on another machine: batches in cloud storage.

    <remote> is the batches folder (e.g. gdrive:MedXIR/vi/runs/<plan>/shard-001-of-002/batches).
    Each batch's documents.jsonl.gz is checked against its manifest before reading.
    """
    for entry in command('lsf', '--dirs-only', remote).decode().split():
        batch = f"{remote.rstrip('/')}/{entry.rstrip('/')}"
        manifest = json.loads(command('cat', f'{batch}/manifest.json'))
        target = scratch / f'{entry.rstrip("/")}.jsonl.gz'
        command('copyto', f'{batch}/documents.jsonl.gz', target)
        if sha256(target) != manifest['files']['documents.jsonl.gz']['sha256']:
            raise ValueError(f'Corrupted remote document batch: {batch}')
        with gzip.open(target, 'rt', encoding='utf-8') as source:
            for line in source:
                yield json.loads(line)
        target.unlink()


def document_text(document):
    text = document.get('text') or ''
    if not text.strip():
        text = '\n\n'.join(b['text'] for b in document.get('blocks', []) if b.get('text'))
    return text.strip()


def local_documents(db, table):
    cursor = db.execute(f'SELECT document FROM {table} WHERE document IS NOT NULL')
    while rows := cursor.fetchmany(2000):
        for (raw,) in rows:
            document = json.loads(raw)
            if '_archived' not in document:
                yield document


def archived_documents(db, config, scratch):
    """Current documents whose full text lives only in cloud batches."""
    # Retry checkpoints are created without the archive column; they never archive.
    if 'archive' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
        return
    current = {}
    for doc_id, batch_id in db.execute(
            "SELECT doc_id,archive FROM jobs WHERE archive IS NOT NULL "
            "AND json_extract(document,'$._archived') IS NOT NULL"):
        current.setdefault(batch_id, set()).add(doc_id)
    if not current:
        return
    if not config:
        raise ValueError(f'{len(current)} archived batches but no storage config')
    root = destination_root(config).rstrip('/')
    manifests = {batch_id: json.loads(manifest) for batch_id, manifest in db.execute(
        'SELECT batch_id,manifest FROM storage_batches') if batch_id in current}

    def download(batch_id):
        manifest = manifests[batch_id]
        target = scratch / f'{batch_id}.jsonl.gz'
        command('copyto', f"{root}/{manifest['path']}/documents.jsonl.gz", target)
        if sha256(target) != manifest['files']['documents.jsonl.gz']['sha256']:
            raise ValueError(f'Corrupted document batch: {batch_id}')
        return target

    with ThreadPoolExecutor(4) as pool:
        for batch_id, future in [(b, pool.submit(download, b)) for b in sorted(current)]:
            path = future.result()
            with gzip.open(path, 'rt', encoding='utf-8') as source:
                for line in source:
                    document = json.loads(line)
                    if document['doc_id'] in current[batch_id]:
                        yield document
            path.unlink()


def export(root, lang, output, min_chars, scratch, remote=None):
    config_path = root / f'storage.{lang}.json'
    config = json.loads(config_path.read_text()) if config_path.is_file() else None
    seen, stats, buffer = set(), Counter(), {name: [] for name in SCHEMA.names}
    with pq.ParquetWriter(output, SCHEMA, compression='zstd') as writer:
        def flush():
            if buffer['doc_id']:
                writer.write_table(pa.table(buffer, schema=SCHEMA))
                for column in buffer.values():
                    column.clear()

        for path, table in checkpoints(root, lang):
            print(f'{lang}: reading {path}', flush=True)
            db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
            documents = local_documents(db, table)
            if table == 'jobs':
                documents = (d for source in (documents, archived_documents(db, config, scratch))
                             for d in source)
            source_name = 'europepmc' if table == 'items' else 'crawl'
            for document in documents:
                status, text = document.get('status') or '', document_text(document)
                if document['doc_id'] in seen:
                    stats['duplicate'] += 1
                elif status in SKIP_STATUS or len(text) < min_chars:
                    stats[f'skipped:{status or "none"}'] += 1
                else:
                    seen.add(document['doc_id'])
                    stats['exported'] += 1
                    stats[f'source:{source_name}'] += 1
                    stats['chars'] += len(text)
                    for name, value in zip(SCHEMA.names, (
                            document['doc_id'], lang, document.get('url') or '',
                            document.get('host') or '', document.get('title') or '', status,
                            document.get('parser_version') or '', source_name, text)):
                        buffer[name].append(value)
                    if len(buffer['doc_id']) >= 5000:
                        flush()
            db.close()
        # Another machine's crawl shard before the recovery stores: a live page wins over an archived copy.
        recovered = recovered_documents(root, lang)
        if remote:
            remote_pages = (('crawl', document) for document in remote_batch_documents(remote, scratch))
            recovered = itertools.chain(remote_pages, recovered)
        for source_name, document in recovered:
            # Same accept/skip rules as local pages; duplicates are dropped by doc_id.
            status, text = document.get('status') or '', document_text(document)
            if document['doc_id'] in seen:
                stats['duplicate'] += 1
            elif status in SKIP_STATUS or len(text) < min_chars:
                stats[f'skipped:{source_name}:{status or "none"}'] += 1
            else:
                seen.add(document['doc_id'])
                stats['exported'] += 1
                stats[f'source:{source_name}'] += 1
                stats['chars'] += len(text)
                for name, value in zip(SCHEMA.names, (
                        document['doc_id'], lang, document.get('url') or '',
                        document.get('host') or '', document.get('title') or '', status,
                        document.get('parser_version') or '', source_name, text)):
                    buffer[name].append(value)
                if len(buffer['doc_id']) >= 5000:
                    flush()
        flush()
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=Path('artifacts/recrawl_20261015'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/corpus_v1'))
    parser.add_argument('--langs', nargs='+', choices=['vi', 'en', 'zh'], required=True)
    parser.add_argument('--min-chars', type=int, default=30)
    parser.add_argument('--remote', action='append', default=[], metavar='LANG=BATCHES',
                        help='Batches folder of a shard run elsewhere, e.g. vi=gdrive:MedXIR/vi/runs/<plan>/shard-001-of-002/batches')
    args = parser.parse_args()
    remotes = dict(item.split('=', 1) for item in args.remote)
    args.output.mkdir(parents=True, exist_ok=True)
    for lang in args.langs:
        target = args.output / f'{lang}.parquet'
        temporary = target.with_suffix('.parquet.tmp')
        with tempfile.TemporaryDirectory(prefix='medxir-export-') as scratch:
            stats = export(args.root, lang, temporary, args.min_chars, Path(scratch), remotes.get(lang))
        temporary.replace(target)
        print(f'{target}: {stats["exported"]:,} documents, {stats["chars"]:,} chars')
        for key in sorted(k for k in stats if k not in ('exported', 'chars')):
            print(f'  {key:30} {stats[key]:>10,}')


if __name__ == '__main__':
    main()
