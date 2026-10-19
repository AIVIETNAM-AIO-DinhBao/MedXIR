"""Fetch pending Pharmacity blog URLs from the address the organizers announced.

BTC notice: https://pmc-ecm-healthblog.beta.pharmacity.io/<slug>/ is unreachable; use
https://www.pharmacity.vn/<slug>.htm instead and keep the original doc_id. Each page is
parsed with the www.pharmacity.vn template; `url` stays the corpus URL, `final_url`
is the new address. Sequential, paced, resumable.

Output: artifacts/recrawl_20261015/pharmacity_v1/{documents.jsonl.gz, fetch.jsonl, raw/}

    uv run python scripts/pharmacity_fetch.py --delay 2
"""
import argparse
import gzip
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from content_parser import parse_page

OLD_HOST = 'pmc-ecm-healthblog.beta.pharmacity.io'
NEW_HOST = 'www.pharmacity.vn'
USER_AGENT = 'MedXIR/0.1 (corpus collection)'


def new_url(url):
    slug = urlsplit(url).path.strip('/')
    return f'https://{NEW_HOST}/{slug}.htm' if slug and '/' not in slug else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--state', nargs='+', type=Path,
                        default=sorted(Path('artifacts/recrawl_20261015/vi').glob('shard-*/state.sqlite')))
    parser.add_argument('--output', type=Path, default=Path('artifacts/recrawl_20261015/pharmacity_v1'))
    parser.add_argument('--delay', type=float, default=2.0)
    parser.add_argument('--limit', type=int, default=0, help='0 = all')
    args = parser.parse_args()

    (args.output / 'raw').mkdir(parents=True, exist_ok=True)
    log_path = args.output / 'fetch.jsonl'
    done = set()
    if log_path.exists():
        done = {row['doc_id'] for row in map(json.loads, log_path.read_text().splitlines()) if row['status'] != 'error'}
    docs = []
    for path in args.state:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        docs += db.execute('SELECT doc_id,url FROM jobs WHERE host=? AND page IS NULL', [OLD_HOST]).fetchall()
        db.close()
    docs = [(doc_id, url) for doc_id, url in sorted(docs) if doc_id not in done]
    docs = docs[:args.limit] if args.limit else docs
    print(f'{len(docs):,} pending Pharmacity URLs ({len(done):,} already done)', flush=True)

    client = httpx.Client(timeout=40, follow_redirects=True, headers={'User-Agent': USER_AGENT})
    counts = {'ok': 0, 'not_found': 0, 'error': 0}
    with gzip.open(args.output / 'documents.jsonl.gz', 'at', encoding='utf-8') as out, \
            log_path.open('a', encoding='utf-8') as log:
        for n, (doc_id, url) in enumerate(docs, 1):
            target, error = new_url(url), None
            if not target:
                status, error = 'not_found', 'no slug'
            else:
                try:
                    response = client.get(target)
                    if response.status_code == 429:
                        print(f'Stopped at {n}: 429. Wait, then rerun.', flush=True)
                        break
                    if response.status_code != 200 or urlsplit(str(response.url)).path in ('', '/'):
                        status, error = 'not_found', f'http {response.status_code} {response.url}'
                    else:
                        raw = response.content
                        raw_path = args.output / 'raw' / f'{doc_id}.html.gz'
                        raw_path.write_bytes(gzip.compress(raw, mtime=0))
                        page = parse_page(dict(
                            doc_id=doc_id, url=url, host=NEW_HOST, final_url=str(response.url),
                            state='html_response', content_type=response.headers.get('content-type', ''),
                            sha256=hashlib.sha256(raw).hexdigest(),
                            raw_path=str(raw_path.relative_to(args.output))), raw)
                        page['lang'] = 'vi'
                        out.write(json.dumps(page, ensure_ascii=False) + '\n')
                        status = 'ok'
                except httpx.HTTPError as exc:
                    status, error = 'error', type(exc).__name__
            counts[status] += 1
            log.write(json.dumps(dict(doc_id=doc_id, url=url, new_url=target, status=status, error=error),
                                 ensure_ascii=False) + '\n')
            log.flush(); out.flush()
            if n % 50 == 0:
                print(f'{n:,}/{len(docs):,} {counts}', flush=True)
            time.sleep(args.delay)
    print(counts, flush=True)


if __name__ == '__main__':
    main()
