"""Generate src/notebooks/kaggle_bm25_vi.ipynb, the Kaggle CPU job that runs BM25 over the Vietnamese chunks.

The notebook copies scripts/bm25_vi.py into a code cell (without its `if __name__ == '__main__'` block) and
runs its main() with the Kaggle paths. Output: vi-candidates-k500.npz (top 500 chunk ids and scores per query).

    uv run python scripts/build_bm25_notebook.py
"""
import argparse
import json
from pathlib import Path

from build_chunk_notebook import copied_source, notebook


def build_cells():
    return [
        ('md', """
# MedXIR: BM25 over the Vietnamese chunks

CPU job. Attach one chunk corpus, the dataset `ntklinhfitus/medxir-chunk-corpus-v2` (set EXPECTED_CORPUS_VERSION
for another), and the dataset `boinhbo/medxir-retrieval-inputs` (`query.parquet`). Output in `/kaggle/working`:
`vi-candidates-k500.npz` + `bm25_manifest.json` (corpus version and VI chunk count it covers).
The first code cell is a copy of `scripts/bm25_vi.py`.
"""),
        ('code', '# Copied from scripts/bm25_vi.py by scripts/build_bm25_notebook.py. Edit the script and rebuild.\n'
                 "__file__ = '/kaggle/working/bm25_vi.py'  # the module derives ROOT from __file__; paths are set below\n"
                 + copied_source(Path('scripts') / 'bm25_vi.py')),
        ('code', """
import glob
import sys

import pyarrow.parquet as pq

EXPECTED_CORPUS_VERSION = 'v2'  # the hybrid notebook's BM25_CORPUS_VERSION must match the corpus used here

manifests = glob.glob('/kaggle/input/**/chunk_manifest.json', recursive=True)
query_files = glob.glob('/kaggle/input/**/query.parquet', recursive=True)
assert len(manifests) == 1 and len(query_files) == 1, ('attach one chunk corpus and one query.parquet', manifests, query_files)
CHUNK_MANIFEST = json.loads(Path(manifests[0]).read_text(encoding='utf-8'))
assert CHUNK_MANIFEST['dataset_version'] == EXPECTED_CORPUS_VERSION, CHUNK_MANIFEST.get('dataset_version')
CHUNK_DIR = Path(manifests[0]).parent / 'chunks' / 'vi'
VI_ROWS = sum(pq.ParquetFile(p).metadata.num_rows for p in CHUNK_DIR.glob('part-*.parquet'))
assert VI_ROWS == CHUNK_MANIFEST['languages']['vi']['chunks'], (VI_ROWS, CHUNK_MANIFEST['languages']['vi'])
QUERY_PATH = Path(query_files[0])
OUT_DIR = Path('/kaggle/working')
print(CHUNK_DIR, sorted(p.name for p in CHUNK_DIR.glob('part-*.parquet')), f'{VI_ROWS:,} VI chunks', QUERY_PATH, flush=True)
"""),
        ('code', """
sys.argv = ['bm25_vi.py', '--top-k', '500', '--workers', '4', '--no-cache']
main()
(OUT_DIR / 'bm25_manifest.json').write_text(json.dumps(dict(
    corpus_version=CHUNK_MANIFEST['dataset_version'], vi_chunks=VI_ROWS, top_k=500,
    candidates='vi-candidates-k500.npz'), indent=2) + '\\n')
"""),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=Path('src/notebooks/kaggle_bm25_vi.ipynb'))
    args = parser.parse_args()
    cells = build_cells()
    args.output.write_text(json.dumps(notebook(cells), ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'{args.output}: {len(cells)} cells')


if __name__ == '__main__':
    main()
