"""Generate src/notebooks/kaggle_chunk_corpus.ipynb, the Kaggle job that chunks the medxir-corpus dataset.

The notebook copies scripts/chunking.py, scripts/text_clean.py and scripts/dedup_groups.py into
code cells, without their `if __name__ == '__main__'` blocks, so chunk_document() runs exactly as
written. The excluded doc_ids come from artifacts/aux_v1/exclusions.csv at build time. The output
is deterministic: running the builder again regenerates the same notebook.

    uv run python scripts/dedup_groups.py          # writes artifacts/aux_v1/exclusions.csv
    uv run python scripts/build_chunk_notebook.py
"""
import argparse
import csv
import json
from pathlib import Path

SCRIPTS = ['chunking.py', 'text_clean.py', 'dedup_groups.py']
MAIN_BLOCK = "\nif __name__ == '__main__':"


def copied_source(path):
    """Module source with the `if __name__ == '__main__'` block cut off (it would run inside Kaggle)."""
    source = path.read_text(encoding='utf-8')
    cut = source.find(MAIN_BLOCK)
    return (source if cut < 0 else source[:cut]).rstrip() + '\n'


def excluded_ids(path):
    with path.open(encoding='utf-8', newline='') as file:
        return sorted(int(row['doc_id']) for row in csv.DictReader(file))


def id_list(ids, per_line=10):
    lines = [', '.join(str(i) for i in ids[start:start + per_line]) for start in range(0, len(ids), per_line)]
    return '[\n    ' + ',\n    '.join(lines) + ',\n]'


def markdown(text):
    return text.strip('\n')


def build_cells(scripts, excluded):
    header = ('# Copied from {name} by scripts/build_chunk_notebook.py. Edit the script and rebuild.\n')
    cells = [
        ('md', """
# MedXIR corpus: chunk every unique text

Input: the `medxir-corpus` Kaggle dataset (no internet needed). Output in `/kaggle/working`:
`chunks/<lang>/part-NNN.parquet`, `hash_groups.parquet` and `chunk_manifest.json`.

The cells that define `chunk_document`, `clean_text` and `hash_groups` are copies of
`scripts/chunking.py`, `scripts/text_clean.py` and `scripts/dedup_groups.py`.
"""),
    ]
    for name in scripts:
        cells.append(('code', header.format(name=name) + copied_source(Path('scripts') / name)))
    cells += [
        ('code', f"""
EXCLUDED_DOC_IDS = {id_list(excluded)}  # scripts/dedup_groups.py pharmacity_exclusions()
"""),
        ('code', """
import csv
import glob
import inspect
import json
import os
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Local test runs set DATA_ROOT, SAMPLE_DOCS and OUT_DIR. On Kaggle they stay unset.
DATA_ROOT = os.environ.get('DATA_ROOT', '')
SAMPLE_DOCS = int(os.environ.get('SAMPLE_DOCS', '0'))  # documents per part; 0 = all
OUT = Path(os.environ.get('OUT_DIR', '/kaggle/working'))
WORKERS = 4
PARAMS = inspect.signature(chunk_document).parameters
CHUNK_PARAMS = '/'.join(str(PARAMS[name].default) for name in ('limit', 'overlap', 'minimum'))
COLUMNS = ['chunk_id', 'text_sha256', 'rep_doc_id', 'lang', 'order', 'start', 'end',
           'chunk_text', 'n_units', 'title', 'clean_version', 'chunk_params']
SCHEMA = pa.schema([
    ('chunk_id', pa.string()), ('text_sha256', pa.string()), ('rep_doc_id', pa.int64()),
    ('lang', pa.string()), ('order', pa.int32()), ('start', pa.int64()), ('end', pa.int64()),
    ('chunk_text', pa.string()), ('n_units', pa.int32()), ('title', pa.string()),
    ('clean_version', pa.string()), ('chunk_params', pa.string()),
])
print('chunk_params', CHUNK_PARAMS, '| clean_version', CLEAN_VERSION, '| workers', WORKERS)
"""),
        ('md', '## 1. Dataset root and manifest'),
        ('code', """
def find_root():
    if DATA_ROOT:
        return Path(DATA_ROOT)
    hits = sorted(glob.glob('/kaggle/input/**/registry.parquet', recursive=True))
    assert len(hits) == 1, hits
    return Path(hits[0]).parent


T0 = time.time()
ROOT = find_root()
MANIFEST = json.loads((ROOT / 'manifest.json').read_text())
OUT.mkdir(parents=True, exist_ok=True)
print('root', ROOT, '| version', MANIFEST['version'], '| built', MANIFEST['built_at'],
      '| documents', f"{MANIFEST['documents']:,}")
"""),
        ('md', '## 2. Hash groups (exclusions applied)'),
        ('code', """
registry = pd.read_parquet(ROOT / 'registry.parquet')
groups = hash_groups(registry, EXCLUDED_DOC_IDS)
groups.to_parquet(OUT / 'hash_groups.parquet', index=False)
del registry
print(f'excluded {len(EXCLUDED_DOC_IDS)}; hash groups {len(groups):,}; '
      f'documents covered {int(groups.n_ids.sum()):,}')
print(groups.group_type.value_counts().to_string())
"""),
        ('md', """
## 3. Clean and chunk, part by part

Each worker cleans one unique text, chunks it with `chunk_document`, and asserts that every chunk
is `cleaned[start:end]`. `not_in_raw` counts chunks that are not a substring of the exported text,
which happens only where cleaning changed the text (line-break tags, lead-in).
"""),
        ('code', """
def chunk_one(job):
    text_sha256, rep_doc_id, lang, title, raw = job
    cleaned = clean_text(raw, lang)
    chunks = chunk_document(cleaned, lang)
    for chunk in chunks:
        assert cleaned[chunk.start:chunk.end] == chunk.text, (rep_doc_id, chunk.order)
    return dict(
        text_sha256=text_sha256, rep_doc_id=rep_doc_id, title=title,
        changed=cleaned != raw, not_in_raw=sum(chunk.text not in raw for chunk in chunks),
        chunks=[(chunk.order, chunk.start, chunk.end, chunk.text, size(chunk.text, lang))
                for chunk in chunks])


REP_SHA = dict(zip(groups.rep_doc_id, groups.text_sha256))
IDS_OF_SHA = dict(zip(groups.text_sha256, groups.n_ids))
REP_IDS = groups.rep_doc_id.values
PER_LANG = {}
"""),
        ('code', """
def clean_title(value):
    return None if pd.isna(value) else str(value)


def write_part(lang, number, rows):
    columns = dict(zip(COLUMNS, map(list, zip(*rows)))) if rows else {name: [] for name in COLUMNS}
    table = pa.table(columns, schema=SCHEMA)
    assert len(set(columns['chunk_id'])) == table.num_rows, 'chunk_id is not unique'
    folder = OUT / 'chunks' / lang
    folder.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, folder / f'part-{number:03d}.parquet', compression='zstd')
    return table.num_rows


with Pool(WORKERS) as pool:
    for lang in ('en', 'vi', 'zh'):
        stats = dict(texts=0, doc_ids=0, changed=0, changed_ids=0, not_in_raw=0,
                     chunk_counts=[], units=[], chunks=0, read_seconds=0.0, chunk_seconds=0.0,
                     write_seconds=0.0)
        PER_LANG[lang] = stats
        for number, path in enumerate(sorted((ROOT / 'docs' / lang).glob('*.parquet'))):
            started = time.time()
            docs = pd.read_parquet(path, columns=['doc_id', 'title', 'text', 'text_sha256'])
            docs = docs[docs.doc_id.isin(REP_IDS)].sort_values('doc_id')
            assert (docs.text_sha256.values == docs.doc_id.map(REP_SHA).values).all()
            if SAMPLE_DOCS:
                docs = docs.sample(n=min(SAMPLE_DOCS, len(docs)), random_state=0).sort_values('doc_id')
            jobs = [(sha, int(doc_id), lang, clean_title(title), text) for doc_id, sha, title, text
                    in zip(docs.doc_id, docs.text_sha256, docs.title, docs.text)]
            del docs
            stats['read_seconds'] += time.time() - started
            started = time.time()
            rows = []
            for result in pool.imap(chunk_one, jobs, chunksize=64):
                ids = int(IDS_OF_SHA[result['text_sha256']])
                stats['texts'] += 1
                stats['doc_ids'] += ids
                stats['changed'] += result['changed']
                stats['changed_ids'] += ids if result['changed'] else 0
                stats['not_in_raw'] += result['not_in_raw']
                stats['chunk_counts'].append(len(result['chunks']))
                for order, start, end, chunk_text, units in result['chunks']:
                    stats['units'].append(units)
                    rows.append((f"{result['text_sha256'][:16]}-{order}", result['text_sha256'],
                                 result['rep_doc_id'], lang, order, start, end, chunk_text, units,
                                 result['title'], CLEAN_VERSION, CHUNK_PARAMS))
            stats['chunk_seconds'] += time.time() - started
            started = time.time()
            stats['chunks'] += write_part(lang, number, rows)
            stats['write_seconds'] += time.time() - started
            print(f'{lang} part-{number:03d}: {len(jobs):,} texts, {len(rows):,} chunks, '
                  f'chunk {stats["chunk_seconds"]:.1f}s total so far', flush=True)
            del jobs, rows
if not SAMPLE_DOCS:
    assert sum(stats['texts'] for stats in PER_LANG.values()) == len(groups)
"""),
        ('md', '## 4. Manifest'),
        ('code', """
def summarise(stats):
    counts, units = np.array(stats['chunk_counts']), np.array(stats['units'])
    return dict(
        texts=stats['texts'], doc_ids=stats['doc_ids'], chunks=stats['chunks'],
        texts_changed_by_cleaning=stats['changed'], doc_ids_changed_by_cleaning=stats['changed_ids'],
        chunks_not_in_raw=stats['not_in_raw'],
        chunks_per_text_mean=round(float(counts.mean()), 2),
        chunks_per_text_p50=int(np.percentile(counts, 50)), chunks_per_text_p90=int(np.percentile(counts, 90)),
        chunks_per_text_max=int(counts.max()),
        n_units_p50=int(np.percentile(units, 50)), n_units_p90=int(np.percentile(units, 90)),
        n_units_max=int(units.max()),
        read_seconds=round(stats['read_seconds'], 1), chunk_seconds=round(stats['chunk_seconds'], 1),
        write_seconds=round(stats['write_seconds'], 1),
        chunk_seconds_per_1000_texts=round(stats['chunk_seconds'] / stats['texts'] * 1000, 2),
        seconds_per_1000_texts=round((stats['read_seconds'] + stats['chunk_seconds'] + stats['write_seconds'])
                                     / stats['texts'] * 1000, 2))


SUMMARY = {lang: summarise(stats) for lang, stats in PER_LANG.items()}
MANIFEST_OUT = dict(
    dataset_version=MANIFEST['version'], dataset_built_at=MANIFEST['built_at'],
    clean_version=CLEAN_VERSION, chunk_params=CHUNK_PARAMS, id_scheme='{text_sha256[:16]}-{order}',
    excluded_ids=len(EXCLUDED_DOC_IDS), hash_groups=len(groups),
    group_types={name: int(count) for name, count in groups.group_type.value_counts().items()},
    sample_docs_per_part=SAMPLE_DOCS, languages=SUMMARY,
    elapsed_seconds=round(time.time() - T0, 1))
(OUT / 'chunk_manifest.json').write_text(json.dumps(MANIFEST_OUT, ensure_ascii=False, indent=2) + '\\n')
"""),
        ('md', '## 5. Summary'),
        ('code', """
pd.DataFrame(SUMMARY).T
"""),
    ]
    return cells


def notebook(cells):
    out = []
    for index, (kind, text) in enumerate(cells):
        cell = dict(cell_type=kind, id=f'cell-{index:02d}', metadata={}, source=markdown(text).splitlines(keepends=True))
        if kind == 'code':
            cell.update(execution_count=None, outputs=[], source=text.strip('\n').splitlines(keepends=True))
        out.append(cell)
    return dict(
        cells=out,
        metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3'),
                      language_info=dict(name='python')),
        nbformat=4, nbformat_minor=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--exclusions', type=Path, default=Path('artifacts/aux_v1/exclusions.csv'))
    parser.add_argument('--output', type=Path, default=Path('src/notebooks/kaggle_chunk_corpus.ipynb'))
    args = parser.parse_args()
    excluded = excluded_ids(args.exclusions)
    cells = build_cells(SCRIPTS, excluded)
    args.output.write_text(json.dumps(notebook(cells), ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'{args.output}: {len(cells)} cells, {len(excluded)} excluded ids')


if __name__ == '__main__':
    main()
