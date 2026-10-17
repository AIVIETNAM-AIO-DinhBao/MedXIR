"""Collect the EN PubMed records from Europe PMC instead of pubmed.ncbi.nlm.nih.gov.

NCBI blocks this network for both web pages and E-utilities (redirect to its
misuse page). Europe PMC mirrors MEDLINE (source MED) with title, abstract and
MeSH through a public REST API. One process covers the whole EN plan: a
resumable SQLite checkpoint, batches uploaded and SHA-256 verified like the
crawler's, and a follow-up report of records without abstract or not found.

Test:  uv run python scripts/pubmed_europepmc.py --limit 1000
Full:  uv run python scripts/pubmed_europepmc.py
"""
import argparse
import csv
import gzip
import io
import json
import re
import shutil
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from storage import command, destination_root, sha256, validate_destination, verify_remote

VERSION = 'europepmc-v1'
API = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
HOST = 'pubmed.ncbi.nlm.nih.gov'
PMID_URL = re.compile(r'https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d+)/?')
BATCH_FILES = ('documents.jsonl.gz', 'records.jsonl.gz')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    return re.sub(r'\s+', ' ', value).replace('\x00', '').strip()


def open_state(root, plan):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'state.sqlite', timeout=60)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS items(doc_id INTEGER PRIMARY KEY, pmid TEXT NOT NULL, url TEXT NOT NULL,
                                         status TEXT, document TEXT, record TEXT, batch_id TEXT);
        CREATE TABLE IF NOT EXISTS batches(batch_id TEXT PRIMARY KEY, manifest TEXT NOT NULL,
                                           verified INTEGER NOT NULL DEFAULT 0);''')
    digest = sha256(plan)
    saved = db.execute("SELECT value FROM metadata WHERE key='plan_sha256'").fetchone()
    if saved and saved[0] != digest:
        raise ValueError(f'Checkpoint belongs to plan {saved[0]}, not {plan} ({digest})')
    if not saved:
        rows = []
        for line in plan.open(encoding='utf-8'):
            job = json.loads(line)
            match = PMID_URL.fullmatch(job['url'])
            if job.get('host') != HOST or not match:
                raise ValueError(f'Not a PubMed article URL: {job["doc_id"]} {job["url"]}')
            rows.append((job['doc_id'], match[1], job['url']))
        with db:
            db.executemany('INSERT INTO items(doc_id,pmid,url) VALUES (?,?,?)', rows)
            db.execute("INSERT INTO metadata VALUES ('plan_sha256',?)", [digest])
        print(f'Initialized {len(rows):,} PubMed IDs from {plan}', flush=True)
    return db, digest


def abstract_blocks(markup):
    """Europe PMC marks structured-abstract sections with <h4>; other tags are inline."""
    blocks, pending = [], []
    def flush():
        value = clean(''.join(pending))
        if value:
            blocks.append(('paragraph', value))
        pending.clear()
    for node in BeautifulSoup(markup, 'html.parser').contents:
        name = getattr(node, 'name', None)
        if name in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            flush()
            if heading := clean(node.get_text('')):
                blocks.append(('heading', heading))
        elif name in {'p', 'div'}:
            flush()
            pending.append(node.get_text(''))
            flush()
        else:
            pending.append(node.get_text('') if name else str(node))
    flush()
    return blocks


def to_document(doc_id, url, pmid, record, fetched_at):
    base = dict(doc_id=doc_id, url=url, host=HOST, final_url=None, source='europepmc',
                source_record=f'MED/{pmid}', parser_version=VERSION, fetched_at=fetched_at,
                page_type='abstract', answers=[], resources=[])
    if record is None:
        return dict(base, title='', status='not_found', warnings=['record_not_in_europepmc'],
                    blocks=[], text='', source_metadata=dict(pmid=pmid))
    title = clean(BeautifulSoup(record.get('title') or '', 'html.parser').get_text(''))
    blocks = [dict(kind='heading', role='title', text=title, source_path='europepmc:title')] if title else []
    blocks += [dict(kind=kind, role='abstract', text=value, source_path='europepmc:abstractText')
               for kind, value in abstract_blocks(record.get('abstractText') or '')]
    for index, block in enumerate(blocks):
        block['order'] = index
    warnings = ([] if title else ['title_missing']) + (
        [] if any(b['role'] == 'abstract' for b in blocks) else ['abstract_missing'])
    journal = (record.get('journalInfo') or {}).get('journal') or {}
    return dict(base, title=title, status='needs_review' if warnings else 'extracted_unreviewed',
                warnings=warnings, blocks=blocks, text='\n\n'.join(b['text'] for b in blocks),
                source_metadata=dict(
                    pmid=pmid, pmcid=record.get('pmcid'), doi=record.get('doi'),
                    journal=journal.get('title'), pub_year=record.get('pubYear'),
                    language=record.get('language'), authors=record.get('authorString'),
                    pub_types=(record.get('pubTypeList') or {}).get('pubType', []),
                    mesh=[m.get('descriptorName') for m in (record.get('meshHeadingList') or {}).get('meshHeading', [])],
                    keywords=(record.get('keywordList') or {}).get('keyword', [])))


def openalex_document(doc_id, url, pmid, work, fetched_at):
    """Fallback for PMIDs absent from Europe PMC (mostly PubMed-indexed preprints)."""
    index = work.get('abstract_inverted_index') or {}
    words = sorted((position, word) for word, positions in index.items() for position in positions)
    abstract = clean(' '.join(word for _, word in words))
    title = clean(work.get('title') or '')
    blocks = [dict(kind='heading', role='title', text=title, source_path='openalex:title')] if title else []
    if abstract:
        blocks.append(dict(kind='paragraph', role='abstract', text=abstract,
                           source_path='openalex:abstract_inverted_index'))
    for order, block in enumerate(blocks):
        block['order'] = order
    warnings = ([] if title else ['title_missing']) + ([] if abstract else ['abstract_missing'])
    location = (work.get('primary_location') or {}).get('source') or {}
    return dict(doc_id=doc_id, url=url, host=HOST, final_url=None, source='openalex',
                source_record=work.get('id'), parser_version='openalex-v1', fetched_at=fetched_at,
                page_type='abstract', answers=[], resources=[], title=title,
                status='needs_review' if warnings else 'extracted_unreviewed', warnings=warnings,
                blocks=blocks, text='\n\n'.join(b['text'] for b in blocks),
                source_metadata=dict(pmid=pmid, doi=work.get('doi'), journal=location.get('display_name'),
                                     pub_year=work.get('publication_year'), language=work.get('language'),
                                     pub_types=[work.get('type')] if work.get('type') else []))


def fill_openalex(db, client):
    """Replace not_found documents found in OpenAlex; resealing publishes the new version."""
    missing = db.execute("SELECT doc_id,pmid,url FROM items WHERE status='not_found' ORDER BY doc_id").fetchall()
    filled = Counter()
    for start in range(0, len(missing), 100):
        group = missing[start:start + 100]
        response = client.get('https://api.openalex.org/works', params={
            'filter': 'ids.pmid:' + '|'.join(pmid for _, pmid, _ in group), 'per-page': '100'})
        response.raise_for_status()
        works = {w['ids'].get('pmid', '').rstrip('/').rsplit('/', 1)[-1]: w for w in response.json()['results']}
        fetched_at = utc_now()
        with db:
            for doc_id, pmid, url in group:
                if pmid not in works:
                    continue
                document = openalex_document(doc_id, url, pmid, works[pmid], fetched_at)
                filled[document['status']] += 1
                db.execute('UPDATE items SET status=?,document=?,record=?,batch_id=NULL WHERE doc_id=?',
                           [document['status'], json.dumps(document, ensure_ascii=False),
                            json.dumps(works[pmid], ensure_ascii=False), doc_id])
        time.sleep(1)
    print(f'OpenAlex: {sum(filled.values()):,}/{len(missing):,} not_found PMIDs filled {dict(filled)}', flush=True)


def search(client, pmids, attempts=6):
    """One request per PMID group; transient failures back off, others stop the run."""
    params = dict(query='(' + ' OR '.join(f'EXT_ID:{p}' for p in pmids) + ') AND SRC:MED',
                  resultType='core', format='json', pageSize=1000)
    for attempt in range(attempts):
        try:
            response = client.get(API, params=params)
            if response.status_code == 200:
                data = response.json()
                results = data['resultList']['result']
                if data['hitCount'] > len(results):
                    raise ValueError(f'Unexpected pagination: {data["hitCount"]} hits, {len(results)} returned')
                return {r['id']: r for r in results if r.get('source') == 'MED'}
            if response.status_code not in (429, 500, 502, 503, 504):
                raise ValueError(f'Europe PMC HTTP {response.status_code}: {response.text[:300]}')
            retry_after = response.headers.get('retry-after', '')
            wait = float(retry_after) if retry_after.isdigit() else 5 * 2 ** attempt
        except (httpx.TransportError, json.JSONDecodeError) as error:
            wait = 5 * 2 ** attempt
            print(f'Europe PMC request failed ({type(error).__name__}); retry in {wait:.0f}s', flush=True)
        if attempt + 1 < attempts:
            time.sleep(min(wait, 300))
    raise ValueError(f'Europe PMC unavailable after {attempts} attempts; checkpoint kept, rerun to resume')


def seal(db, root, plan_sha, batch_docs, force=False):
    rows = db.execute('SELECT doc_id,document,record FROM items WHERE status IS NOT NULL AND batch_id IS NULL '
                      'ORDER BY doc_id LIMIT ?', [batch_docs]).fetchall()
    if not rows or (not force and len(rows) < batch_docs):
        return False
    batch_id = uuid.uuid4().hex
    folder = root / 'batches' / batch_id
    folder.mkdir(parents=True)
    with gzip.open(folder / 'documents.jsonl.gz', 'wt', encoding='utf-8') as documents, \
            gzip.open(folder / 'records.jsonl.gz', 'wt', encoding='utf-8') as records:
        for doc_id, document, record in rows:
            documents.write(document + '\n')
            if record:
                records.write(json.dumps(dict(doc_id=doc_id, record=json.loads(record)), ensure_ascii=False) + '\n')
    manifest = dict(format_version=1, batch_id=batch_id, path=f'europepmc/{plan_sha}/batches/{batch_id}',
                    created_at=time.time(), source=API, parser_version=VERSION, plan_sha256=plan_sha,
                    count=len(rows), files={name: dict(bytes=(folder / name).stat().st_size,
                                                       sha256=sha256(folder / name)) for name in BATCH_FILES})
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    with db:
        db.execute('INSERT INTO batches(batch_id,manifest) VALUES (?,?)', [batch_id, json.dumps(manifest)])
        db.executemany('UPDATE items SET batch_id=? WHERE doc_id=?', [(batch_id, row[0]) for row in rows])
    print(f'Sealed batch {batch_id}: {len(rows):,} documents', flush=True)
    return True


def upload_pending(db, root, config):
    """Manifest last: it marks a complete batch. Verified raw records leave the checkpoint."""
    for batch_id, manifest_json in db.execute('SELECT batch_id,manifest FROM batches WHERE verified=0').fetchall():
        manifest = json.loads(manifest_json)
        folder = root / 'batches' / batch_id
        remote = destination_root(config).rstrip('/') + '/' + manifest['path']
        for name, expected in manifest['files'].items():
            if sha256(folder / name) != expected['sha256']:
                raise ValueError(f'Local batch corrupted: {folder / name}')
            command('copyto', folder / name, remote + '/' + name, '--immutable', '--retries', '3')
            verify_remote(remote + '/' + name, expected['sha256'])
        command('copyto', folder / 'manifest.json', remote + '/manifest.json', '--immutable')
        verify_remote(remote + '/manifest.json', sha256(folder / 'manifest.json'))
        with db:
            db.execute('UPDATE batches SET verified=1 WHERE batch_id=?', [batch_id])
            db.execute('UPDATE items SET record=NULL WHERE batch_id=?', [batch_id])
        shutil.rmtree(folder)
        print(f'Verified on cloud storage: {batch_id}', flush=True)


def write_report(db, root, plan_sha, config):
    folder = root / 'followup'
    folder.mkdir(exist_ok=True)
    counts = Counter()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['doc_id', 'pmid', 'url', 'category', 'warnings'])
    for doc_id, pmid, url, status, document in db.execute(
            'SELECT doc_id,pmid,url,status,document FROM items ORDER BY doc_id'):
        category = status or 'pending'
        counts[category] += 1
        if category != 'extracted_unreviewed':
            warnings = json.loads(document)['warnings'] if document else []
            writer.writerow([doc_id, pmid, url, category, ' | '.join(warnings)])
    (folder / 'documents.csv.gz').write_bytes(gzip.compress(buffer.getvalue().encode(), mtime=0))
    summary = dict(generated_at=utc_now(), plan_sha256=plan_sha, source=API, parser_version=VERSION,
                   total=sum(counts.values()), categories=dict(counts),
                   documents_csv_sha256=sha256(folder / 'documents.csv.gz'))
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    if config:
        remote = destination_root(config).rstrip('/') + f'/europepmc/{plan_sha}/followup'
        for name in ('documents.csv.gz', 'summary.json'):
            command('copyto', folder / name, remote + '/' + name, '--retries', '3')
            verify_remote(remote + '/' + name, sha256(folder / name))
    return counts


def show_status(db):
    counts = Counter(dict(db.execute("SELECT coalesce(status,'pending'),count(*) FROM items GROUP BY 1")))
    batches = Counter(dict(db.execute('SELECT verified,count(*) FROM batches GROUP BY 1')))
    done = sum(n for k, n in counts.items() if k != 'pending')
    print(f'Done {done:,}/{sum(counts.values()):,}: {dict(counts)}')
    print(f'Batches: verified={batches[1]:,} awaiting upload={batches[0]:,}; '
          f'not yet sealed={db.execute("SELECT count(*) FROM items WHERE status IS NOT NULL AND batch_id IS NULL").fetchone()[0]:,}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--plan', type=Path, default=Path('artifacts/recrawl_20261015/en/plan.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/recrawl_20261015/en/europepmc'))
    parser.add_argument('--storage-config', type=Path, default=Path('artifacts/recrawl_20261015/storage.en.json'))
    parser.add_argument('--no-upload', action='store_true', help='Keep sealed batches local')
    parser.add_argument('--limit', type=int, default=0, help='Process at most N pending IDs this run; 0 = all')
    parser.add_argument('--ids-per-request', type=int, default=100)
    parser.add_argument('--delay', type=float, default=1.0, help='Seconds between requests')
    parser.add_argument('--batch-docs', type=int, default=20000)
    parser.add_argument('--progress-every', type=int, default=2000)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--export', type=Path, help='Write all fetched documents to this JSONL.gz and exit')
    parser.add_argument('--fill-openalex', action='store_true',
                        help='Look up not_found PMIDs in OpenAlex instead of fetching pending IDs')
    args = parser.parse_args()
    if not 1 <= args.ids_per_request <= 500 or args.batch_docs < 1 or args.limit < 0:
        parser.error('--ids-per-request must be 1..500; --batch-docs/--limit must be positive')
    config = None if args.no_upload else json.loads(args.storage_config.read_text())
    failed = None
    import fcntl
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / '.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error(f'Another process is using {args.output}')
        try:
            db, plan_sha = open_state(args.output, args.plan)
        except ValueError as error:
            parser.error(str(error))
        try:
            if args.status:
                show_status(db)
                return
            if args.export:
                with gzip.open(args.export, 'wt', encoding='utf-8') as output:
                    for (document,) in db.execute('SELECT document FROM items WHERE document IS NOT NULL ORDER BY doc_id'):
                        output.write(document + '\n')
                print(f'Wrote {args.export}')
                return
            if config:
                validate_destination(config)
                upload_pending(db, args.output, config)  # Batches left by an interrupted run.
            if args.fill_openalex:
                with httpx.Client(timeout=60, headers={'User-Agent': 'MedXIR/0.1 (corpus collection)'}) as client:
                    fill_openalex(db, client)
            pending = [] if args.fill_openalex else db.execute(
                'SELECT doc_id,pmid,url FROM items WHERE status IS NULL ORDER BY doc_id'
                + (f' LIMIT {args.limit}' if args.limit else '')).fetchall()
            # Measured: about 1.6 s per request of 100 IDs at the default 1 s delay.
            print(f'{len(pending):,} PubMed IDs to fetch from Europe PMC; '
                  f'~{len(pending) / args.ids_per_request * (args.delay + 1.6) / 60:.1f} min', flush=True)
            started, processed = time.monotonic(), 0
            with httpx.Client(timeout=60, headers={'User-Agent': 'MedXIR/0.1 (corpus collection)'}) as client:
                for start in range(0, len(pending), args.ids_per_request):
                    group = pending[start:start + args.ids_per_request]
                    records = search(client, sorted({pmid for _, pmid, _ in group}))
                    fetched_at = utc_now()
                    with db:
                        for doc_id, pmid, url in group:
                            record = records.get(pmid)
                            document = to_document(doc_id, url, pmid, record, fetched_at)
                            db.execute('UPDATE items SET status=?,document=?,record=? WHERE doc_id=?',
                                       [document['status'], json.dumps(document, ensure_ascii=False),
                                        json.dumps(record, ensure_ascii=False) if record else None, doc_id])
                    processed += len(group)
                    if processed % args.progress_every < args.ids_per_request or processed == len(pending):
                        rate = processed / max(time.monotonic() - started, 1e-9)
                        print(f'{processed:,}/{len(pending):,} | {rate:.0f} IDs/s | '
                              f'found {len(records)}/{len(group)} in last request', flush=True)
                    if seal(db, args.output, plan_sha, args.batch_docs) and config:
                        upload_pending(db, args.output, config)
                    time.sleep(args.delay)
        except KeyboardInterrupt:
            print('Interrupted; fetched IDs are saved and skipped on resume.', flush=True)
        except (ValueError, httpx.HTTPError) as error:
            failed = error
        finally:
            if not args.status and not args.export:
                while seal(db, args.output, plan_sha, args.batch_docs, force=True):
                    pass
                if config:
                    upload_pending(db, args.output, config)
                counts = write_report(db, args.output, plan_sha, config)
                print(json.dumps(dict(counts), ensure_ascii=False), flush=True)
                print(f'Follow-up: {args.output / "followup" / "documents.csv.gz"}', flush=True)
            db.close()
    if failed:
        parser.exit(1, f'Stopped: {failed}\n')
    if counts.get('pending'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
