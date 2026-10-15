"""Build complete language plans for a fresh crawl, preserving every corpus ID.

No pilot or checkpoint exclusions. --prepare-cloud binds existing language folders,
prepares both shards and publishes plans/configs for the other machine.
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from storage import command, destination_root, prepare_destination, sha256, validate_destination


def build(corpus, output, language_map, policy, shard_count):
    digest = sha256(corpus)
    if digest != policy['corpus_sha256']:
        raise ValueError('Corpus hash differs from the reviewed policy; review before preparing a new run')
    output.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect()
    count, unique_ids, unique_urls = db.execute(
        'SELECT count(*),count(DISTINCT id),count(DISTINCT url) FROM read_parquet(?)',
        [str(corpus)]).fetchone()
    if count != unique_ids or count != unique_urls:
        raise ValueError('Corpus contains duplicate IDs or URLs')
    hosts = {urlsplit(u).hostname for (u,) in db.execute(
        'SELECT DISTINCT regexp_extract(url, \'^https?://[^/]+\', 0) FROM read_parquet(?)',
        [str(corpus)]).fetchall()}
    if hosts - language_map.keys() or set(language_map.values()) - {'vi', 'en', 'zh'}:
        raise ValueError(f'Unreviewed language mapping for hosts: {hosts - language_map.keys()}')
    handles, counts, active, shards = {}, Counter(), Counter(), {}
    try:
        for lang in ('vi', 'en', 'zh'):
            folder = output / lang
            folder.mkdir(exist_ok=True)
            handles[lang] = (folder / 'plan.jsonl.tmp').open('w', encoding='utf-8')
            shards[lang] = Counter()
        cursor = db.execute('SELECT id,url FROM read_parquet(?) ORDER BY id', [str(corpus)])
        while rows := cursor.fetchmany(2000):
            for doc_id, url in rows:
                host = urlsplit(url).hostname
                lang = language_map[host]
                item = dict(doc_id=doc_id, url=url, host=host, language=lang)
                reason = policy['deferred_hosts'].get(host)
                if reason:
                    item['deferred_reason'] = reason
                else:
                    active[lang] += 1
                handles[lang].write(json.dumps(item, ensure_ascii=False) + '\n')
                counts[lang] += 1
                shards[lang][doc_id % shard_count] += 1
    finally:
        for file in handles.values():
            file.close()
        db.close()
    plans = {}
    for lang in ('vi', 'en', 'zh'):
        plan = output / lang / 'plan.jsonl'
        tmp = plan.with_suffix('.jsonl.tmp')
        if plan.exists() and sha256(plan) != sha256(tmp):
            raise ValueError(f'Refusing to replace existing plan: {plan}; use a new output directory')
        tmp.replace(plan)
        plans[lang] = dict(path=str(plan), sha256=sha256(plan), total=counts[lang],
                           initially_active=active[lang], deferred=counts[lang] - active[lang],
                           shards=dict(shards[lang]))
    if sum(counts.values()) != count:
        raise ValueError('Language plan union does not equal corpus')
    result = dict(corpus=str(corpus), corpus_sha256=digest, total=count, shard_count=shard_count,
                  plans=plans, deferred_hosts=policy['deferred_hosts'])
    (output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def cloud(output, root_config, manifest):
    config = json.loads(root_config.read_text())
    validate_destination(config)
    entries = json.loads(command('lsjson', destination_root(config), '--dirs-only', '--no-modtime'))
    for lang in ('vi', 'en', 'zh'):
        matches = [e for e in entries if e['Name'] == lang]
        if len(matches) != 1:
            raise ValueError(f'Expected one existing {lang}/ directory, found {len(matches)}')
        child = dict(config, remote=config['remote'].rstrip('/') + '/' + lang,
                     folder_id=matches[0]['ID'])
        path = output / f'storage.{lang}.json'
        if path.exists() and json.loads(path.read_text()) != child:
            raise ValueError(f'Refusing to change storage binding: {path}')
        path.write_text(json.dumps(child, indent=2) + '\n')
        prepare_destination(child, output / lang / 'plan.jsonl', manifest['shard_count'])
    # Prepare directories once on the coordinator to avoid Drive duplicate-folder races.
    base = destination_root(config).rstrip('/') + '/coordination/' + output.name
    command('mkdir', base)
    for lang in ('vi', 'en', 'zh'):
        command('mkdir', base + '/' + lang)
        command('copyto', output / lang / 'plan.jsonl', base + '/' + lang + '/plan.jsonl', '--immutable')
        command('copyto', output / f'storage.{lang}.json', base + f'/storage.{lang}.json', '--immutable')
    command('copyto', output / 'manifest.json', base + '/manifest.json', '--immutable')
    print(f'Coordination artifacts: {base}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=Path('dataset/links_corpus_vi_en_zh.parquet'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/recrawl_20261015'))
    parser.add_argument('--languages', type=Path, default=Path('configs/host_languages.json'))
    parser.add_argument('--policy', type=Path, default=Path('configs/recrawl_policy.json'))
    parser.add_argument('--shard-count', type=int, default=2)
    parser.add_argument('--storage-config', type=Path, default=Path('storage.json'))
    parser.add_argument('--prepare-cloud', action='store_true')
    args = parser.parse_args()
    if args.shard_count < 1:
        parser.error('--shard-count must be positive')
    result = build(args.corpus, args.output, json.loads(args.languages.read_text()),
                   json.loads(args.policy.read_text()), args.shard_count)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.prepare_cloud:
        cloud(args.output, args.storage_config, result)


if __name__ == '__main__':
    main()
