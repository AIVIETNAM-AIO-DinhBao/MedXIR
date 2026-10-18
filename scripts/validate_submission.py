"""Validate a submission against docs/SUBMISSION.md, optionally zip it for upload.

Errors (exit 1): not a JSON list, missing/duplicate/unknown query ids, wrong field
types, doc ids outside the official corpus, empty chunk_text, bad chunk_order,
zip with more than one file or a file inside a folder.
Warnings: doc ids given as strings, chunk doc_id not listed in relevant_docs,
chunk_text not found verbatim in the exported text (only with --texts).

    uv run python scripts/validate_submission.py run.json --texts artifacts/corpus_v1/*.parquet --zip
"""
import argparse
import html
import json
import re
import sys
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

import duckdb


def normalize(text):
    # Same normalization the scorer applies before matching (EVALUATION.md 3.2).
    text = html.unescape(unicodedata.normalize('NFKC', text)).lower()
    return re.sub(r'\s+', ' ', text).strip()


def load(path):
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if not n.endswith('/')]
            if len(names) != 1 or '/' in names[0] or not names[0].endswith('.json'):
                raise ValueError(f'ZIP must hold exactly one .json at its root, found {names}')
            return json.loads(archive.read(names[0]))
    return json.loads(path.read_text(encoding='utf-8'))


def as_doc_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def validate(submission, query_ids, corpus_ids, texts):
    errors, warnings, counts = [], Counter(), Counter()
    if not isinstance(submission, list):
        return ['top level must be a JSON list'], warnings, counts
    seen = Counter()
    for index, entry in enumerate(submission):
        where = f'entry {index}'
        if not isinstance(entry, dict):
            errors.append(f'{where}: not an object')
            continue
        query_id = entry.get('id')
        if not isinstance(query_id, int) or isinstance(query_id, bool):
            errors.append(f'{where}: id must be an integer, got {query_id!r}')
            continue
        where = f'query {query_id}'
        seen[query_id] += 1
        docs, chunks = entry.get('relevant_docs'), entry.get('relevant_chunks')
        if not isinstance(docs, list) or not isinstance(chunks, list):
            errors.append(f'{where}: relevant_docs and relevant_chunks must both be lists (may be empty)')
            continue
        listed = set()
        for value in docs:
            doc_id = as_doc_id(value)
            if doc_id is None or doc_id not in corpus_ids:
                errors.append(f'{where}: relevant_docs has unknown id {value!r}')
                continue
            warnings['doc id given as string'] += isinstance(value, str)
            listed.add(doc_id)
        counts['docs'] += len(docs)
        counts['empty docs'] += not docs
        counts['empty chunks'] += not chunks
        for position, chunk in enumerate(chunks):
            spot = f'{where} chunk {position}'
            if not isinstance(chunk, dict):
                errors.append(f'{spot}: not an object')
                continue
            doc_id = as_doc_id(chunk.get('doc_id'))
            text = chunk.get('chunk_text')
            if doc_id is None or doc_id not in corpus_ids:
                errors.append(f'{spot}: unknown doc_id {chunk.get("doc_id")!r}')
                continue
            warnings['doc id given as string'] += isinstance(chunk.get('doc_id'), str)
            if not isinstance(text, str) or not text.strip():
                errors.append(f'{spot}: chunk_text must be a non-empty string')
                continue
            if 'chunk_order' in chunk:
                order = chunk['chunk_order']
                if not isinstance(order, int) or isinstance(order, bool) or order < 0:
                    errors.append(f'{spot}: chunk_order must be a non-negative integer')
            warnings['chunk doc_id not in relevant_docs'] += doc_id not in listed
            if texts is not None:
                source = texts.get(doc_id)
                if source is None:
                    warnings['chunk doc missing from --texts'] += 1
                elif normalize(text) not in source:
                    warnings['chunk_text not verbatim in source'] += 1
            counts['chunks'] += 1
    errors += [f'query {q}: appears {n} times' for q, n in seen.items() if n > 1]
    missing, extra = query_ids - seen.keys(), seen.keys() - query_ids
    if missing:
        errors.append(f'{len(missing)} queries missing, e.g. {sorted(missing)[:10]}')
    if extra:
        errors.append(f'{len(extra)} ids not in query.parquet, e.g. {sorted(extra)[:10]}')
    return errors, warnings, counts


def load_texts(paths, wanted):
    db = duckdb.connect()
    db.execute('CREATE TEMP TABLE wanted(doc_id BIGINT)')
    db.executemany('INSERT INTO wanted VALUES (?)', [[i] for i in wanted])
    files = ', '.join(f"'{p}'" for p in paths)
    rows = db.execute(f'SELECT doc_id, text FROM read_parquet([{files}]) JOIN wanted USING (doc_id)').fetchall()
    return {doc_id: normalize(text) for doc_id, text in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('submission', type=Path, help='.json or .zip')
    parser.add_argument('--queries', type=Path, default=Path('dataset/query.parquet'))
    parser.add_argument('--corpus', type=Path, default=Path('dataset/links_corpus_vi_en_zh.parquet'))
    parser.add_argument('--texts', type=Path, nargs='*', help='exported corpus parquets for the verbatim check')
    parser.add_argument('--zip', action='store_true', help='write <name>.zip next to the .json if valid')
    args = parser.parse_args()

    submission = load(args.submission)
    query_ids = {row[0] for row in duckdb.sql(f"SELECT id FROM '{args.queries}'").fetchall()}
    corpus_ids = {row[0] for row in duckdb.sql(f"SELECT id FROM '{args.corpus}'").fetchall()}
    texts = None
    if args.texts:
        wanted = {as_doc_id(c.get('doc_id')) for e in submission if isinstance(e, dict)
                  for c in e.get('relevant_chunks') or [] if isinstance(c, dict)} - {None}
        texts = load_texts(args.texts, wanted)
    errors, warnings, counts = validate(submission, query_ids, corpus_ids, texts)

    entries = len(submission) if isinstance(submission, list) else 0
    print(f'{args.submission}: {entries} entries, {counts["docs"]:,} docs, {counts["chunks"]:,} chunks; '
          f'{counts["empty docs"]} queries without docs, {counts["empty chunks"]} without chunks')
    for name, value in warnings.items():
        if value:
            print(f'WARNING {name}: {value:,}')
    for error in errors[:30]:
        print(f'ERROR {error}')
    if errors:
        print(f'INVALID: {len(errors)} errors')
        sys.exit(1)
    print('VALID')
    if args.zip:
        if args.submission.suffix != '.json':
            parser.error('--zip needs a .json input')
        target = args.submission.with_suffix('.zip')
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(args.submission, arcname=args.submission.name)
        print(f'Wrote {target}')


if __name__ == '__main__':
    main()
