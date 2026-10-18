"""Crawl official URLs, keep raw responses, and extract with content_parser.

Run from the repo root: uv run python scripts/crawl.py
Use --prepare-only to create the shared plan without sending requests.
"""
import argparse
import asyncio
import gzip
import hashlib
import json
import math
import signal
import sqlite3
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from contextlib import ExitStack
from urllib.parse import urljoin, urlsplit

import duckdb
import httpx

from content_parser import VERSION, parse_page, write_review
from storage import Storage
from followup import write_report


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def prepare_plan(args):
    """Give every accessible host a minimum, then allocate by sqrt(URL count).

    Within each host, interleave path groups to cover more than its largest group.
    The plan is created before sharding, so independent machines select the same IDs.
    """
    if args.plan.exists():
        return
    if not args.pilot.is_file():
        raise ValueError(f'Missing pilot manifest: {args.pilot}')
    pilot = [json.loads(line) for line in args.pilot.read_text().splitlines() if line.strip()]
    hosts = sorted({r['host'] for r in pilot if r['state'] == 'html_response'} | set(args.include_host))
    # Deferred pilot entries were never fetched; they still belong in a full plan.
    excluded_ids = [] if getattr(args, 'fresh_plan', False) else sorted({
        int(r['doc_id']) for r in pilot if r['state'] != 'skipped_host'})
    db = duckdb.connect()
    db.execute('CREATE TEMP TABLE excluded_docs(doc_id BIGINT PRIMARY KEY)')
    if excluded_ids:
        db.executemany('INSERT OR IGNORE INTO excluded_docs VALUES (?)', [(i,) for i in excluded_ids])
    for checkpoint in args.exclude_completed:
        if not checkpoint.is_file():
            raise ValueError(f'Missing checkpoint to exclude: {checkpoint}')
        with sqlite3.connect(checkpoint.resolve().as_uri() + '?mode=ro', uri=True) as previous:
            cursor = previous.execute('SELECT doc_id FROM jobs WHERE page IS NOT NULL')
            while batch := cursor.fetchmany(1000):
                db.executemany('INSERT OR IGNORE INTO excluded_docs VALUES (?)', batch)
    corpus_counts = dict(db.execute("SELECT lower(regexp_extract(url, '^https?://([^/:]+)', 1)), "
                                   "count(*) FROM read_parquet(?) GROUP BY 1", [str(args.corpus)]).fetchall())
    if args.all_hosts:
        hosts = sorted(corpus_counts)
    # All SQL parameters come from the manifest or CLI, never from string SQL.
    db.execute("CREATE TEMP TABLE source AS SELECT id AS doc_id, url, "
               "lower(regexp_extract(url, '^https?://([^/:]+)', 1)) AS host, "
               "regexp_replace(regexp_extract(url, '^https?://[^/]+/([^/?#]+)', 1), "
               "'[0-9]+', '{n}', 'g') AS path_group FROM read_parquet(?) "
               "WHERE host IN (SELECT unnest(?)) AND id NOT IN (SELECT doc_id FROM excluded_docs)",
               [str(args.corpus), hosts])
    counts = dict(db.execute('SELECT host, count(*) FROM source GROUP BY host').fetchall())
    total = sum(counts.values())
    target = total if args.limit == 0 else min(args.limit, total)
    if target == 0:
        raise ValueError('No URLs available for this plan')
    if target < len(counts):
        raise ValueError(f'--limit must be at least {len(counts)} to cover every eligible host')
    quotas = dict(counts) if args.limit == 0 else {host: min(20, count) for host, count in counts.items()}
    if sum(quotas.values()) > target:
        quotas = {host: 1 for host in counts}
    # Deterministic, capped allocation; small hosts are not crowded out.
    while sum(quotas.values()) < target:
        available = [h for h in sorted(counts) if quotas[h] < counts[h]]
        remaining = target - sum(quotas.values())
        weight = sum(math.sqrt(counts[h]) for h in available)
        for host in available:
            amount = max(1, int(remaining * math.sqrt(counts[host]) / weight))
            amount = min(amount, counts[host] - quotas[host], target - sum(quotas.values()))
            quotas[host] += amount
    db.execute('CREATE TEMP TABLE quotas(host VARCHAR, quota BIGINT)')
    db.executemany('INSERT INTO quotas VALUES (?, ?)', sorted(quotas.items()))
    # Two ranks first interleave templates, then cap each host at its allocation.
    cursor = db.execute('SELECT doc_id,url,host,path_group FROM source ORDER BY host,doc_id') if args.limit == 0 else db.execute('''
        WITH groups AS (
            SELECT *, row_number() OVER (
                PARTITION BY host, path_group ORDER BY hash(doc_id, ?), doc_id
            ) AS group_rank FROM source
        ), ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY host ORDER BY group_rank, hash(path_group, ?), doc_id
            ) AS host_rank FROM groups
        )
        SELECT doc_id, url, host, path_group FROM ranked JOIN quotas USING(host)
        WHERE host_rank <= quota ORDER BY host, host_rank
    ''', [args.seed, args.seed])
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.plan.with_suffix('.jsonl.tmp')
    with temporary.open('w', encoding='utf-8') as file:
        while batch := cursor.fetchmany(1000):
            for doc_id, url, host, group in batch:
                file.write(json.dumps(dict(doc_id=doc_id, url=url, host=host,
                                           path_group=group), ensure_ascii=False) + '\n')
    temporary.replace(args.plan)
    save_json(args.plan.with_suffix('.summary.json'), dict(
        corpus_urls=sum(corpus_counts.values()), planned=target, eligible_urls=total,
        all_hosts=args.all_hosts, excluded_checkpoints=[str(p) for p in args.exclude_completed],
        hosts={h: dict(corpus=n, eligible=counts.get(h, 0), planned=quotas.get(h, 0),
                      deferred=h not in hosts) for h, n in sorted(corpus_counts.items())}))
    db.close()
    print(f'Prepared {target:,} URLs / {len(quotas)} hosts: {args.plan}', flush=True)
    print('Excluded hosts without HTML in pilot: ' + ', '.join(sorted(
        {r['host'] for r in pilot} - set(hosts))), flush=True)


def open_state(args, root):
    """One SQLite transaction checkpoints the fetch and parse results together."""
    db = sqlite3.connect(root / 'state.sqlite', timeout=60)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS jobs('
               'doc_id INTEGER PRIMARY KEY, url TEXT, host TEXT, page TEXT, document TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS pending_hosts ON jobs(host,doc_id) WHERE page IS NULL')
    identity = dict(plan_sha256=file_hash(args.plan),
                    shard_count=args.shard_count, shard_id=args.shard_id)
    previous = db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
    if previous and json.loads(previous[0]) != identity:
        raise ValueError('Plan/shard changed. Use a different --output or the original plan/shard.')
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('identity', ?)", [json.dumps(identity)])
    db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_run', ?)", [json.dumps(dict(
        started_at=utc_now(), parser_version=VERSION, workers=args.workers,
        host_workers=args.host_workers,
        delay=args.delay, effective_delay=args.delay * args.shard_count,
        rate_limits=getattr(args, 'rate_limits', {}), timeout=args.timeout, retries=args.retries))])
    if not db.execute("SELECT 1 FROM metadata WHERE key='plan_loaded'").fetchone():
        deferred = {}
        languages = set()
        with args.plan.open(encoding='utf-8') as file:
            batch = []
            for line in file:
                row = json.loads(line)
                if row['doc_id'] % args.shard_count == args.shard_id:
                    batch.append((row['doc_id'], row['url'], row['host']))
                    if row.get('deferred_reason'):
                        deferred[row['host']] = dict(reason='deferred:' + row['deferred_reason'])
                    if row.get('language'):
                        languages.add(row['language'])
                if len(batch) >= 1000:
                    db.executemany('INSERT OR IGNORE INTO jobs(doc_id,url,host) VALUES (?,?,?)', batch)
                    batch.clear()
            db.executemany('INSERT OR IGNORE INTO jobs(doc_id,url,host) VALUES (?,?,?)', batch)
        db.execute("INSERT INTO metadata VALUES ('plan_loaded','1')")
        if deferred:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('paused_hosts',?)", [json.dumps(deferred)])
        if len(languages) == 1:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('language',?)", [languages.pop()])
    db.commit()
    return db


def classify(record, raw):
    sample = raw[:8000].decode('utf-8', errors='ignore').lower()
    # NCBI serves its access screens with both 200 and 203. Detect these
    # before HTTP classification so the shared source pause can take effect.
    host = urlsplit(record.get('final_url') or record['url']).hostname or ''
    if (host == 'ncbi.nlm.nih.gov' or host.endswith('.ncbi.nlm.nih.gov')) and (
            '<title>ncbi - www error blocked diagnostic</title>' in sample
            or ('challengeid' in sample and 'submitsolve' in sample
                and 'browseracceptscookies' in sample)):
        return 'access_challenge'
    if record['http_status'] != 200:
        return 'http_error'
    if not raw:
        return 'empty_response'
    mime = record['content_type']
    if mime and 'html' not in mime:
        return 'non_html'
    if ('denied by http_custom' in sample or 'verify you are human' in sample
            or 'document.cookie' in sample and 'location.reload' in sample
            or 'captcha' in sample and len(raw) < 10000):
        return 'access_challenge'
    source, final = urlsplit(record['url']), urlsplit(record['final_url'])
    if source.path not in ('', '/') and final.path in ('', '/'):
        return 'redirected_home'
    return 'html_response'


def rate_group(host):
    """Observed related subdomains share one request budget."""
    for domain in ('39.net', 'familydoctor.com.cn', 'iiyi.com', 'ncbi.nlm.nih.gov'):
        if host == domain or host.endswith('.' + domain):
            return domain
    return host


def load_rate_limits(path, default_workers, default_delay):
    """Load optional per-rate-group limits without weakening the safe defaults."""
    defaults = dict(host_workers=default_workers, delay=default_delay)
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'Invalid rate-limit config {path}: {error}') from error
    groups = payload.get('groups') if isinstance(payload, dict) else None
    if not isinstance(groups, dict):
        raise ValueError('Rate-limit config must contain an object named "groups"')
    policies = {}
    for group, values in groups.items():
        normalized = group.lower()
        if not isinstance(group, str) or not normalized or rate_group(normalized) != normalized:
            raise ValueError(f'Rate-limit key must be a canonical rate group: {group!r}')
        if not isinstance(values, dict) or set(values) - {'host_workers', 'delay'}:
            raise ValueError(f'Invalid rate-limit policy for {group!r}')
        policy = defaults | values
        if (isinstance(policy['host_workers'], bool) or not isinstance(policy['host_workers'], int)
                or policy['host_workers'] < 1 or not isinstance(policy['delay'], (int, float))
                or isinstance(policy['delay'], bool) or policy['delay'] < 0):
            raise ValueError(f'Invalid worker/delay values for {group!r}')
        policies[normalized] = policy
    return policies


class HostPaused(Exception):
    pass


class Downloader:
    """Limit every HTTP hop by its actual hostname, including redirects/retries."""
    def __init__(self, client, args):
        self.client = client
        self.args = args
        self.slots = asyncio.Semaphore(args.workers)
        self.locks = defaultdict(asyncio.Lock)
        self.host_slots = {}
        self.next_request = defaultdict(float)
        self.paused_groups = set()

    def policy(self, group):
        return getattr(self.args, 'rate_limits', {}).get(group, dict(
            host_workers=self.args.host_workers, delay=self.args.delay))

    def host_slot(self, group):
        if group not in self.host_slots:
            self.host_slots[group] = asyncio.Semaphore(self.policy(group)['host_workers'])
        return self.host_slots[group]

    async def get(self, url):
        host = rate_group(urlsplit(url).hostname)
        if host in self.paused_groups:
            raise HostPaused(host)
        policy = self.policy(host)
        async with self.host_slot(host):
            # Serialize request starts, while allowing previous responses to finish in parallel.
            async with self.locks[host]:
                while self.next_request[host] > time.monotonic():
                    await asyncio.sleep(self.next_request[host] - time.monotonic())
                await self.slots.acquire()
                # A concurrent response may have added Retry-After while waiting for a slot.
                try:
                    while self.next_request[host] > time.monotonic():
                        await asyncio.sleep(self.next_request[host] - time.monotonic())
                    if host in self.paused_groups:
                        raise HostPaused(host)
                except BaseException:
                    self.slots.release()
                    raise
                self.next_request[host] = time.monotonic() + policy['delay'] * self.args.shard_count
            try:
                # Queue/rate-limit waiting is not a network timeout.
                async with asyncio.timeout(self.args.timeout * 2), self.client.stream('GET', url) as response:
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.args.max_bytes:
                            raise ValueError('response_too_large')
                        chunks.append(chunk)
                    return response, b''.join(chunks)
            finally:
                self.slots.release()

    async def fetch(self, url):
        for hop in range(6):
            response, raw = await self.get(url)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response, raw
            location = response.headers.get('location')
            if not location:
                return response, raw
            url = urljoin(str(response.url), location)
        raise ValueError('too_many_redirects')


async def crawl_one(job, downloader, root, args):
    record = dict(job, fetched_at=utc_now(), state='fetch_error', raw_path=None)
    raw = b''
    for attempt in range(args.retries + 1):
        try:
            response, raw = await downloader.fetch(job['url'])
            record.update(http_status=response.status_code, final_url=str(response.url),
                          content_type=response.headers.get('content-type', '').lower(),
                          body_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                          fetched_at=utc_now(), attempts=attempt + 1)
            record['state'] = classify(record, raw)
            record.pop('error', None)
            if response.status_code == 429 or response.status_code in (408, 425, 500, 502, 503, 504):
                # Numeric Retry-After is respected; longer waits defer the host.
                retry_after = response.headers.get('retry-after', '')
                wait = 2 ** (attempt + 1)
                try:
                    wait = float(retry_after) if retry_after.isdigit() else max(0,
                        (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
                wait = max(0, wait)
                record['retry_after_seconds'] = wait
                response_host = rate_group(urlsplit(str(response.url)).hostname)
                downloader.next_request[response_host] = max(downloader.next_request[response_host],
                                                             time.monotonic() + wait)
                if wait > 60 or attempt == args.retries:
                    break
                await asyncio.sleep(wait)
                continue
            break
        except (httpx.HTTPError, TimeoutError, ValueError) as error:
            for key in ('http_status', 'final_url', 'content_type', 'body_bytes', 'sha256',
                        'retry_after_seconds'):
                record.pop(key, None)
            record.update(state='fetch_error', error=f'{type(error).__name__}: {error}',
                          attempts=attempt + 1)
            raw = b''
            if isinstance(error, ValueError) or attempt == args.retries:
                break
            await asyncio.sleep(2 ** (attempt + 1))
    if record['state'] != 'fetch_error':
        record['raw_path'] = f"raw/{job['doc_id']}-{record['sha256'][:16]}.gz"
        target = root / record['raw_path']
        temporary = target.with_suffix('.gz.tmp')
        temporary.write_bytes(gzip.compress(raw, mtime=0))
        temporary.replace(target)
    try:
        document = await asyncio.to_thread(parse_page, record, raw)
    except Exception as error:
        document = dict(doc_id=job['doc_id'], url=job['url'], host=job['host'],
                        status='parser_error', warnings=[f'{type(error).__name__}: {error}'])
    return record, document


def export_results(db, root, started, paused, review_limit=0, storage=None, export_files=True):
    states, statuses, warnings, hosts = Counter(), Counter(), Counter(), defaultdict(Counter)
    versions = Counter()
    bytes_saved, text_count, completed = 0, 0, 0
    review_groups = {}
    with ExitStack() as stack:
        if export_files:
            pages = stack.enter_context((root / 'pages.jsonl.tmp').open('w', encoding='utf-8'))
            documents = stack.enter_context((root / 'documents.jsonl.tmp').open('w', encoding='utf-8'))
        if storage and export_files:
            results = storage.results(db)
        else:
            results = ((json.loads(page), json.loads(doc)) for page, doc in db.execute(
                'SELECT page,document FROM jobs WHERE page IS NOT NULL ORDER BY doc_id'))
        for page, doc in results:
            if export_files:
                pages.write(json.dumps(page, ensure_ascii=False) + '\n')
                documents.write(json.dumps(doc, ensure_ascii=False) + '\n')
            completed += 1
            states[page['state']] += 1
            statuses[doc['status']] += 1
            versions[doc.get('parser_version', 'unknown')] += 1
            warnings.update(doc.get('warnings', []))
            hosts[page['host']][doc['status']] += 1
            bytes_saved += page.get('body_bytes', 0)
            text_count += doc.get('has_text', bool(doc.get('text', '').strip()))
            if review_limit and (page['host'], doc['status']) not in review_groups:
                for key in ('selectors', 'warnings', 'blocks', 'removed', 'outside_headings',
                            'outside_paragraphs', 'resources'):
                    doc.setdefault(key, [])
                doc.setdefault('title', '')
                doc.setdefault('selected_source_text', '')
                review_groups[(page['host'], doc['status'])] = doc
    if export_files:
        for name in ('pages.jsonl', 'documents.jsonl'):
            (root / (name + '.tmp')).replace(root / name)
    planned = db.execute('SELECT count(*) FROM jobs').fetchone()[0]
    summary = dict(updated_at=utc_now(), planned=planned, completed=completed,
                   pending=planned - completed, with_text=text_count,
                   response_bytes=bytes_saved, elapsed_seconds=round(time.monotonic() - started, 1),
                   source_states=dict(states), parser_statuses=dict(statuses),
                   parser_versions=dict(versions),
                   warnings=dict(warnings), hosts={h: dict(c) for h, c in sorted(hosts.items())},
                   paused_hosts=paused)
    save_json(root / 'summary.json', summary)
    if review_groups:
        if storage and not export_files:
            # Read only batches containing the selected samples, not the whole corpus.
            selected_batches = defaultdict(dict)
            for key, doc in review_groups.items():
                if doc.get('_archived'):
                    selected_batches[doc['_archived']][doc['doc_id']] = key
            for batch_id, selected in selected_batches.items():
                for doc in storage.documents(storage.manifest(batch_id)):
                    if doc['doc_id'] in selected:
                        review_groups[selected[doc['doc_id']]] = doc
        priority = {'parser_error': 0, 'unknown_template': 1, 'needs_review': 2,
                    'encoded_content': 3, 'extracted_unreviewed': 4}
        review = sorted(review_groups.values(), key=lambda d: (
            priority.get(d['status'], 5), d['host']))[:review_limit]
        write_review(review, root / 'review.html')
    print(json.dumps({k: summary[k] for k in ('planned', 'completed', 'pending', 'with_text',
                                             'elapsed_seconds', 'parser_statuses')},
                     ensure_ascii=False), flush=True)


async def run(args, db, root, started, paused, storage=None):
    pending_hosts = [row[0] for row in db.execute('SELECT DISTINCT host FROM jobs WHERE page IS NULL')]
    blocked_groups = {rate_group(h): info for h, info in paused.items() if info['reason'] != 'parser_quality'}
    for host in pending_hosts:
        if rate_group(host) in blocked_groups:
            paused.setdefault(host, blocked_groups[rate_group(host)])
    if paused:
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('paused_hosts',?)", [json.dumps(paused)])
        db.commit()
    hosts = [host for host in pending_hosts if host not in paused]
    planned = db.execute('SELECT count(*) FROM jobs').fetchone()[0]
    counts = Counter(dict(db.execute("SELECT json_extract(document, '$.status'), count(*) "
                                    "FROM jobs WHERE page IS NOT NULL GROUP BY 1").fetchall()))
    print(f'Shard {args.shard_id}/{args.shard_count}: {sum(counts.values()):,}/{planned:,} saved; '
          f'{planned-sum(counts.values()):,} pending. Resume is automatic.', flush=True)

    def pending_jobs(host):
        last_id = -1
        while True:
            rows = db.execute('SELECT doc_id,url FROM jobs WHERE host=? AND page IS NULL '
                              'AND doc_id>? ORDER BY doc_id LIMIT 100', [host, last_id]).fetchall()
            if not rows:
                return
            for doc_id, url in rows:
                yield dict(doc_id=doc_id, url=url, host=host)
            last_id = rows[-1][0]

    processed = 0
    scheduled = 0
    remaining_workers = len(hosts) * args.host_workers
    consecutive_errors = Counter()
    parser_streak = Counter()
    recent_parser_errors = defaultdict(lambda: deque(maxlen=50))

    def pause_host(host, reason, page):
        affected = [host] if reason == 'parser_quality' else [h for h in hosts if rate_group(h) == rate_group(host)]
        if reason != 'parser_quality':
            downloader.paused_groups.add(rate_group(host))
        for affected_host in affected:
            paused[affected_host] = dict(reason=reason, http_status=page.get('http_status'), last_doc_id=page['doc_id'])
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('paused_hosts',?)", [json.dumps(paused)])
        db.commit()
        print(f'Paused {host}: {paused[host]}', flush=True)
    async with httpx.AsyncClient(timeout=args.timeout, follow_redirects=False,
                                 headers={'User-Agent': 'MedXIR/0.1 (corpus collection)'},
                                 limits=httpx.Limits(max_connections=args.workers,
                                                     max_keepalive_connections=args.workers)) as client:
        downloader = Downloader(client, args)
        downloader.paused_groups.update(blocked_groups)

        async def crawl_host(host, jobs):
            nonlocal processed, scheduled
            for job in jobs:
                if host in paused:
                    break
                if getattr(args, 'max_new', 0) and scheduled >= args.max_new:
                    break
                scheduled += 1
                try:
                    page, document = await crawl_one(job, downloader, root, args)
                except HostPaused:
                    if host not in paused:
                        pause_host(host, 'redirect_to_paused_group', job)
                    break  # Keep unsent jobs pending, including queued subdomain requests.
                db.execute('UPDATE jobs SET page=?, document=? WHERE doc_id=?',
                           [json.dumps(page, ensure_ascii=False),
                            json.dumps(document, ensure_ascii=False), job['doc_id']])
                db.commit()
                processed += 1
                counts[document['status']] += 1
                bad_parse = (document['status'] in {'unknown_template', 'parser_error', 'encoded_content', 'empty_content'}
                             or 'block_text_mismatch' in document.get('warnings', []))
                parser_streak[host] = parser_streak[host] + 1 if bad_parse else 0
                recent_parser_errors[host].append(bad_parse)
                if storage and processed % 100 == 0:
                    if storage.cache_bytes() > args.cache_limit_gb * 1024**3:
                        raise ValueError('Raw/batch cache limit reached. Pending data retained; '
                                         'upload --prune-local before resuming.')
                if page['state'] in ('access_challenge', 'fetch_error') or (
                        page['state'] == 'http_error' and (
                            page['http_status'] in (401, 403, 407, 408, 425, 429)
                            or page['http_status'] >= 500)):
                    consecutive_errors[rate_group(host)] += 1
                else:
                    consecutive_errors[rate_group(host)] = 0
                if processed % args.progress_every == 0:
                    completed = sum(counts.values())
                    speed = processed / max(0.001, time.monotonic()-started)
                    print(f'{utc_now()} | {completed:,}/{planned:,} ({completed/planned:.1%}) '
                          f'| {speed:.2f} URL/s this run | {host} / {document["status"]}', flush=True)
                    save_json(root / 'summary.json', dict(updated_at=utc_now(), planned=planned,
                              completed=sum(counts.values()), pending=planned-sum(counts.values()),
                              parser_statuses=counts, paused_hosts=paused,
                              elapsed_seconds=round(time.monotonic()-started, 1)))
                if consecutive_errors[rate_group(host)] >= 3 or page.get('retry_after_seconds', 0) > 60:
                    pause_host(host, page['state'], page)
                    break
                if (parser_streak[host] >= 3 or len(recent_parser_errors[host]) == 50
                        and sum(recent_parser_errors[host]) >= 5):
                    pause_host(host, 'parser_quality', page)
                    break

        async def worker(host, jobs):
            nonlocal remaining_workers
            try:
                await crawl_host(host, jobs)
            finally:
                remaining_workers -= 1

        async def upload_batches():
            # Own SQLite connections in the storage thread; HTTP requests keep progressing.
            last_report = time.monotonic()
            while remaining_workers:
                await asyncio.to_thread(storage.flush)
                if time.monotonic() - last_report >= getattr(args, 'followup_every', 300):
                    await asyncio.to_thread(write_report, root, storage)
                    last_report = time.monotonic()
                if storage.cache_bytes() > args.cache_limit_gb * 1024**3:
                    raise ValueError('Raw/batch cache limit reached. Resume after uploading '
                                     'with --prune-local, or increase --cache-limit-gb.')
                await asyncio.sleep(2)
            await asyncio.to_thread(storage.flush, True)

        async def local_reports():
            last_report = time.monotonic()
            while remaining_workers:
                if time.monotonic() - last_report >= getattr(args, 'followup_every', 300):
                    await asyncio.to_thread(write_report, root)
                    last_report = time.monotonic()
                await asyncio.sleep(1)

        # Structured concurrency cancels outstanding requests if any task fails.
        async with asyncio.TaskGroup() as group:
            for host in hosts:
                jobs = pending_jobs(host)
                for _ in range(args.host_workers):
                    group.create_task(worker(host, jobs))
            if storage:
                group.create_task(upload_batches())
            else:
                group.create_task(local_reports())


def show_status(root):
    """Read the current checkpoint, including while another process is crawling."""
    path = root / 'state.sqlite'
    if not path.is_file():
        raise ValueError(f'No checkpoint at {path}')
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')  # One consistent snapshot while the crawler keeps writing.
        planned, completed = db.execute('SELECT count(*), count(page) FROM jobs').fetchone()
        print(f'{root}\nSaved: {completed:,}/{planned:,} ({completed/max(1,planned):.1%}) '
              f'| Pending: {planned-completed:,}')
        print('\nParser statuses:')
        for status, count in db.execute("SELECT json_extract(document,'$.status'), count(*) "
                                       "FROM jobs WHERE page IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC"):
            print(f'  {status}: {count:,}')
        print('\nParser versions:')
        for version, count in db.execute("SELECT json_extract(document,'$.parser_version'),count(*) "
                                        "FROM jobs WHERE page IS NOT NULL GROUP BY 1"):
            print(f'  {version}: {count:,}')
        print('\nHosts with most pending URLs:')
        for host, count in db.execute('SELECT host,count(*) FROM jobs WHERE page IS NULL '
                                     'GROUP BY host ORDER BY count(*) DESC LIMIT 10'):
            print(f'  {host}: {count:,}')
        paused = db.execute("SELECT value FROM metadata WHERE key='paused_hosts'").fetchone()
        if paused:
            print('\nPaused hosts (use --retry-paused after resolving the cause):')
            print(paused[0])


def main():
    # Background processes may inherit SIGINT as ignored; restore a graceful stop.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=Path('dataset/links_corpus_vi_en_zh.parquet'))
    parser.add_argument('--pilot', type=Path, default=Path('artifacts/host_template_audit/pages.jsonl'))
    parser.add_argument('--plan', type=Path, default=Path('artifacts/crawl/plan.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/crawl'))
    parser.add_argument('--limit', type=int, default=10000, help='Plan size; 0 = all eligible URLs')
    parser.add_argument('--seed', type=int, default=20261010)
    parser.add_argument('--fresh-plan', action='store_true', help='Do not exclude historical pilot IDs when creating a NEW plan')
    parser.add_argument('--max-new', type=int, default=0, help='Stop after scheduling this many URLs; 0 = no cap')
    parser.add_argument('--followup-every', type=float, default=300, help='Seconds between local/cloud repair queue snapshots')
    parser.add_argument('--followup-only', action='store_true', help='Initialize checkpoint and publish repair queues without fetching')
    parser.add_argument('--reparse-plan', type=Path, help='With --reparse, restrict to IDs/URLs in this JSONL[.gz] plan')
    parser.add_argument('--exclude-completed', type=Path, action='append', default=[],
                        help='Exclude completed IDs in this state.sqlite when creating a plan; repeatable')
    parser.add_argument('--all-hosts', action='store_true', help='Include hosts without successful pilot HTML in a NEW plan')
    parser.add_argument('--include-host', action='append', default=[], help='Add a newly verified host to a NEW plan; repeatable')
    parser.add_argument('--shard-count', type=int, default=1)
    parser.add_argument('--shard-id', type=int, default=0)
    parser.add_argument('--workers', type=int, default=12, help='Maximum active HTTP requests')
    parser.add_argument('--host-workers', type=int, default=1, help='Maximum parallel requests per hostname')
    parser.add_argument('--delay', type=float, default=1.5, help='Seconds between starts per hostname')
    parser.add_argument('--rate-limits', type=Path,
                        help='JSON overrides by canonical rate group: host_workers and/or delay')
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--retries', type=int, default=2, help='Additional attempts per URL')
    parser.add_argument('--max-bytes', type=int, default=10 * 1024 * 1024)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Require an existing checkpoint and continue it')
    parser.add_argument('--retry-paused', action='store_true', help='Allow pending URLs of paused hosts to run again')
    parser.add_argument('--retry-host', action='append', default=[], metavar='HOST',
                        help='Unpause only this paused host (repeatable); other pauses and deferrals stay')
    parser.add_argument('--status', action='store_true', help='Read progress without crawling or changing the plan')
    parser.add_argument('--progress-every', type=int, default=25, help='Print progress every N completed URLs')
    parser.add_argument('--export-only', action='store_true', help='Export saved checkpoints without fetching')
    parser.add_argument('--reparse', action='store_true', help='Reparse saved raw responses without fetching')
    parser.add_argument('--force-reparse', action='store_true', help='With --reparse, include documents already at this parser version')
    parser.add_argument('--parse-workers', type=int, default=2, help='CPU processes for --reparse; independent of HTTP workers')
    parser.add_argument('--review-limit', type=int, default=0, help='Export HTML for up to N saved documents')
    parser.add_argument('--storage-config', type=Path, help='Shared cloud destination JSON (no tokens)')
    parser.add_argument('--batch-docs', type=int, default=10000)
    parser.add_argument('--batch-mb', type=int, default=256, help='Seal after this many MiB of compressed raw')
    parser.add_argument('--prune-local', action='store_true', help='Evict only verified batches and compact documents')
    parser.add_argument('--cache-limit-gb', type=float, default=4, help='Raw/batch high-water mark; stop if exceeded')
    args = parser.parse_args()
    if (args.limit < 0 or args.shard_count < 1 or not 0 <= args.shard_id < args.shard_count
            or args.workers < 1 or args.delay < 0 or args.timeout <= 0 or args.retries < 0
            or args.max_bytes < 1 or args.review_limit < 0 or args.progress_every < 1
            or args.host_workers < 1 or args.batch_docs < 1 or args.batch_mb < 1
            or args.cache_limit_gb <= 0 or args.parse_workers < 1 or args.max_new < 0
            or args.followup_every <= 0):
        parser.error('Invalid limits, shard, or concurrency configuration')
    if args.prune_local and not args.storage_config:
        parser.error('--prune-local requires --storage-config')
    if args.force_reparse and not args.reparse:
        parser.error('--force-reparse requires --reparse')
    if args.reparse_plan and not args.reparse:
        parser.error('--reparse-plan requires --reparse')
    if args.fresh_plan and args.exclude_completed:
        parser.error('--fresh-plan cannot exclude historical checkpoints')
    if (args.retry_paused or args.retry_host) and any((args.status, args.prepare_only, args.export_only,
                                                        args.reparse, args.followup_only)):
        parser.error('--retry-paused/--retry-host are only for crawling pending URLs')
    if sum((args.status, args.prepare_only, args.export_only, args.reparse, args.followup_only)) > 1:
        parser.error('Choose one mode: --status, --prepare-only, --export-only, or --reparse')
    root = args.output / f'shard-{args.shard_id:03d}-of-{args.shard_count:03d}'
    if args.status:
        try:
            show_status(root)
        except ValueError as error:
            parser.error(str(error))
        return
    if args.storage_config and not args.storage_config.is_file():
        parser.error('Missing shared storage config. Log in with rclone config, then run '
                     'python scripts/storage.py init to create storage.json')
    try:
        args.rate_limits = load_rate_limits(args.rate_limits, args.host_workers, args.delay)
    except ValueError as error:
        parser.error(str(error))
    if args.resume and (not (root / 'state.sqlite').is_file() or not args.plan.is_file()):
        parser.error('--resume requires an existing checkpoint and plan for this shard')
    prepare_plan(args)
    print(f'Using plan: {args.plan} (existing plans are reused unchanged)', flush=True)
    if args.prepare_only:
        return
    (root / 'raw').mkdir(parents=True, exist_ok=True)
    # Prevent two local processes from writing the same shard (also across crashes).
    import fcntl
    with (root / '.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error(f'Another crawler is already using {root}')
        db = open_state(args, root)
        try:
            storage = Storage(root, args.storage_config, args.batch_docs, args.batch_mb,
                              args.prune_local) if args.storage_config else None
        except (ValueError, OSError) as error:
            db.close()
            parser.error(str(error))
        if not storage and 'archive' in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
            if db.execute("SELECT 1 FROM jobs WHERE json_extract(document,'$._archived') IS NOT NULL LIMIT 1").fetchone():
                parser.error('Archived documents require --storage-config to read full results')
        saved_paused = db.execute("SELECT value FROM metadata WHERE key='paused_hosts'").fetchone()
        paused = json.loads(saved_paused[0]) if saved_paused else {}
        if args.retry_paused:
            paused = {}
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('paused_hosts','{}')")
            db.commit()
        if args.retry_host:
            for host in args.retry_host:
                if host in paused:
                    print(f'Retrying paused host {host}: {paused.pop(host)}', flush=True)
                else:
                    print(f'--retry-host {host}: not paused, nothing to do', flush=True)
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('paused_hosts',?)", [json.dumps(paused)])
            db.commit()
        started = time.monotonic()
        try:
            if args.reparse:
                columns = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
                archive_column = 'archive' if 'archive' in columns else 'NULL'
                condition = "page IS NOT NULL"
                parameters = []
                if args.reparse_plan:
                    db.execute('CREATE TEMP TABLE repair_ids(doc_id INTEGER PRIMARY KEY)')
                    opener = gzip.open if args.reparse_plan.suffix == '.gz' else open
                    with opener(args.reparse_plan, 'rt', encoding='utf-8') as repair_file:
                        for line in repair_file:
                            item = json.loads(line)
                            row = db.execute('SELECT url FROM jobs WHERE doc_id=?', [item['doc_id']]).fetchone()
                            if row and row[0] != item['url']:
                                raise ValueError(f'Reparse plan URL mismatch: {item["doc_id"]}')
                            if row:
                                db.execute('INSERT OR IGNORE INTO repair_ids VALUES (?)', [item['doc_id']])
                    condition += ' AND doc_id IN (SELECT doc_id FROM repair_ids)'
                if not args.force_reparse:
                    condition += " AND coalesce(json_extract(document,'$.parser_version'),'') != ?"
                    parameters = [VERSION]
                # Read IDs up front: an open SELECT keeps a WAL read snapshot, and the
                # storage connection's batch writes would then make our UPDATE fail.
                ids = [row[0] for row in db.execute(
                    f'SELECT doc_id FROM jobs WHERE {condition} ORDER BY doc_id', parameters)]
                total = len(ids)
                print(f'Reparse {total:,} saved documents with {VERSION}; no source HTTP requests.', flush=True)
                processed = 0
                with ExitStack() as stack:
                    pool = stack.enter_context(ProcessPoolExecutor(args.parse_workers,
                        mp_context=get_context('spawn'))) if args.parse_workers > 1 else None
                    # Bound queued raw/results to a small batch, not the whole corpus.
                    step = args.parse_workers * 2
                    for start in range(0, total, step):
                        chunk = ids[start:start + step]
                        batch = db.execute(f'SELECT doc_id,page,{archive_column} FROM jobs WHERE doc_id IN '
                                           f'({",".join("?" * len(chunk))}) ORDER BY doc_id', chunk).fetchall()
                        tasks = []
                        for doc_id, page_json, archive_id in batch:
                            page = json.loads(page_json)
                            # Read failures stop the run; retain the previous good document.
                            raw = storage.raw(page, archive_id) if storage else (
                                gzip.decompress((root / page['raw_path']).read_bytes()) if page.get('raw_path') else b'')
                            if storage and page.get('raw_path') and not (root / page['raw_path']).exists():
                                (root / page['raw_path']).write_bytes(gzip.compress(raw, mtime=0))
                            tasks.append((doc_id, page, pool.submit(parse_page, page, raw) if pool else raw))
                        for doc_id, page, task in tasks:
                            try:
                                document = task.result() if pool else parse_page(page, task)
                            except Exception as error:
                                document = dict(doc_id=doc_id, url=page['url'], host=page['host'],
                                                status='parser_error', warnings=[f'{type(error).__name__}: {error}'])
                            db.execute('UPDATE jobs SET document=? WHERE doc_id=?',
                                       [json.dumps(document, ensure_ascii=False), doc_id])
                            if 'archive' in columns:
                                db.execute('UPDATE jobs SET archive=NULL WHERE doc_id=?', [doc_id])
                            db.commit()
                            processed += 1
                            if processed % args.progress_every == 0 or processed == total:
                                print(f'Reparse {processed:,}/{total:,} | {document["status"]} | doc_id={doc_id}', flush=True)
                            if storage and processed % 100 == 0:
                                storage.flush()
                if storage:
                    storage.flush(force=True)
            elif not args.export_only and not args.followup_only:
                asyncio.run(run(args, db, root, started, paused, storage))
        except KeyboardInterrupt:
            print('Interrupted; saved jobs will be skipped on resume.', flush=True)
        except ExceptionGroup as error:
            print(f'Crawler stopped: {error.exceptions}. Resume keeps completed jobs.', flush=True)
            raise SystemExit(1)
        finally:
            try:
                export_results(db, root, started, paused, args.review_limit, storage,
                               export_files=not storage or args.export_only)
                report = write_report(root, storage)
                print(f'Follow-up queue: {report["outstanding"]:,}/{report["total"]:,} IDs; '
                      f'{root / "followup" / "documents.csv.gz"}', flush=True)
                pending = db.execute('SELECT count(*) FROM jobs WHERE page IS NULL').fetchone()[0]
            finally:
                db.close()
        if pending and not (args.reparse or args.export_only or args.followup_only):
            print(f'{pending:,} URLs still pending. Inspect --status; resume after resolving paused hosts.', flush=True)
            raise SystemExit(2)


if __name__ == '__main__':
    main()
