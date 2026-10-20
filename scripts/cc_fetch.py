"""Recover pending URLs from Common Crawl captures and parse them with content_parser.

Reads capture pointers from a cc_index JSONL (one per crawl, see the index probe),
fetches only each record's byte range from data.commoncrawl.org, unwraps the HTTP
body, then runs the same parse_page used by the crawler. Archive servers only;
never contacts the origin site.

Output (resumable, append-only):
  <output>/raw/<doc_id>.html.gz   response body as stored by Common Crawl
  <output>/documents.jsonl.gz     parse_page results, one per recovered doc_id
  <output>/fetch.jsonl            one line per attempt: doc_id, ok, error

    uv run python scripts/cc_fetch.py --pointers /tmp/.../cc_*.jsonl --output artifacts/recrawl_20261015/cc_v1
"""
import argparse
import gzip
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from content_parser import parse_page

USER_AGENT = 'MedXIR/0.1 (corpus collection; Common Crawl range fetch)'
ARCHIVE = 'https://data.commoncrawl.org/'
PENDING = {
    'vi': 'artifacts/recrawl_20261015/vi/shard-000-of-002/state.sqlite',
    'zh': 'artifacts/recrawl_20261015/zh/shard-000-of-002/state.sqlite',
}


def normalize(url):
    return url.rstrip('/').lower().replace('https://', '').replace('http://', '').replace('www.', '')


def plan_docs(path):
    """Docs from a retry_plan.py archive plan (pages that redirected on the live site)."""
    docs = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            docs[row['doc_id']] = dict(doc_id=row['doc_id'], url=row['url'], host=row['host'], lang=row['language'])
    return docs


def pending_docs():
    docs = {}
    for lang, path in PENDING.items():
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        for doc_id, url, host in db.execute('SELECT doc_id,url,host FROM jobs WHERE page IS NULL'):
            docs[doc_id] = dict(doc_id=doc_id, url=url, host=host, lang=lang)
        db.close()
    return docs


def pick_pointers(paths, docs):
    """Newest crawl first; keep the first 200 text/html capture of each pending URL."""
    by_url = {normalize(d['url']): d for d in docs.values()}
    chosen = {}
    rows = []
    for path in paths:
        rows += [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    # Newest crawl first, text/html before 'unk' (MIME not recorded; used only when no html capture exists).
    rows.sort(key=lambda r: r['crawl'], reverse=True)
    rows.sort(key=lambda r: (r.get('mime') or '') == 'unk')
    for row in rows:
        mime = row.get('mime') or ''
        if row['status'] != 200 or ('html' not in mime and mime != 'unk'):
            continue
        doc = by_url.get(normalize(row['url']))
        if doc and doc['doc_id'] not in chosen:
            chosen[doc['doc_id']] = dict(doc, pointer=row)
    return chosen


def unwrap(warc_bytes):
    """Return (http_headers, body) from one WARC response record."""
    record = gzip.decompress(warc_bytes)
    _, _, rest = record.partition(b'\r\n\r\n')          # WARC headers
    http, _, body = rest.partition(b'\r\n\r\n')          # HTTP headers
    lines = http.decode('latin-1').split('\r\n')
    headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(':') for l in lines[1:]) if k}
    if headers.get('content-encoding', '').lower() == 'gzip':
        body = gzip.decompress(body)
    return headers, body


def fetch(client, doc, output):
    raw_path = output / 'raw' / f"{doc['doc_id']}.html.gz"
    pointer = doc['pointer']
    try:
        if raw_path.exists():
            body = gzip.decompress(raw_path.read_bytes())
            headers = {}
        else:
            # CloudFront answers 403 to bursts of range requests; back off and retry instead of failing.
            for attempt in range(6):
                response = client.get(ARCHIVE + pointer['warc'], headers={
                    'Range': f"bytes={pointer['offset']}-{pointer['offset'] + pointer['length'] - 1}"})
                if response.status_code not in (403, 429, 503):
                    break
                time.sleep(min(600, 30 * 2 ** attempt))
            if response.status_code != 206:
                return dict(doc_id=doc['doc_id'], ok=False, error=f'http {response.status_code}')
            headers, body = unwrap(response.content)
            raw_path.write_bytes(gzip.compress(body, mtime=0))
        content_type = headers.get('content-type', pointer.get('mime') or 'text/html')
        record = dict(doc_id=doc['doc_id'], url=doc['url'], host=doc['host'], final_url=doc['url'],
                      state='html_response', content_type=content_type,
                      sha256=hashlib.sha256(body).hexdigest(), raw_path=str(raw_path.relative_to(output)))
        return dict(doc_id=doc['doc_id'], ok=True, record=record, raw=body, lang=doc['lang'], crawl=pointer['crawl'])
    except (httpx.HTTPError, OSError, gzip.BadGzipFile, ValueError) as error:
        return dict(doc_id=doc['doc_id'], ok=False, error=type(error).__name__ + ': ' + str(error)[:200])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pointers', nargs='+', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--limit', type=int, default=0, help='0 = all (for test runs)')
    parser.add_argument('--plan', type=Path, help='Recover these docs instead of the pending ones')
    args = parser.parse_args()

    (args.output / 'raw').mkdir(parents=True, exist_ok=True)
    done_path = args.output / 'documents.jsonl.gz'
    done = set()
    if done_path.exists():
        with gzip.open(done_path, 'rt', encoding='utf-8') as f:
            done = {json.loads(line)['doc_id'] for line in f}
    docs = pick_pointers(args.pointers, plan_docs(args.plan) if args.plan else pending_docs())
    todo = [d for doc_id, d in sorted(docs.items()) if doc_id not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f'{len(docs):,} pending docs have a capture; {len(done):,} already parsed; {len(todo):,} to do', flush=True)

    client = httpx.Client(timeout=90, headers={'User-Agent': USER_AGENT})
    started = time.monotonic()
    ok = failed = 0
    with gzip.open(done_path, 'at', encoding='utf-8') as out, \
            open(args.output / 'fetch.jsonl', 'a', encoding='utf-8') as log, \
            ThreadPoolExecutor(args.workers) as pool:
        for n, result in enumerate(pool.map(lambda d: fetch(client, d, args.output), todo), 1):
            log.write(json.dumps({k: v for k, v in result.items() if k not in ('raw', 'record')}, ensure_ascii=False) + '\n')
            if not result['ok']:
                failed += 1
                continue
            try:
                page = parse_page(result['record'], result['raw'])
            except Exception as error:  # one bad page must not stop the run
                log.write(json.dumps(dict(doc_id=result['doc_id'], ok=False, error='parse: ' + str(error)[:200])) + '\n')
                failed += 1
                continue
            page['lang'] = result['lang']
            page['cc_crawl'] = result['crawl']
            out.write(json.dumps(page, ensure_ascii=False) + '\n')
            ok += 1
            if n % 500 == 0:
                out.flush(); log.flush()
                rate = n / (time.monotonic() - started)
                print(f'{n:,}/{len(todo):,} ok {ok:,} failed {failed:,} ({rate:.1f}/s)', flush=True)
    print(f'done: ok {ok:,}, failed {failed:,}, elapsed {time.monotonic() - started:.0f}s', flush=True)


if __name__ == '__main__':
    main()
