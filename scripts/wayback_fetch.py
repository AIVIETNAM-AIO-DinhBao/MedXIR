"""Recover pending URLs from the Internet Archive (Wayback Machine) and parse them.

For each pending URL: one CDX lookup for its newest 200 text/html snapshot, then one
fetch of that snapshot's original bytes (id_ form, no toolbar). Requests are sequential
and paced; a 429 stops the run so it can be resumed later. Archive servers only; the
origin site is never contacted.

Output (resumable, append-only), default artifacts/recrawl_20261015/wayback_v1/:
  documents.jsonl.gz   parse_page results for recovered pages
  fetch.jsonl          one line per URL: ok / not_found / error
  raw/<doc_id>.html.gz snapshot bodies

    uv run python scripts/wayback_fetch.py --langs zh --limit 200 --delay 2
"""
import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from content_parser import parse_page

USER_AGENT = 'MedXIR/0.1 (corpus collection; Internet Archive snapshot fetch)'
CDX = 'https://web.archive.org/cdx/search/cdx'
PENDING = {
    'vi': 'artifacts/recrawl_20261015/vi/shard-000-of-002/state.sqlite',
    'zh': 'artifacts/recrawl_20261015/zh/shard-000-of-002/state.sqlite',
}


SNAPSHOT = re.compile(r'/web/(\d{14})/(https?://.+)$')


class RateLimited(Exception):
    pass


def pending(langs, limit_per_host):
    docs = []
    for lang in langs:
        db = sqlite3.connect(f'file:{PENDING[lang]}?mode=ro', uri=True)
        rows = db.execute('SELECT doc_id,url,host FROM jobs WHERE page IS NULL ORDER BY host, doc_id').fetchall()
        db.close()
        per_host = {}
        for doc_id, url, host in rows:
            per_host[host] = per_host.get(host, 0) + 1
            if limit_per_host and per_host[host] > limit_per_host:
                continue
            docs.append(dict(doc_id=doc_id, url=url, host=host, lang=lang))
    return docs


def get(client, url, delay, follow=True, waits=6, wait_seconds=600):
    """One archive request, paced by `delay`. On 429/503 wait and retry (up to `waits` times)."""
    for attempt in range(waits + 1):
        response = client.get(url, follow_redirects=follow)
        time.sleep(delay)
        if response.status_code not in (429, 503):
            return response
        if attempt < waits:
            print(f'  archive {response.status_code}; waiting {wait_seconds // 60} min (try {attempt + 1}/{waits})',
                  flush=True)
            time.sleep(wait_seconds)
    raise RateLimited(f'{response.status_code} after {waits} waits')


def snapshot(client, url, delay):
    """Nearest capture of this URL: one request. Wayback answers 302 to the snapshot, 404 if none."""
    response = get(client, f'https://web.archive.org/web/{url}', delay, follow=False)
    if response.status_code != 302:
        return None
    match = SNAPSHOT.search(response.headers.get('location', ''))
    return dict(timestamp=match.group(1), original=match.group(2)) if match else None


def body(client, found, delay):
    """Original bytes of the snapshot (id_ form). Returns (status, content_type, bytes)."""
    response = get(client, f"https://web.archive.org/web/{found['timestamp']}id_/{quote(found['original'], safe=':/?=&%#')}",
                   delay)
    return response.status_code, response.headers.get('content-type', ''), response.content


def load_done(path):
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if row['status'] in ('ok', 'not_found'):
            done.add(row['doc_id'])
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--langs', nargs='+', choices=['vi', 'zh'], default=['zh'])
    parser.add_argument('--output', type=Path, default=Path('artifacts/recrawl_20261015/wayback_v1'))
    parser.add_argument('--limit', type=int, default=0, help='max URLs per host (0 = all); use for sampling')
    parser.add_argument('--hosts', nargs='+', help='only these hosts')
    parser.add_argument('--delay', type=float, default=2.0, help='seconds between archive requests')
    args = parser.parse_args()

    (args.output / 'raw').mkdir(parents=True, exist_ok=True)
    fetch_path = args.output / 'fetch.jsonl'
    done = load_done(fetch_path)
    docs = [d for d in pending(args.langs, args.limit) if d['doc_id'] not in done
            and (not args.hosts or d['host'] in args.hosts)]
    print(f'{len(docs):,} URLs to try ({len(done):,} already done)', flush=True)

    client = httpx.Client(timeout=90, headers={'User-Agent': USER_AGENT})
    counts = {'ok': 0, 'not_found': 0, 'error': 0}
    started = time.monotonic()
    with gzip.open(args.output / 'documents.jsonl.gz', 'at', encoding='utf-8') as out, \
            fetch_path.open('a', encoding='utf-8') as log:
        for n, doc in enumerate(docs, 1):
            try:
                found = snapshot(client, doc['url'], args.delay)
                if found is None:
                    status, error = 'not_found', None
                else:
                    code, content_type, raw = body(client, found, args.delay)
                    if code != 200 or 'html' not in content_type:
                        status, error = 'error', f'snapshot {code} {content_type[:40]}'
                    else:
                        raw_path = args.output / 'raw' / f"{doc['doc_id']}.html.gz"
                        raw_path.write_bytes(gzip.compress(raw, mtime=0))
                        record = dict(doc_id=doc['doc_id'], url=doc['url'], host=doc['host'],
                                      final_url=found['original'], state='html_response',
                                      content_type='text/html', sha256=hashlib.sha256(raw).hexdigest(),
                                      raw_path=str(raw_path.relative_to(args.output)))
                        try:
                            page = parse_page(record, raw)
                            page['lang'] = doc['lang']
                            page['wayback_timestamp'] = found['timestamp']
                            out.write(json.dumps(page, ensure_ascii=False) + '\n')
                            status, error = 'ok', None
                        except Exception as exc:  # one bad page must not stop the run
                            status, error = 'error', 'parse: ' + str(exc)[:200]
            except RateLimited as stop:
                print(f'Stopped at {n}/{len(docs)}: archive answered {stop}. Wait, then rerun the same command.',
                      flush=True)
                break
            except httpx.HTTPError as exc:
                status, error = 'error', type(exc).__name__
            counts[status] += 1
            log.write(json.dumps(dict(doc_id=doc['doc_id'], host=doc['host'], url=doc['url'], status=status,
                                      error=error), ensure_ascii=False) + '\n')
            log.flush(); out.flush()
            if n % 25 == 0:
                rate = n / (time.monotonic() - started)
                print(f'{n:,}/{len(docs):,} {counts} ({rate:.2f}/s)', flush=True)
        else:
            print('finished', flush=True)
    print(f'ok {counts["ok"]:,}, not found {counts["not_found"]:,}, errors {counts["error"]:,}', flush=True)


if __name__ == '__main__':
    sys.exit(main())
