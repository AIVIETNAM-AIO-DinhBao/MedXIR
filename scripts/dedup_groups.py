"""Exact-duplicate groups of the Kaggle corpus, without the Pharmacity bad redirects.

pharmacity_exclusions(): IDs whose fetched page is another article (the redirect ended on a page
without the original slug), so their text is wrong for their doc_id.
hash_groups(): one row per text_sha256 among in_corpus documents that are not excluded.

group_type: single; url_variant (same host and path, ignoring scheme, www, trailing slash and
fragment); cross_host (more than one host); same_host_diff_path (redirects to a merged article,
republished abstracts, placeholder pages). Only single and url_variant are fan-out safe: the
chunks of the representative can be reported for every doc_id of the group.

    uv run python scripts/dedup_groups.py --registry artifacts/kaggle/medxir-corpus/registry.parquet
"""
import argparse
import gzip
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd

REASON = 'redirected_to_other_article'


def pharmacity_exclusions(jsonl_gz_path):
    """doc_id and reason of each record whose final_url path lacks the original slug."""
    rows = []
    with gzip.open(jsonl_gz_path, 'rt', encoding='utf-8') as file:
        for line in file:
            record = json.loads(line)
            slug = unquote(urlsplit(record['url']).path.strip('/'))
            landed = unquote(urlsplit(record['final_url']).path)
            if slug not in landed:
                rows.append((record['doc_id'], REASON))
    return pd.DataFrame(rows, columns=['doc_id', 'reason']).sort_values('doc_id').reset_index(drop=True)


def url_key(url):
    parts = urlsplit(url)
    return parts.netloc.lower().removeprefix('www.') + parts.path.rstrip('/').lower()


def hash_groups(registry, excluded_ids=()):
    """One row per text_sha256. rep_doc_id is the smallest doc_id: duplicate texts are identical,
    and no crawl-vs-archive reason favours one source, so the choice only sets url/title metadata."""
    rows = registry[registry.state.eq('in_corpus') & ~registry.doc_id.isin(list(excluded_ids))]
    rows = rows[['doc_id', 'url', 'host', 'text_sha256']].sort_values('doc_id')
    rows = rows.assign(key=rows.url.map(url_key))
    grouped = rows.groupby('text_sha256', sort=True)
    summary = pd.DataFrame({
        'rep_doc_id': grouped.doc_id.min(),
        'doc_ids': grouped.doc_id.agg(lambda ids: [int(i) for i in ids]),
        'n_ids': grouped.size(),
        'n_keys': grouped.key.nunique(),
        'n_hosts': grouped.host.nunique(),
    }).reset_index()
    group_type = pd.Series('same_host_diff_path', index=summary.index)
    group_type = group_type.mask(summary.n_hosts > 1, 'cross_host')
    group_type = group_type.mask(summary.n_keys == 1, 'url_variant')
    group_type = group_type.mask(summary.n_ids == 1, 'single')
    summary['group_type'] = group_type
    summary['fanout_safe'] = summary.group_type.isin(['single', 'url_variant'])
    return summary[['text_sha256', 'rep_doc_id', 'doc_ids', 'n_ids', 'group_type', 'fanout_safe']]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--registry', type=Path, default=Path('artifacts/kaggle/medxir-corpus/registry.parquet'))
    parser.add_argument('--pharmacity', type=Path, default=Path('artifacts/recrawl_20261015/pharmacity_v1/documents.jsonl.gz'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/aux_v1'))
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    exclusions = pharmacity_exclusions(args.pharmacity)
    exclusions.to_csv(args.output / 'exclusions.csv', index=False)
    registry = pd.read_parquet(args.registry)
    groups = hash_groups(registry, exclusions.doc_id)
    groups.to_parquet(args.output / 'hash_groups.parquet', index=False)

    lang_of = registry.set_index('doc_id').lang
    lang = groups.rep_doc_id.map(lang_of)
    print(f'excluded ids: {len(exclusions):,} {exclusions.reason.value_counts().to_dict()}')
    print(f'hash groups: {len(groups):,}; documents covered: {int(groups.n_ids.sum()):,}')
    print('\ngroups by type and lang of the representative:')
    print(pd.crosstab(groups.group_type, lang, margins=True))
    rows = groups.assign(lang=lang).groupby(['group_type', 'lang']).n_ids.sum().unstack(fill_value=0)
    print('\ndocument ids by type and lang (duplicates only, singles included):')
    print(rows.assign(total=rows.sum(axis=1)))


if __name__ == '__main__':
    main()
