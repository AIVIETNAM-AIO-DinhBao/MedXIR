"""Package the exported corpus as a Kaggle dataset: documents + a registry of every official ID.

Inputs (read-only):
  artifacts/corpus_v1/{en,vi,zh}.parquet         export_corpus.py output
  artifacts/recrawl_20261015/{lang}/plan.jsonl   lang/host of every official ID
  --followup documents.csv.gz ...                per-ID crawl outcome of each shard
                                                 (followup/ of every shard, local or from Drive)

Output (<output>/):
  docs/<lang>/part-<version>-NNN.parquet  doc_id, lang, url, host, title, text, status,
                                          parser_version, source, n_chars, text_sha256
  registry.parquet                        one row per official ID: state in_corpus / blocked /
                                          pending / failed, why, and where the text came from
  manifest.json                           counts, sha256 of every file, notes
  dataset-metadata.json                   for `kaggle datasets create -p <output>`

A later crawl selects registry rows with state != 'in_corpus', writes its documents to new
part-<version> files and rebuilds the registry, so no ID is fetched or stored twice.

    uv run python scripts/build_kaggle_dataset.py --version v1 \
        --followup artifacts/followup_20261021/*/documents.csv.gz --kaggle-id <user>/medxir-corpus
"""
import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

LANGS = ('en', 'vi', 'zh')
DOC_SCHEMA = pa.schema([
    ('doc_id', pa.int64()), ('lang', pa.string()), ('url', pa.string()), ('host', pa.string()),
    ('title', pa.string()), ('text', pa.string()), ('status', pa.string()),
    ('parser_version', pa.string()), ('source', pa.string()), ('n_chars', pa.int32()),
    ('text_sha256', pa.string()),
])
REGISTRY_SCHEMA = pa.schema([
    ('doc_id', pa.int64()), ('url', pa.string()), ('lang', pa.string()), ('host', pa.string()),
    ('state', pa.string()), ('reason', pa.string()), ('source', pa.string()),
    ('status', pa.string()), ('parser_version', pa.string()), ('n_chars', pa.int32()),
    ('text_sha256', pa.string()), ('added_in', pa.string()),
])
# Sources that refuse our crawler (decision of 2026-10-17): not in the BTC labels, never retried.
# PubMed is not here: EN comes from Europe PMC. The Pharmacity beta host moved to www.pharmacity.vn.
BLOCKED_DOMAINS = ('120ask.com', 'familydoctor.com.cn', 'zysjonline.com', 'iiyi.com', 'laodong.vn')
# Follow-up categories of IDs that are not in the corpus -> registry state.
CATEGORY_STATE = {
    'deferred': 'failed',  # blocked hosts are matched by domain first; other deferrals (Pharmacity beta) failed
    'paused': 'pending', 'pending': 'pending',
    'crawl_failed': 'failed', 'parse_failed': 'failed', 'incomplete': 'failed',
    'needs_review': 'failed', 'extracted_unreviewed': 'failed', 'not_found': 'failed',
}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def write_docs(corpus, output, version, part_chars):
    """Copy each language's documents into parts of about part_chars characters."""
    found, files = {}, []
    for lang in LANGS:
        folder = output / 'docs' / lang
        folder.mkdir(parents=True, exist_ok=True)
        writer, part, chars = None, 0, 0
        for batch in pq.ParquetFile(corpus / f'{lang}.parquet').iter_batches(batch_size=5000):
            rows = batch.to_pydict()
            texts = rows['text']
            columns = {
                'doc_id': rows['doc_id'], 'lang': rows['lang'], 'url': rows['url'],
                'host': rows['host'], 'title': rows['title'], 'text': texts,
                'status': rows['status'], 'parser_version': rows['parser_version'],
                'source': rows['source'], 'n_chars': [len(t) for t in texts],
                'text_sha256': [hashlib.sha256(t.encode('utf-8')).hexdigest() for t in texts],
            }
            if writer is None or chars >= part_chars:
                if writer:
                    writer.close()
                path = folder / f'part-{version}-{part:03d}.parquet'
                files.append(path)
                writer, part, chars = pq.ParquetWriter(path, DOC_SCHEMA, compression='zstd'), part + 1, 0
            writer.write_table(pa.table(columns, schema=DOC_SCHEMA))
            chars += sum(columns['n_chars'])
            for i, doc_id in enumerate(columns['doc_id']):
                found[doc_id] = (columns['source'][i], columns['status'][i], columns['parser_version'][i],
                                 columns['n_chars'][i], columns['text_sha256'][i])
        if writer:
            writer.close()
        print(f'{lang}: {part} parts; {len(found):,} documents so far', flush=True)
    return found, files


def followup_outcomes(paths):
    """doc_id -> (category, reason) of IDs a shard did not extract; later files win."""
    outcomes = {}
    for path in paths:
        with gzip.open(path, 'rt', encoding='utf-8', newline='') as file:
            for row in csv.DictReader(file):
                detail = row.get('reason') or row.get('parse_status') or row.get('warnings') or ''
                if row.get('http_status') and row.get('http_status') not in ('', '200'):
                    detail = f"http_{row['http_status']}"
                outcomes[int(row['doc_id'])] = (row['category'], detail)
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--corpus', type=Path, default=Path('artifacts/corpus_v1'))
    parser.add_argument('--root', type=Path, default=Path('artifacts/recrawl_20261015'))
    parser.add_argument('--links', type=Path, default=Path('dataset/links_corpus_vi_en_zh.parquet'))
    parser.add_argument('--followup', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, default=Path('artifacts/kaggle/medxir-corpus'))
    parser.add_argument('--version', default='v1')
    parser.add_argument('--part-chars', type=int, default=400_000_000, help='Characters of text per part file')
    parser.add_argument('--kaggle-id', default='USERNAME/medxir-corpus')
    parser.add_argument('--note', action='append', default=[], help='Free-text note for manifest.json')
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f'{args.output} is not empty; build each version into a new folder')
    args.output.mkdir(parents=True, exist_ok=True)

    found, doc_files = write_docs(args.corpus, args.output, args.version, args.part_chars)

    outcomes = followup_outcomes(args.followup)
    official = pq.read_table(args.links, columns=['id', 'url'])
    official_urls = dict(zip(official['id'].to_pylist(), official['url'].to_pylist()))
    planned = {}
    for lang in LANGS:
        with (args.root / lang / 'plan.jsonl').open(encoding='utf-8') as file:
            for line in file:
                row = json.loads(line)
                planned[row['doc_id']] = (lang, row['host'])
    if missing := set(official_urls) - set(planned):
        raise ValueError(f'{len(missing)} official IDs are in no plan')
    if extra := set(found) - set(official_urls):
        raise ValueError(f'{len(extra)} corpus IDs are not official IDs')

    columns = {name: [] for name in REGISTRY_SCHEMA.names}
    states = Counter()
    for doc_id in sorted(official_urls):
        lang, host = planned[doc_id]
        if doc_id in found:
            source, status, parser_version, n_chars, digest = found[doc_id]
            state, reason, added = 'in_corpus', '', args.version
        else:
            source = status = parser_version = digest = None
            n_chars, added = None, None
            category, reason = outcomes.get(doc_id, ('', 'no_followup_record'))
            if host.endswith(BLOCKED_DOMAINS):
                state, reason = 'blocked', 'access_blocked'
            else:
                state = CATEGORY_STATE.get(category, 'failed')
                reason = f'{category}:{reason}'.strip(':') if category else reason
        states[(lang, state)] += 1
        for name, value in zip(REGISTRY_SCHEMA.names, (
                doc_id, official_urls[doc_id], lang, host, state, reason, source, status,
                parser_version, n_chars, digest, added)):
            columns[name].append(value)
    registry = args.output / 'registry.parquet'
    pq.write_table(pa.table(columns, schema=REGISTRY_SCHEMA), registry, compression='zstd')

    files = {str(p.relative_to(args.output)): dict(bytes=p.stat().st_size, sha256=sha256_file(p))
             for p in [*doc_files, registry]}
    by_lang = {lang: {state: count for (l, state), count in sorted(states.items()) if l == lang} for lang in LANGS}
    parsers = Counter((planned[doc_id][0], v[2]) for doc_id, v in found.items())
    sources = Counter((planned[doc_id][0], v[0]) for doc_id, v in found.items())
    manifest = dict(
        version=args.version, built_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        official_ids=len(official_urls), documents=len(found), registry_states=by_lang,
        sources={lang: {s: n for (l, s), n in sorted(sources.items()) if l == lang} for lang in LANGS},
        parser_versions={lang: {p: n for (l, p), n in sorted(parsers.items()) if l == lang} for lang in LANGS},
        notes=args.note, files=files)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (args.output / 'dataset-metadata.json').write_text(json.dumps(dict(
        title='MedXIR Corpus', id=args.kaggle_id, licenses=[dict(name='other')],
        subtitle='Crawled VI/EN/ZH medical articles keyed by official corpus ID',
    ), indent=2) + '\n')
    print(json.dumps(dict(documents=len(found), registry_states=by_lang), indent=2))


if __name__ == '__main__':
    main()
