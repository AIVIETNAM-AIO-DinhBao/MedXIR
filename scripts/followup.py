"""Snapshot every outstanding document into a repair queue, including pending IDs.

Reads a consistent SQLite snapshot; never downloads source pages or archived text.
"""
import argparse
import csv
import gzip
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from storage import Storage, command, sha256


def disposition(page, document, pause=None):
    if page is None:
        if pause:
            return ('deferred' if pause.get('reason', '').startswith('deferred:') else 'paused',
                    'fetch_after_source_fix', pause.get('reason', 'paused'))
        return 'pending', 'crawl', 'not_attempted'
    if page.get('state') == 'non_html':
        return 'incomplete', 'parse_resource', 'non_html'
    if page.get('state') != 'html_response':
        return 'crawl_failed', 'recrawl', page.get('state', 'unknown_fetch_state')
    status = document.get('status', 'document_missing')
    has_text = document.get('has_text', bool(document.get('text', '').strip()))
    warnings = document.get('warnings') or []
    if status == 'source_error':
        return 'crawl_failed', 'recrawl_after_source_check', status
    if status in {'parser_error', 'unknown_template', 'encoded_content', 'empty_content',
                  'document_missing'} or 'block_text_mismatch' in warnings:
        return 'parse_failed', 'reparse_after_fix', status
    if not has_text:
        return 'incomplete', 'inspect_raw_or_fetch_resources', 'no_text'
    if warnings or status != 'extracted_unreviewed':
        return 'needs_review', 'review_or_enrich', ' | '.join(warnings) or status
    return 'extracted_unreviewed', 'none', ''


def write_report(root, storage=None):
    """Write local queues, then verified remote payloads and a checksum manifest.

    Full text is deliberately excluded: compact/pruned checkpoints work too.
    A generation ID and checksums let readers detect concurrent report replacement.
    """
    import fcntl
    root = Path(root)
    folder = root / 'followup'
    folder.mkdir(exist_ok=True)
    with (root / '.followup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        db = sqlite3.connect((root / 'state.sqlite').resolve().as_uri() + '?mode=ro',
                             uri=True, timeout=60)
        counts, actions = Counter(), Counter()
        hosts = defaultdict(Counter)
        fields = ['doc_id', 'url', 'host', 'category', 'action', 'reason', 'fetch_state',
                  'http_status', 'parse_status', 'has_text', 'warnings', 'error',
                  'raw_path', 'archive', 'parser_version', 'fetched_at']
        try:
            db.execute('BEGIN')
            metadata = dict(db.execute('SELECT key,value FROM metadata'))
            identity = json.loads(metadata['identity'])
            paused = json.loads(metadata.get('paused_hosts', '{}'))
            archive_column = 'archive' if 'archive' in {
                r[1] for r in db.execute('PRAGMA table_info(jobs)')} else 'NULL'
            with gzip.open(folder / 'documents.csv.gz.tmp', 'wt', encoding='utf-8', newline='') as file, \
                    gzip.open(folder / 'retry_fetch.jsonl.gz.tmp', 'wt', encoding='utf-8') as retry, \
                    gzip.open(folder / 'reparse.jsonl.gz.tmp', 'wt', encoding='utf-8') as reparse:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                for doc_id, url, host, page_json, doc_json, archive in db.execute(
                        f'SELECT doc_id,url,host,page,document,{archive_column} FROM jobs ORDER BY doc_id'):
                    page = json.loads(page_json) if page_json else None
                    doc = json.loads(doc_json) if doc_json else {}
                    category, action, reason = disposition(page, doc, paused.get(host))
                    counts[category] += 1
                    actions[action] += 1
                    hosts[host][category] += 1
                    if category == 'extracted_unreviewed':
                        continue
                    p = page or {}
                    writer.writerow(dict(doc_id=doc_id, url=url, host=host,
                        category=category, action=action, reason=reason,
                        fetch_state=p.get('state', 'pending'), http_status=p.get('http_status'),
                        parse_status=doc.get('status'),
                        has_text=doc.get('has_text', bool(doc.get('text', '').strip())),
                        warnings=' | '.join(doc.get('warnings') or []), error=p.get('error'),
                        raw_path=p.get('raw_path'), archive=archive,
                        parser_version=doc.get('parser_version'), fetched_at=p.get('fetched_at')))
                    plan_row = dict(doc_id=doc_id, url=url, host=host)
                    # Retry only finished failures. Pending/deferred remain in the original plan.
                    if category == 'crawl_failed':
                        retry.write(json.dumps(plan_row, ensure_ascii=False) + '\n')
                    if category == 'parse_failed':
                        reparse.write(json.dumps(plan_row, ensure_ascii=False) + '\n')
        finally:
            db.close()
        files = {}
        for name in ('documents.csv.gz', 'retry_fetch.jsonl.gz', 'reparse.jsonl.gz'):
            (folder / (name + '.tmp')).replace(folder / name)
            files[name] = dict(bytes=(folder / name).stat().st_size, sha256=sha256(folder / name))
        report = dict(generated_at=datetime.now(timezone.utc).isoformat(), identity=identity,
                      language=metadata.get('language'), total=sum(counts.values()),
                      outstanding=sum(n for k, n in counts.items() if k != 'extracted_unreviewed'),
                      categories=dict(counts), actions=dict(actions),
                      hosts={h: dict(c) for h, c in sorted(hosts.items())}, files=files,
                      note='extracted_unreviewed is provisional extraction, not a manual completeness guarantee.')
        temporary = folder / 'summary.json.tmp'
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(folder / 'summary.json')
        if storage:
            remote = storage.remote_path(storage.prefix + '/followup')
            for name, expected in files.items():
                command('copyto', folder / name, remote + '/' + name, '--retries', '3')
                storage.verify_remote(remote + '/' + name, expected['sha256'])
            command('copyto', folder / 'summary.json', remote + '/summary.json', '--retries', '3')
            storage.verify_remote(remote + '/summary.json', sha256(folder / 'summary.json'))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--storage-config', type=Path)
    args = parser.parse_args()
    store = Storage(args.root, args.storage_config) if args.storage_config else None
    report = write_report(args.root, store)
    print(json.dumps({k: report[k] for k in ('generated_at', 'total', 'outstanding', 'categories')}))


if __name__ == '__main__':
    main()
