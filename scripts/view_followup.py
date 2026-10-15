"""Show counts and a filtered preview/export of the current document repair queues."""
import argparse
import csv
import gzip
from collections import Counter
from contextlib import ExitStack
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=Path('artifacts/recrawl_20261015'))
    parser.add_argument('--language', choices=['vi', 'en', 'zh', 'all'], default='all')
    parser.add_argument('--shard-id', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=2)
    parser.add_argument('--category', nargs='+', default=[
        'crawl_failed', 'parse_failed', 'incomplete', 'needs_review'], choices=[
        'pending', 'deferred', 'paused', 'crawl_failed', 'parse_failed', 'incomplete', 'needs_review'])
    parser.add_argument('--limit', type=int, default=20, help='Preview rows per language; export is not limited')
    parser.add_argument('--export', type=Path, help='Export ALL matching rows into one uncompressed CSV')
    args = parser.parse_args()
    if args.limit < 0 or not 0 <= args.shard_id < args.shard_count:
        parser.error('Invalid preview limit or shard identity')
    languages = ('vi', 'en', 'zh') if args.language == 'all' else (args.language,)
    paths = {lang: args.run / lang / f'shard-{args.shard_id:03d}-of-{args.shard_count:03d}' /
             'followup/documents.csv.gz' for lang in languages}
    for path in paths.values():
        if not path.is_file():
            parser.error(f'Missing report: {path}; generate it with scripts/followup.py first')
        if args.export and args.export.resolve() == path.resolve():
            parser.error('Export must not overwrite the source queue')
    selected = set(args.category)
    total = 0
    with ExitStack() as stack:
        writer = None
        target = stack.enter_context(args.export.open('w', encoding='utf-8', newline='')) if args.export else None
        for lang, path in paths.items():
            counts, matching, preview = Counter(), 0, []
            with gzip.open(path, 'rt', encoding='utf-8', newline='') as file:
                reader = csv.DictReader(file)
                if target and writer is None:
                    writer = csv.DictWriter(target, fieldnames=['language', *reader.fieldnames])
                    writer.writeheader()
                for row in reader:
                    counts[row['category']] += 1
                    if row['category'] not in selected:
                        continue
                    matching += 1
                    if len(preview) < args.limit:
                        preview.append(row)
                    if writer:
                        writer.writerow(dict(language=lang, **row))
            print(f'\n{lang}, shard {args.shard_id}: {path}')
            print('Outstanding counts: ' + ', '.join(f'{k}={v:,}' for k, v in sorted(counts.items())))
            print(f'Matching: {matching:,}; preview: {len(preview)}')
            for row in preview:
                print(f'{row["doc_id"]}\t{row["host"]}\t{row["category"]}\t{row["reason"]}\t{row["url"]}')
            total += matching
        if args.export:
            print(f'\nExported {total:,} rows to {args.export} (all matches, independent of --limit)')


if __name__ == '__main__':
    main()
