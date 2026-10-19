"""Find Common Crawl captures of given hosts via the public columnar index.

Reads each crawl's cc-index parquet footers (data.commoncrawl.org, not the origin
site) to pick files covering a host's SURT range, then reads only those rows.
One JSONL per crawl: <output>/cc_<crawl>.jsonl with url, status, mime and the WARC
byte range that cc_fetch.py needs.

    uv run python scripts/cc_index.py --output artifacts/recrawl_20261015/cc_v1 \
        --hosts nhathuoclongchau.com.vn --crawls CC-MAIN-2026-34 CC-MAIN-2026-30
"""
import argparse
import gzip
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

# Host -> SURT prefix (reversed domain). Keep in sync with the pending plan.
PREFIXES = {
    'www.120ask.com': 'com,120ask)/',
    'www.familydoctor.com.cn': 'cn,com,familydoctor)/',
    'baidianfeng.familydoctor.com.cn': 'cn,com,familydoctor,baidianfeng)/',
    'zysjonline.com': 'com,zysjonline)/',
    'article.iiyi.com': 'com,iiyi,article)/',
    'laodong.vn': 'vn,laodong)/',
    'nhathuoclongchau.com.vn': 'vn,com,nhathuoclongchau)/',
    'pmc-ecm-healthblog.beta.pharmacity.io': 'io,pharmacity,beta,pmc-ecm-healthblog)/',
    # Live site now redirects most articles home or to a login wall.
    'thaythuocvietnam.vn': 'vn,thaythuocvietnam)/',
    'www.vinmec.com': 'com,vinmec)/',
}
ARCHIVE = 'https://data.commoncrawl.org/'


def crawl_paths(crawl):
    raw = urllib.request.urlopen(f'{ARCHIVE}crawl-data/{crawl}/cc-index-table.paths.gz', timeout=60).read()
    return [line for line in gzip.decompress(raw).decode().split() if 'subset=warc' in line]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--crawls', nargs='+', required=True)
    parser.add_argument('--hosts', nargs='+', default=list(PREFIXES))
    args = parser.parse_args()
    db = duckdb.connect()
    db.execute('INSTALL httpfs; LOAD httpfs;')

    def footer(path):
        # Retry a few times: the index server drops connections under load.
        error = None
        for _ in range(3):
            try:
                row = db.execute(
                    "SELECT min(stats_min_value), max(stats_max_value) FROM parquet_metadata(?) "
                    "WHERE path_in_schema='url_surtkey'", [ARCHIVE + path]).fetchone()
                return path, row or (None, None)
            except duckdb.Error as exc:
                error = exc
        raise RuntimeError(f'footer failed {path}: {error}')

    for crawl in args.crawls:
        print(f'{crawl}: reading index footers (about 100 files, several minutes)...', flush=True)
        ranges = []
        with ThreadPoolExecutor(8) as pool:
            for done, item in enumerate(pool.map(footer, crawl_paths(crawl)), 1):
                ranges.append(item)
                if done % 20 == 0:
                    print(f'  {crawl}: {done} footers read', flush=True)
        print(f'{crawl}: searching {args.hosts}', flush=True)
        with (args.output / f'cc_{crawl}.jsonl').open('w') as out:
            for host in args.hosts:
                prefix = PREFIXES[host]
                files = [ARCHIVE + p for p, (lo, hi) in ranges if lo and lo <= prefix + '￿' and hi >= prefix]
                if not files:
                    continue
                rows = db.execute(
                    'SELECT url, fetch_status, content_mime_type, warc_filename, warc_record_offset, warc_record_length '
                    'FROM read_parquet(?) WHERE url_surtkey >= ? AND url_surtkey < ?',
                    [files, prefix, prefix + '￿']).fetchall()
                for url, status, mime, warc, offset, length in rows:
                    out.write(json.dumps(dict(crawl=crawl, host=host, url=url, status=status, mime=mime,
                                              warc=warc, offset=offset, length=length)) + '\n')
                print(crawl, host, len(rows), 'captures', flush=True)


if __name__ == '__main__':
    main()
