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

CPU job. Attach the kernel `boinhbo/medxir-chunk-corpus` (chunks) and the dataset
`boinhbo/medxir-retrieval-inputs` (`query.parquet`). Output in `/kaggle/working`: `vi-candidates-k500.npz`.
The first code cell is a copy of `scripts/bm25_vi.py`.
"""),
        ('code', '# Copied from scripts/bm25_vi.py by scripts/build_bm25_notebook.py. Edit the script and rebuild.\n'
                 "__file__ = '/kaggle/working/bm25_vi.py'  # the module derives ROOT from __file__; paths are set below\n"
                 + copied_source(Path('scripts') / 'bm25_vi.py')),
        ('code', """
import glob
import sys

chunk_files = sorted(glob.glob('/kaggle/input/**/chunks/vi/part-*.parquet', recursive=True))
query_files = glob.glob('/kaggle/input/**/query.parquet', recursive=True)
assert len(chunk_files) == 7 and len(query_files) == 1, (chunk_files, query_files)
CHUNK_DIR = Path(chunk_files[0]).parent
QUERY_PATH = Path(query_files[0])
OUT_DIR = Path('/kaggle/working')
print(CHUNK_DIR, QUERY_PATH, flush=True)
"""),
        ('code', """
sys.argv = ['bm25_vi.py', '--top-k', '500', '--workers', '4', '--no-cache']
main()
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
