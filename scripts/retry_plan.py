"""Build retry plans from a shard checkpoint (read-only), for crawl.py or cc_fetch.py.

live: pending URLs of the given paused hosts that no recovery store holds yet
      (cc_v1, wayback_v1, pharmacity_v1), plus pages that failed transiently
      (connection errors, timeouts, empty bodies, HTTP 429/5xx). Feed to crawl.py with a
      new --output, as the runbook requires: the original checkpoint stays unchanged.
archive: pages that answered but redirected home or to a login wall on the live site;
      only an archived capture can recover them (cc_index.py + cc_fetch.py --plan).

Deferred (blocked) hosts are never included.

    uv run python scripts/retry_plan.py --lang vi --mode live --host nhathuoclongchau.com.vn \
        --output artifacts/recrawl_20261015/vi/retry_v1/plan.jsonl
"""
import argparse
import gzip
import json
import sqlite3
from pathlib import Path

RECOVERED = ('cc_v1', 'wayback_v1', 'pharmacity_v1')
TRANSIENT = {'fetch_error', 'empty_response'}
ARCHIVE_ONLY = {'redirected_home', 'source_error'}


def recovered_ids(root):
    ids = set()
    for name in RECOVERED:
        path = root / name / 'documents.jsonl.gz'
        if path.is_file():
            with gzip.open(path, 'rt', encoding='utf-8') as source:
                ids.update(json.loads(line)['doc_id'] for line in source)
    return ids


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=Path('artifacts/recrawl_20261015'))
    parser.add_argument('--lang', choices=['vi', 'zh'], required=True)
    parser.add_argument('--shard', default='shard-000-of-002')
    parser.add_argument('--mode', choices=['live', 'archive'], required=True)
    parser.add_argument('--host', action='append', default=[], help='live: paused host whose pending URLs to retry')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    state = args.root / args.lang / args.shard / 'state.sqlite'
    db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
    paused = json.loads((db.execute("SELECT value FROM metadata WHERE key='paused_hosts'").fetchone() or ['{}'])[0])
    deferred = {host for host, info in paused.items() if info.get('reason', '').startswith('deferred')}
    if blocked := deferred & set(args.host):
        parser.error(f'Deferred hosts are not retried: {sorted(blocked)}')
    done = recovered_ids(args.root)

    rows = []
    for doc_id, url, host, page, document in db.execute(
            'SELECT doc_id,url,host,page,document FROM jobs ORDER BY doc_id'):
        if host in deferred or doc_id in done:
            continue
        if page is None:
            if args.mode == 'live' and host in args.host:
                rows.append((doc_id, url, host))
            continue
        status = json.loads(document).get('status') if document else None
        http_status = json.loads(page).get('http_status') or 0
        retryable_http = http_status == 429 or http_status >= 500
        if args.mode == 'live' and (status in TRANSIENT or (status == 'http_error' and retryable_http)):
            rows.append((doc_id, url, host))
        elif args.mode == 'archive' and status in ARCHIVE_ONLY:
            rows.append((doc_id, url, host))
    db.close()

    if args.output.exists():
        parser.error(f'{args.output} exists; plans are immutable once a checkpoint uses them')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', encoding='utf-8') as out:
        for doc_id, url, host in rows:
            out.write(json.dumps(dict(doc_id=doc_id, url=url, host=host, language=args.lang),
                                 ensure_ascii=False) + '\n')
    hosts = {}
    for _, _, host in rows:
        hosts[host] = hosts.get(host, 0) + 1
    print(f'{len(rows):,} URLs -> {args.output}', flush=True)
    for host, count in sorted(hosts.items(), key=lambda item: -item[1]):
        print(f'  {host}: {count:,}', flush=True)


if __name__ == '__main__':
    main()
