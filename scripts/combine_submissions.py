"""One submission from the document lists of one ZIP and the chunk lists of another.

The scorer grades relevant_docs and relevant_chunks separately (docs/EVALUATION.md), so each half can come from
the run that scores best on it: 30/10 the hybrid run (direction A) had the best documents (Doc F2 0.3045,
ghigh) and the fused ensemble (direction B, var-all-b1-d200-c70-x1) the best chunks (Chunk F2 0.2060).

Chunks keep their own doc_id. Their documents are not added to the document list by default: a chunk is matched
against the reference passages of its doc_id whether or not that document is listed, and every added document
lowers Doc precision (validate_submission.py prints a warning only). --append-chunk-docs adds them anyway.
--min-chars drops chunk texts shorter than that (tiny ZH entries cannot cover 40% of a reference passage).

    uv run python scripts/combine_submissions.py --docs A.zip --chunks B.zip --out C.zip --min-chars 200
"""
import argparse
import json
import zipfile
from pathlib import Path


def read(path):
    path = Path(path)
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith('.json')]
            assert len(names) == 1, f'{path}: expected one JSON, found {names}'
            return json.loads(archive.read(names[0]))
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--docs', required=True, help='submission whose relevant_docs are kept')
    parser.add_argument('--chunks', required=True, help='submission whose relevant_chunks are kept')
    parser.add_argument('--out', required=True, help='output .zip')
    parser.add_argument('--min-chars', type=int, default=0)
    parser.add_argument('--append-chunk-docs', action='store_true')
    args = parser.parse_args()

    docs = {e['id']: e for e in read(args.docs)}
    chunks = {e['id']: e for e in read(args.chunks)}
    assert docs.keys() == chunks.keys(), f'query ids differ: {len(docs.keys() ^ chunks.keys())} not in both'

    out, n_chunks, n_short, n_docs, unlisted = [], 0, 0, 0, 0
    for qid, entry in docs.items():
        kept = [c for c in chunks[qid]['relevant_chunks'] if len(c['chunk_text']) >= args.min_chars]
        n_short += len(chunks[qid]['relevant_chunks']) - len(kept)
        listed = list(entry['relevant_docs'])
        missing = list(dict.fromkeys(c['doc_id'] for c in kept if c['doc_id'] not in set(listed)))
        unlisted += len(missing)
        if args.append_chunk_docs:
            listed += missing
        out.append(dict(id=qid, relevant_docs=listed, relevant_chunks=kept))
        n_chunks += len(kept)
        n_docs += len(listed)

    out_path = Path(args.out)
    with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(out_path.with_suffix('.json').name, json.dumps(out, ensure_ascii=False, separators=(',', ':')))
    n = len(out)
    print(f'{out_path}: {out_path.stat().st_size / 1e6:.1f} MB | docs/q {n_docs / n:.1f} | chunks/q {n_chunks / n:.1f} '
          f'| short dropped/q {n_short / n:.1f} | chunk docs outside the doc list/q {unlisted / n:.1f}'
          + (' (appended)' if args.append_chunk_docs else ''))


if __name__ == '__main__':
    main()
