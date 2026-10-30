"""CPU Kaggle job: parse audited offline HTML and write an isolated additions corpus."""
from pathlib import Path
from collections import Counter
from urllib.request import urlopen
from urllib.parse import quote
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import shutil
import sqlite3
import time

import pyarrow as pa
import pyarrow.parquet as pq
import offline_recovery_parser as worker
from chunking import chunk_document, size
from text_clean import clean_text, CLEAN_VERSION
from dedup_groups import url_key


CHUNK_SCHEMA = pa.schema([
    ('chunk_id', pa.string()), ('text_sha256', pa.string()), ('rep_doc_id', pa.int64()),
    ('lang', pa.string()), ('order', pa.int32()), ('start', pa.int64()), ('end', pa.int64()),
    ('chunk_text', pa.string()), ('n_units', pa.int32()), ('title', pa.string()),
    ('clean_version', pa.string()), ('chunk_params', pa.string()),
])


class Sink:
    def __init__(self, path, schema=None):
        self.path, self.schema, self.writer, self.rows, self.count = path, schema, None, [], 0

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) >= 512:
            self.flush()

    def flush(self):
        if not self.rows:
            return
        table = pa.Table.from_pylist(self.rows, schema=self.schema)
        if self.writer is None:
            self.schema = table.schema
            self.writer = pq.ParquetWriter(self.path, self.schema, compression='zstd')
        self.writer.write_table(table)
        self.count += len(self.rows)
        self.rows.clear()

    def close(self):
        self.flush()
        if self.writer:
            self.writer.close()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_jsonl(path):
    path = Path(path)
    if not path.exists() and path.suffix == '.gz':
        path = path.with_suffix('')
    handle = gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz' else path.open(encoding='utf-8')
    with handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def run():
    started = time.monotonic()
    root = next(Path('/kaggle/input').rglob('recovery_manifest.json')).parent
    spec = json.loads((root / 'recovery_manifest.json').read_text())
    out = Path('/kaggle/working/recovery_release_20261029_v2')
    out.mkdir(exist_ok=True)
    scratch = Path('/kaggle/working/recovery_scratch')
    scratch.mkdir(exist_ok=True)
    assert sha(root / 'source_matches.parquet') == spec['source_matches_sha256']
    matches = pq.read_table(root / 'source_matches.parquet').to_pylist()
    expected = {r['doc_id']: r for r in matches}
    assert len(expected) == len(matches)
    files = json.loads((root / 'source_files.json').read_text())
    paths = {r['path']: r for r in files}
    by_file = {}
    for row in matches:
        by_file.setdefault(row['source_path'], []).append(row)

    base_manifest_path = next(Path('/kaggle/input').rglob('chunk_manifest.json'))
    baseline = json.loads(base_manifest_path.read_text())
    assert baseline['dataset_version'] == spec['baseline_version']
    assert baseline['dataset_built_at'] == spec['baseline_built_at']
    assert baseline['chunk_params'] == '350/50/40' and baseline['clean_version'] == CLEAN_VERSION
    import inspect
    params=inspect.signature(chunk_document).parameters
    assert [params[n].default for n in ['limit','overlap','minimum']]==[350,50,40]
    base_hashes = {}
    for batch in pq.ParquetFile(base_manifest_path.parent / 'hash_groups.parquet').iter_batches(
            batch_size=65536, columns=['text_sha256', 'rep_doc_id', 'group_type', 'fanout_safe']):
        for r in batch.to_pylist():
            base_hashes[r['text_sha256']] = (r['rep_doc_id'], r['group_type'], r['fanout_safe'])
    base_prefixes = {h[:16]: h for h in base_hashes}

    db = sqlite3.connect(scratch / 'parsed.sqlite')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE docs(id INTEGER PRIMARY KEY,sha TEXT,url TEXT,host TEXT,lang TEXT,title TEXT,text TEXT,metadata TEXT)')
    review = gzip.open(out / 'needs_review.jsonl.gz', 'wt', encoding='utf-8')
    counters, file_audit = Counter(), []
    parent = None
    parent_root = None
    stub_ids = set()
    archive_revalidated_ids = set()
    short_qa_retry_ids = set()

    def save(d):
        i = d['doc_id']
        e = expected.get(i)
        if e is not None:
            assert e['url'] == d['url'] and e['lang'] == d['lang']
        if not d.get('text', '').strip():
            return
        h = hashlib.sha256(d['text'].encode()).hexdigest()
        metadata = {k: v for k, v in d.items() if k != 'text'}
        db.execute('INSERT OR IGNORE INTO docs VALUES (?,?,?,?,?,?,?,?)',
                   (i, h, d['url'], d['host'], d['lang'], d.get('title', ''),
                    d['text'], json.dumps(metadata, ensure_ascii=False)))

    seed_path = root / 'seed_documents.jsonl.gz'
    raw = gzip.decompress(seed_path.read_bytes()) if seed_path.exists() else (root / 'seed_documents.jsonl').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == spec['seed_raw_sha256']
    seeds = [json.loads(line) for line in raw.decode().split('\n') if line.strip()]
    assert len({d['doc_id'] for d in seeds}) == spec['seed_count']
    if os.environ.get('MEDXIR_PARENT_RELEASE') == '1':
        parent_path = next(Path('/kaggle/input').rglob('release_manifest.json'))
        parent_root = parent_path.parent
        parent = json.loads(parent_path.read_text())
        assert parent['release'] == spec['release']
        assert parent['baseline_manifest'] == baseline
        docs_path = parent_root / 'documents_additions_20261029_v2.parquet'
        assert sha(docs_path) == parent['files'][docs_path.name]['sha256']
        seed_index = {d['doc_id']: d for d in seeds}
        for batch in pq.ParquetFile(docs_path).iter_batches(batch_size=512):
            for d in batch.to_pylist():
                if (d.get('source') == 'snute_offline_recovery' and d['doc_id'] in expected
                        and expected[d['doc_id']]['source_path'].startswith('db_wayback/')):
                    archive_revalidated_ids.add(d['doc_id'])
                    continue
                if len(clean_text(d['text'], d['lang'])) < 30 and not d.get('complete_qa', False):
                    stub_ids.add(d['doc_id'])
                    continue
                d['warnings'] = json.loads(d.pop('warnings_json'))
                if d['doc_id'] in seed_index:
                    original = seed_index[d['doc_id']]
                    assert original['text'] == d['text']
                    d = {**original, **d}
                elif d.get('source') == 'snute_offline_recovery' and d['doc_id'] in expected:
                    d.setdefault('source_path', expected[d['doc_id']]['source_path'])
                save(d)
        stub_ids.update(json.loads(os.environ.get('MEDXIR_STUB_IDS', '[]')))
        assert stub_ids <= set(expected)
        # Older needs_review payloads contain only short labels. Retry their HTML,
        # plus sources interrupted or not processed in the parent job.
        for d in seeds:
            if len(clean_text(d['text'], d['lang'])) < 30:
                stub_ids.add(d['doc_id'])
        previous_audit = json.loads((parent_root / 'file_audit.json').read_text())
        for row in read_jsonl(parent_root / 'needs_review.jsonl.gz'):
            qa_host = row.get('host') in {'www.120ask.com', 'ask.39.net', 'ask.familydoctor.com.cn'}
            short_accepted_status = (row.get('status') == 'extracted_unreviewed'
                                     or qa_host and row.get('status') == 'needs_review')
            # The parent records raw length; cleaning may make it shorter.
            # Retry accepted-status QA candidates and let the parser verify
            # complete question/answer content instead of guessing a raw limit.
            if short_accepted_status:
                short_qa_retry_ids.add(row['doc_id'])
        successful = {r['path'] for r in previous_audit if r['status'] in {'parsed', 'covered_by_seed'}}
        for name in list(by_file):
            if name in successful:
                by_file[name] = [r for r in by_file[name]
                                 if r['doc_id'] in stub_ids or r['host'] in {'laodong.vn','zysjonline.com',
                                     'vietnamnet.vn','article.iiyi.com','baidianfeng.familydoctor.com.cn','pubmed.ncbi.nlm.nih.gov'}
                                 or r['doc_id'] in archive_revalidated_ids or r['doc_id'] in short_qa_retry_ids]
            if not by_file[name]:
                del by_file[name]
        extra_path = os.environ.get('MEDXIR_EXTRA_SOURCE_FILES')
        if extra_path:
            # The metadata audit selected the first stored HTML per ID. If that
            # snapshot fails parsing, try another archived copy of the same URL.
            extra_files = json.loads(Path(extra_path).read_text())
            current_done = {r[0] for r in db.execute('SELECT id FROM docs')}
            remaining_by_host = {}
            for r in matches:
                if r['doc_id'] not in current_done:
                    remaining_by_host.setdefault(r['host'], []).append(r)
            for entry in extra_files:
                name = entry['path']
                host = name.rsplit('/', 1)[-1].removesuffix('.sqlite')
                alternate = [r for r in remaining_by_host.get(host, []) if r['source_path'] != name]
                if alternate:
                    paths[name] = entry
                    combined = {r['doc_id']: r for r in by_file.get(name, [])}
                    combined.update({r['doc_id']: r for r in alternate})
                    by_file[name] = list(combined.values())
    else:
        for d in seeds:
            assert d['baseline_state'] != 'in_corpus'
            save(d)
    db.commit()
    done = {r[0] for r in db.execute('SELECT id FROM docs')}

    # Four forked workers; blobs are bounded and submitted in groups of 64.
    # Working disk holds one source DB at a time (largest 14.75 GB) plus outputs.
    with mp.get_context('fork').Pool(4) as pool:
        for name in sorted(by_file, key=lambda n: paths[n]['size']):
            entry = paths[name]
            targets = {r['doc_id']: r for r in by_file[name] if r['doc_id'] not in done}
            audit = dict(path=name, targets=len(targets))
            if not targets:
                audit['status'] = 'covered_by_seed'; file_audit.append(audit); continue
            if time.monotonic() - started > 27000:
                audit['status'] = 'time_budget'; file_audit.append(audit); break
            if shutil.disk_usage(scratch).free < entry['size'] + 1024 ** 3:
                audit['status'] = 'disk_budget'; file_audit.append(audit); continue
            local = scratch / 'source.sqlite'
            print(f'Parse {name}: {len(targets):,} IDs; {entry["size"]/1e9:.2f} GB', flush=True)
            try:
                url = ('https://huggingface.co/datasets/' + spec['source_repository'] + '/resolve/'
                       + spec['source_revision'] + '/' + quote(name, safe='/') + '?download=true')
                for attempt in range(3):
                    try:
                        with urlopen(url, timeout=120) as response, local.open('wb') as f:
                            while True:
                                block = response.read(4 * 1024 * 1024)
                                if not block:
                                    break
                                f.write(block)
                        assert local.stat().st_size == entry['size']
                        if entry.get('lfs'):
                            assert sha(local) == entry['lfs']['oid']
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(2 ** (attempt + 1))
                source_db = sqlite3.connect(local.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
                good, bad = 0, 0
                try:
                    source_db.execute('PRAGMA query_only=ON')
                    source_db.execute('PRAGMA cache_size=-32768')
                    target_ids = sorted(targets)
                    for pos in range(0, len(target_ids), 64):
                        if time.monotonic() - started > 27000:
                            raise TimeoutError('parse_time_budget')
                        ids = target_ids[pos:pos+64]
                        sql = 'SELECT id,url,final_url,ctype,html FROM pages WHERE status=200 AND length(html)<2097152 AND id IN (' + ','.join('?' for _ in ids) + ')'
                        tasks = []
                        for i, u, final, ctype, blob in source_db.execute(sql, ids):
                            e = targets[i]
                            if u != e['url']:
                                review.write(json.dumps(dict(doc_id=i, url=e['url'], observed_url=u,
                                    source_path=name, accepted=False, status='source_url_mismatch')) + '\n')
                                bad += 1
                                continue
                            tasks.append((i, u, e['host'], e['lang'], final, ctype, blob, name))
                        for d in pool.imap(worker.parse_html, tasks, chunksize=4):
                            if d['accepted']:
                                save(d); done.add(d['doc_id']); good += 1
                            else:
                                review.write(json.dumps(d, ensure_ascii=False) + '\n'); bad += 1
                        db.commit()
                        if pos and pos % 4096 == 0:
                            print(f'  {pos+len(ids):,}/{len(target_ids):,} checked; accepted={good:,}', flush=True)
                finally:
                    source_db.close()
                audit.update(status='parsed', accepted=good, review=bad, not_returned=len(targets)-good-bad)
            except Exception as exc:
                audit.update(status='error', error_type=type(exc).__name__, http_code=getattr(exc, 'code', None))
                print('  source failed:', audit['error_type'], flush=True)
            finally:
                if local.exists(): local.unlink()
            file_audit.append(audit)
            (out / 'file_audit.json').write_text(json.dumps(file_audit, indent=2))
    (out / 'file_audit.json').write_text(json.dumps(file_audit, indent=2))
    if parent_root is not None:
        # Preserve parent rejections that have not subsequently been recovered.
        for row in read_jsonl(parent_root / 'needs_review.jsonl.gz'):
            if row['doc_id'] not in done:
                review.write(json.dumps(row, ensure_ascii=False) + '\n')
    review.close()
    filtered_review = out / 'needs_review.filtered.jsonl.gz'
    with gzip.open(out / 'needs_review.jsonl.gz', 'rt', encoding='utf-8') as old_review, \
         gzip.open(filtered_review, 'wt', encoding='utf-8') as new_review:
        for line in old_review:
            if json.loads(line)['doc_id'] not in done:
                new_review.write(line)
    filtered_review.replace(out / 'needs_review.jsonl.gz')
    db.execute('CREATE INDEX docs_sha ON docs(sha)'); db.commit()

    doc_sink = Sink(out / 'documents_additions_20261029_v2.parquet')
    accepted_statuses, accepted_warnings = Counter(), Counter()
    for i, h, url, host, lang, title, text, metadata in db.execute('SELECT * FROM docs ORDER BY id'):
        meta = json.loads(metadata)
        accepted_statuses[meta.get('status', '')] += 1
        accepted_warnings.update(meta.get('warnings', []))
        doc_sink.add(dict(doc_id=i, url=url, host=host, lang=lang, title=title, text=text,
                          text_sha256=h, status=meta.get('status', ''), parser_version=meta.get('parser_version', ''),
                          source=meta.get('source', '') or '', source_path=meta.get('source_path', '') or '',
                          source_final_url=meta.get('source_final_url', meta.get('final_url', '')) or '',
                          source_html_sha256=meta.get('source_html_sha256', meta.get('source_sha256', '')) or '',
                          page_type=meta.get('page_type', '') or '', complete_qa=bool(meta.get('complete_qa', False)),
                          resources_json=json.dumps(meta.get('resources', []), ensure_ascii=False),
                          warnings_json=json.dumps(meta.get('warnings', []), ensure_ascii=False)))
    doc_sink.close()
    chunk_sink = Sink(out / 'chunk_corpus_additions_20261029_v2.parquet', CHUNK_SCHEMA)
    group_sink = Sink(out / 'hash_groups_additions_20261029_v2.parquet')
    alias_sink = Sink(out / 'aliases_to_existing_20261029_v2.parquet')
    todo_sink = Sink(out / 'embedding_todo_20261029_v2.parquet')
    new_prefixes, accepted_by_lang = {}, Counter()
    for h, rep, n in db.execute('SELECT sha,min(id),count(*) FROM docs GROUP BY sha ORDER BY sha'):
        members = db.execute('SELECT id,url,host FROM docs WHERE sha=? ORDER BY id', (h,)).fetchall()
        if h in base_hashes:
            old_rep, old_type, old_safe = base_hashes[h]
            for i, url, host in members:
                alias_sink.add(dict(doc_id=i, url=url, text_sha256=h, existing_rep_doc_id=old_rep,
                                    existing_group_type=old_type, existing_fanout_safe=bool(old_safe),
                                    decision='mapping_review_required'))
            continue
        assert h[:16] not in base_prefixes or base_prefixes[h[:16]] == h, 'chunk prefix collision with baseline'
        assert h[:16] not in new_prefixes or new_prefixes[h[:16]] == h, 'new chunk prefix collision'
        new_prefixes[h[:16]] = h
        lang, title, text = db.execute('SELECT lang,title,text FROM docs WHERE id=?', (rep,)).fetchone()
        keys = {url_key(url) for _, url, _ in members}; hosts = {host for _, _, host in members}
        typ = 'single' if n == 1 else 'url_variant' if len(keys) == 1 else 'cross_host' if len(hosts) > 1 else 'same_host_diff_path'
        group_sink.add(dict(text_sha256=h, rep_doc_id=rep, doc_ids=[i for i, _, _ in members], n_ids=n,
                            group_type=typ, fanout_safe=typ in {'single', 'url_variant'}))
        cleaned = clean_text(text, lang)
        for c in chunk_document(cleaned, lang):
            assert cleaned[c.start:c.end] == c.text
            cid = f'{h[:16]}-{c.order}'
            chunk_sink.add(dict(chunk_id=cid, text_sha256=h, rep_doc_id=rep, lang=lang, order=c.order,
                                start=c.start, end=c.end, chunk_text=c.text, n_units=size(c.text, lang),
                                title=title, clean_version=CLEAN_VERSION, chunk_params='350/50/40'))
            todo_sink.add(dict(chunk_id=cid, text_sha256=h, chunk_text_sha256=hashlib.sha256(c.text.encode()).hexdigest(),
                               embedding_state='check_each_model_inventory_before_embedding'))
            accepted_by_lang[lang] += 1
    for sink in [chunk_sink, group_sink, alias_sink, todo_sink]: sink.close()
    original_vi = json.loads((root / 'all_vi_new_records.json').read_text())
    wanted_vi = {r['doc_id'] for r in original_vi if r['usable_text']}
    included = {r[0] for r in db.execute('SELECT id FROM docs')}
    missing_vi = sorted(wanted_vi - included)
    (out / 'vi_crawl_ids_not_in_release.json').write_text(json.dumps(missing_vi))
    unresolved = [dict(doc_id=i, url=r['url'], host=r['host'], lang=r['lang'],
                       source_path=r['source_path'], reason='not_accepted_after_recovery')
                  for i, r in expected.items() if i not in included]
    pq.write_table(pa.Table.from_pylist(unresolved, schema=pa.schema([
        ('doc_id', pa.int64()), ('url', pa.string()), ('host', pa.string()),
        ('lang', pa.string()), ('source_path', pa.string()), ('reason', pa.string())])),
        out / 'unresolved_recovery_ids_20261029_v2.parquet', compression='zstd')
    report = dict(release=spec['release'], baseline_manifest=baseline, accepted_document_ids=doc_sink.count,
                  accepted_documents_by_language=dict(db.execute('SELECT lang,count(*) FROM docs GROUP BY lang')),
                  accepted_parser_statuses=dict(accepted_statuses), accepted_parser_warnings=dict(accepted_warnings),
                  unresolved_source_candidate_ids=len(unresolved),
                  source_candidate_ids=len(matches), seed_ids=len(seeds), new_text_groups=group_sink.count,
                  new_chunks=chunk_sink.count, alias_document_ids=alias_sink.count, chunks_by_language=dict(accepted_by_lang),
                  vi_completed_ids_not_in_baseline=spec['all_vi_not_in_v1'], vi_with_text_expected=len(wanted_vi),
                  vi_with_text_included=len(wanted_vi & included), vi_with_text_missing=len(missing_vi),
                  source_file_statuses=dict(Counter(r['status'] for r in file_audit)),
                  baseline_modified=False, embeddings_generated=False, elapsed_seconds=round(time.monotonic()-started, 1),
                  replaces_previous_addition_files=True,
                  provenance=dict(source_repository=spec['source_repository'], source_revision=spec['source_revision'],
                                  source_matches_sha256=spec['source_matches_sha256'],
                                  official_parquet_sha256=spec.get('official_sha256'),
                                  baseline_registry_sha256=spec.get('baseline_registry_sha256'),
                                  seed_raw_sha256=spec['seed_raw_sha256'],
                                  implementation_sha256=sha(__file__),
                                  module_sha256={name: sha(Path(__file__).parent / name) for name in
                                      ['content_parser.py', 'text_clean.py', 'chunking.py',
                                       'dedup_groups.py', 'offline_recovery_parser.py']}),
                  limitations=['Accepted documents may retain parser warnings; inspect their warnings_json column.',
                               'Aliases to existing text require mapping review; no automatic fanout.',
                               'Compare chunk inventory separately for every model/revision before embedding.'])
    if parent is not None:
        (out / 'parent_file_audit.json').write_text(json.dumps(previous_audit, indent=2))
        effective_audit = {r['path']: r for r in previous_audit}
        effective_audit.update({r['path']: r for r in file_audit})
        report['source_file_statuses'] = dict(Counter(r['status'] for r in effective_audit.values()))
        report['parent_source_file_statuses'] = parent.get('source_file_statuses', {})
        report['parent_elapsed_seconds'] = parent.get('elapsed_seconds')
        report['parent_release_manifest_sha256'] = sha(parent_root / 'release_manifest.json')
        report['source_candidate_ids'] = parent['source_candidate_ids']
        report['recovered_from_parent_document_ids'] = parent['accepted_document_ids']
        report['removed_short_crawl_stub_ids'] = sorted(stub_ids)
        report['removed_short_crawl_stubs_recovered'] = len(stub_ids & included)
        report['vi_with_text_count_is_not_quality_approval'] = True
        report['archive_parent_ids_revalidated'] = len(archive_revalidated_ids)
        report['short_qa_ids_retried'] = len(short_qa_retry_ids)
        report['short_qa_ids_recovered'] = len(short_qa_retry_ids & included)
    db.close()
    for p in scratch.glob('parsed.sqlite*'): p.unlink()
    report['files'] = {p.name: dict(bytes=p.stat().st_size, sha256=sha(p)) for p in out.iterdir()
                       if p.is_file() and p.name != 'release_manifest.json'}
    (out / 'release_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in ['files', 'baseline_manifest']}, indent=2))


if __name__ == '__main__':
    run()
