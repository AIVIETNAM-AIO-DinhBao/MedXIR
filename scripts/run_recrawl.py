"""Run one assigned shard of the fresh language plans on this machine.

The coordinator prepares cloud folders once with prepare_recrawl.py. Keep the
shard ID fixed across restarts. stdout/stderr for each child go to its own log.
"""
import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

from storage import command, destination_root, sha256


def download(output, root_config):
    config = json.loads(root_config.read_text())
    base = destination_root(config).rstrip('/') + '/coordination/' + output.name
    output.mkdir(parents=True, exist_ok=True)
    command('copyto', base + '/manifest.json', output / 'manifest.json')
    manifest = json.loads((output / 'manifest.json').read_text())
    for lang, info in manifest['plans'].items():
        folder = output / lang
        folder.mkdir(exist_ok=True)
        temporary = folder / 'plan.jsonl.download'
        command('copyto', base + f'/{lang}/plan.jsonl', temporary)
        if sha256(temporary) != info['sha256']:
            raise ValueError(f'Downloaded plan checksum mismatch: {lang}')
        plan = folder / 'plan.jsonl'
        if plan.exists() and sha256(plan) != info['sha256']:
            raise ValueError(f'Existing {lang} plan differs; choose a new output')
        temporary.replace(plan)
        target = output / f'storage.{lang}.json'
        payload = command('cat', base + f'/storage.{lang}.json')
        if target.exists() and json.loads(target.read_bytes()) != json.loads(payload):
            raise ValueError(f'Existing storage config differs: {lang}')
        target.write_bytes(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('artifacts/recrawl_20261015'))
    parser.add_argument('--shard-id', type=int, required=True)
    parser.add_argument('--workers', type=int, default=16, help='Total HTTP worker budget across active languages')
    parser.add_argument('--max-new', type=int, default=0, help='Per language fetch cap for an initial verification run')
    parser.add_argument('--batch-docs', type=int, default=2000)
    parser.add_argument('--followup-every', type=float, default=300)
    parser.add_argument('--initialize-only', action='store_true', help='Publish initial queues without fetching')
    parser.add_argument('--download', action='store_true', help='Download and verify shared plans/configs first')
    parser.add_argument('--download-only', action='store_true',
                        help='Download plans/configs then exit; useful before checkpoint handoff fetch')
    parser.add_argument('--storage-config', type=Path, default=Path('storage.json'))
    parser.add_argument('--keep-local', action='store_true', help='Retain verified raw/batches for initial checks')
    args = parser.parse_args()
    if args.download:
        download(args.output, args.storage_config)
    if args.download_only:
        if not args.download:
            parser.error('--download-only requires --download')
        return
    manifest = json.loads((args.output / 'manifest.json').read_text())
    shard_count = manifest['shard_count']
    if not 0 <= args.shard_id < shard_count or args.workers < 1 or args.max_new < 0:
        parser.error('Invalid shard, worker budget or fetch cap')
    active = sum(bool(info['initially_active']) for info in manifest['plans'].values())
    if args.workers < active:
        parser.error('Worker budget must cover all active languages')
    workers = max(1, args.workers // max(1, active))
    logs = args.output / 'logs'
    logs.mkdir(exist_ok=True)
    children = {}
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        for process in children.values():
            if process.poll() is None:
                process.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    handles = []
    try:
        for lang, info in manifest['plans'].items():
            plan = args.output / lang / 'plan.jsonl'
            if sha256(plan) != info['sha256']:
                raise ValueError(f'Plan changed: {lang}')
            child_args = [sys.executable, '-u', 'scripts/crawl.py', '--plan', str(plan),
                          '--output', str(args.output / lang), '--shard-count', str(shard_count),
                          '--shard-id', str(args.shard_id), '--workers', str(workers),
                          '--host-workers', '1', '--delay', '2', '--retries', '1',
                          '--storage-config', str(args.output / f'storage.{lang}.json'),
                          '--batch-docs', str(args.batch_docs), '--batch-mb', '128',
                          '--cache-limit-gb', '4', '--followup-every', str(args.followup_every),
                          '--progress-every', '100']
            if args.initialize_only or not info['initially_active']:
                child_args += ['--followup-only']
            if args.max_new:
                child_args += ['--max-new', str(args.max_new)]
            if not args.keep_local:
                child_args += ['--prune-local']
            root = args.output / lang / f'shard-{args.shard_id:03d}-of-{shard_count:03d}'
            if (root / 'state.sqlite').exists():
                child_args += ['--resume']
            log = (logs / f'{lang}-shard-{args.shard_id}.log').open('a')
            handles.append(log)
            process = subprocess.Popen(child_args, stdout=log, stderr=subprocess.STDOUT)
            children[lang] = process
            print(f'{lang}: pid={process.pid}, log={log.name}', flush=True)
        (args.output / f'processes-shard-{args.shard_id}.json').write_text(json.dumps(
            dict(supervisor_pid=__import__('os').getpid(),
                 children={lang: p.pid for lang, p in children.items()}), indent=2) + '\n')
        while any(p.poll() is None for p in children.values()):
            time.sleep(1)
        result = {lang: p.returncode for lang, p in children.items()}
        print(json.dumps(dict(exit_codes=result, stopped=stopping)), flush=True)
        # Exit 2 means pending/deferred IDs remain; this is expected, not completion.
        if any(code not in (0, 2) for code in result.values()):
            raise SystemExit(1)
        if any(code == 2 for code in result.values()):
            raise SystemExit(2)
    finally:
        for process in children.values():
            if process.poll() is None:
                process.terminate()
        for process in children.values():
            process.wait()
        for file in handles:
            file.close()


if __name__ == '__main__':
    main()
