"""Offline checks for data preservation, sharding and interruption boundaries.

Run: uv run python -m unittest discover -s tests -v
"""
import asyncio
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import crawl
import checkpoint_handoff
import followup
import prepare_recrawl
import storage
from content_parser import blocks_from, parse_page


class PipelineChecks(unittest.TestCase):
    def test_ncbi_access_screens_are_not_articles_or_ordinary_http_errors(self):
        record = dict(url='https://pubmed.ncbi.nlm.nih.gov/34859388/',
                      final_url='https://pubmed.ncbi.nlm.nih.gov/34859388/',
                      http_status=203, content_type='text/html')
        challenge = b'<script>const challengeId="sample"; function browserAcceptsCookies() {} async function submitSolve(nonce) {}</script>'
        self.assertEqual(crawl.classify(record, challenge), 'access_challenge')
        record.update(http_status=200, final_url='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi')
        blocked = b'<title>NCBI - WWW Error Blocked Diagnostic</title><h1>Access Denied</h1>'
        self.assertEqual(crawl.classify(record, blocked), 'access_challenge')
        article = b'<h1>Evaluation of an immune challenge</h1><p>Abstract: a study.</p>'
        self.assertEqual(crawl.classify(record, article), 'html_response')
        record['http_status'] = 404
        self.assertEqual(crawl.classify(record, article), 'http_error')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.args = SimpleNamespace(
            plan=self.root / 'plan.jsonl', pilot=self.root / 'pilot.jsonl',
            corpus=self.root / 'corpus.parquet', exclude_completed=[], all_hosts=False, include_host=[],
            limit=0, seed=1, shard_count=1, shard_id=0, workers=4, host_workers=2,
            delay=0, timeout=1, retries=0, progress_every=100, max_bytes=10000,
            cache_limit_gb=1)

    def tearDown(self):
        self.temp.cleanup()

    def state(self, rows):
        self.args.plan.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        (self.root / 'raw').mkdir(exist_ok=True)
        return crawl.open_state(self.args, self.root)

    def test_plan_exclusion_and_noncontiguous_shards(self):
        db = duckdb.connect()
        db.execute('CREATE TABLE corpus(id BIGINT,url VARCHAR)')
        db.executemany('INSERT INTO corpus VALUES (?,?)', [
            (1, 'https://ok.example/a'), (4, 'https://blocked.example/b'),
            (7, 'https://ok.example/c'), (10, 'https://ok.example/d')])
        db.execute('COPY corpus TO ? (FORMAT PARQUET)', [str(self.args.corpus)])
        db.close()
        self.args.pilot.write_text('\n'.join(json.dumps(row) for row in [
            dict(doc_id=1, host='ok.example', state='html_response'),
            dict(doc_id=4, host='blocked.example', state='skipped_host'),
        ]) + '\n')
        previous = self.root / 'previous.sqlite'
        with sqlite3.connect(previous) as db:
            db.execute('CREATE TABLE jobs(doc_id INTEGER,page TEXT)')
            db.execute("INSERT INTO jobs VALUES (7,'{}')")
        self.args.exclude_completed = [previous]
        crawl.prepare_plan(self.args)
        self.assertEqual([json.loads(l)['doc_id'] for l in self.args.plan.read_text().splitlines()], [10])
        self.args.plan = self.root / 'all.jsonl'
        self.args.all_hosts = True
        crawl.prepare_plan(self.args)
        self.args.shard_count = 3
        partitions = []
        for shard in range(3):
            self.args.shard_id = shard
            root = self.root / f'shard{shard}'
            root.mkdir()
            db = crawl.open_state(self.args, root)
            partitions.append({r[0] for r in db.execute('SELECT doc_id FROM jobs')})
            db.close()
        self.assertEqual(set.union(*partitions), {4, 10})
        self.assertEqual(sum(map(len, partitions)), 2)
        for shard, ids in enumerate(partitions):
            self.assertTrue(all(doc_id % 3 == shard for doc_id in ids))

        self.args.plan = self.root / 'fresh.jsonl'
        self.args.fresh_plan = True
        self.args.exclude_completed = []
        crawl.prepare_plan(self.args)
        self.assertEqual({json.loads(l)['doc_id'] for l in self.args.plan.read_text().splitlines()},
                         {1, 4, 7, 10})

    def test_complete_language_plans_preserve_deferred_ids_and_shards(self):
        db = duckdb.connect()
        db.execute('CREATE TABLE corpus(id BIGINT,url VARCHAR)')
        db.executemany('INSERT INTO corpus VALUES (?,?)', [
            (1, 'https://vi.example/a'), (8, 'https://zh.example/b'),
            (11, 'https://pubmed.ncbi.nlm.nih.gov/123/')])
        db.execute('COPY corpus TO ? (FORMAT PARQUET)', [str(self.args.corpus)])
        db.close()
        result = prepare_recrawl.build(self.args.corpus, self.root / 'new',
            {'vi.example': 'vi', 'zh.example': 'zh', 'pubmed.ncbi.nlm.nih.gov': 'en'},
            dict(corpus_sha256=storage.sha256(self.args.corpus),
                 deferred_hosts={'pubmed.ncbi.nlm.nih.gov': 'parser_missing'}), 2)
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['plans']['en']['deferred'], 1)
        self.args.plan = self.root / 'new/en/plan.jsonl'
        db = crawl.open_state(self.args, self.root)
        pauses = json.loads(db.execute("SELECT value FROM metadata WHERE key='paused_hosts'").fetchone()[0])
        self.assertIn('deferred:', pauses['pubmed.ncbi.nlm.nih.gov']['reason'])
        self.assertEqual(db.execute('SELECT doc_id FROM jobs').fetchall(), [(11,)])
        db.close()

    def test_followup_covers_pending_errors_compact_and_review_then_clears_fixed_ids(self):
        rows = [dict(doc_id=i, host='example.org', url=f'https://example.org/{i}')
                for i in (1, 4, 7, 10, 13)]
        db = self.state(rows)
        for i, page, doc in [
            (4, {'state': 'access_challenge'}, {'status': 'access_challenge'}),
            (7, {'state': 'html_response'}, {'status': 'unknown_template'}),
            (10, {'state': 'html_response'}, {'status': 'needs_review', 'has_text': True,
                                            '_archived': 'batch', 'warnings': ['pagination_not_fetched']}),
            (13, {'state': 'html_response'}, {'status': 'extracted_unreviewed', 'has_text': True})]:
            db.execute('UPDATE jobs SET page=?,document=? WHERE doc_id=?',
                       [json.dumps(page), json.dumps(doc), i])
        db.commit()
        report = followup.write_report(self.root)
        self.assertEqual(report['outstanding'], 4)
        self.assertEqual(report['total'], 5)
        with gzip.open(self.root / 'followup/retry_fetch.jsonl.gz', 'rt') as f:
            self.assertEqual([json.loads(l)['doc_id'] for l in f], [4])
        with gzip.open(self.root / 'followup/reparse.jsonl.gz', 'rt') as f:
            self.assertEqual([json.loads(l)['doc_id'] for l in f], [7])
        db.execute('UPDATE jobs SET document=? WHERE doc_id=7',
                   [json.dumps(dict(status='extracted_unreviewed', text='Fixed.'))])
        db.commit()
        self.assertEqual(followup.write_report(self.root)['outstanding'], 3)
        db.close()

    def test_short_image_documents_stay_in_review_without_losing_text(self):
        body = '<p>Nội dung như sau: ' + 'dữ liệu ' * 25 + '</p><img src="a.jpg"><img src="b.jpg">'
        doc = parse_page(dict(doc_id=1, host='baoquangtri.vn', url='https://baoquangtri.vn/a',
                              state='html_response', content_type='text/html'),
                         ('<h1>Example</h1><div class="td-post-content">' + body + '</div>').encode())
        self.assertEqual(doc['status'], 'needs_review')
        self.assertIn('image_content_not_transcribed', doc['warnings'])
        self.assertIn('Nội dung như sau', doc['text'])

    def test_max_new_bounds_concurrent_fetches_and_keeps_remaining_pending(self):
        rows = [dict(doc_id=i, host=f'{i}.example.org', url=f'https://{i}.example.org/a')
                for i in range(10)]
        db = self.state(rows)
        self.args.max_new = 3
        async def fake_fetch(job, *args):
            await asyncio.sleep(0)
            return dict(job, state='html_response', http_status=200), dict(status='extracted_unreviewed')
        with patch.object(crawl, 'crawl_one', side_effect=fake_fetch):
            asyncio.run(crawl.run(self.args, db, self.root, time.monotonic(), {}))
        self.assertEqual(db.execute('SELECT count(page) FROM jobs').fetchone()[0], 3)
        db.close()

    def test_subdomains_share_budget_and_queue_wait_is_not_timeout(self):
        starts = []
        async def request(request):
            starts.append(time.monotonic())
            return httpx.Response(200, text='<p>OK</p>')
        async def scenario():
            self.args.host_workers = 1
            self.args.delay = .03
            self.args.timeout = .01
            async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
                downloader = crawl.Downloader(client, self.args)
                await asyncio.gather(*(downloader.fetch(f'https://{h}.39.net/a')
                                       for h in ('food','care','ask')))
                downloader.paused_groups.add('39.net')
                with self.assertRaises(crawl.HostPaused):
                    await downloader.fetch('https://baby.39.net/a')
        asyncio.run(scenario())
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(b-a >= .025 for a,b in zip(starts, starts[1:])))

    def test_rate_group_policy_can_raise_cnkang_concurrency_without_changing_39_net(self):
        active, peaks = Counter(), Counter()

        async def request(request):
            group = crawl.rate_group(request.url.host)
            active[group] += 1
            peaks[group] = max(peaks[group], active[group])
            await asyncio.sleep(.02)
            active[group] -= 1
            return httpx.Response(200, text='<p>OK</p>')

        async def scenario():
            self.args.host_workers = 1
            self.args.delay = 0
            self.args.rate_limits = {
                'www.cnkang.com': dict(host_workers=2, delay=0),
                '39.net': dict(host_workers=1, delay=0),
            }
            async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
                downloader = crawl.Downloader(client, self.args)
                await asyncio.gather(
                    downloader.fetch('https://www.cnkang.com/a'),
                    downloader.fetch('https://www.cnkang.com/b'),
                    downloader.fetch('https://ask.39.net/a'),
                    downloader.fetch('https://baby.39.net/b'))

        asyncio.run(scenario())
        self.assertEqual(peaks['www.cnkang.com'], 2)
        self.assertEqual(peaks['39.net'], 1)

    def test_handoff_snapshot_is_standalone_and_keeps_shard_identity(self):
        db = self.state([dict(doc_id=1, host='example.org', url='https://example.org/a')])
        db.execute('UPDATE jobs SET page=?,document=? WHERE doc_id=1', [
            json.dumps(dict(state='html_response')),
            json.dumps(dict(status='extracted_unreviewed'))])
        db.commit()
        snapshot = self.root / 'handoff.sqlite'
        identity, saved, planned = checkpoint_handoff.snapshot_checkpoint(
            self.root / 'state.sqlite', snapshot)
        self.assertEqual(identity['shard_id'], 0)
        self.assertEqual(identity['shard_count'], 1)
        self.assertEqual((saved, planned), (1, 1))
        self.assertTrue(snapshot.is_file())
        self.assertEqual(checkpoint_handoff.handoff_prefix(identity),
                         f"handoffs/{identity['plan_sha256']}/shard-000-of-001")
        db.close()

    def test_quality_pause_survives_resume_and_other_hosts_continue(self):
        rows = [dict(doc_id=i, host='bad.example' if i <= 6 else 'good.example',
                     url=f'https://example.org/{i}') for i in range(1, 10)]
        db = self.state(rows)
        async def fake_fetch(job, *args):
            await asyncio.sleep(0)
            page = dict(job, state='html_response', http_status=200)
            return page, dict(doc_id=job['doc_id'], status=(
                'unknown_template' if job['host']=='bad.example' else 'extracted_unreviewed'))
        paused = {}
        with patch.object(crawl, 'crawl_one', side_effect=fake_fetch):
            asyncio.run(crawl.run(self.args, db, self.root, time.monotonic(), paused))
        self.assertEqual(paused['bad.example']['reason'], 'parser_quality')
        self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE host='good.example' AND page IS NOT NULL").fetchone()[0], 3)
        saved = db.execute('SELECT count(page) FROM jobs').fetchone()[0]
        paused = json.loads(db.execute("SELECT value FROM metadata WHERE key='paused_hosts'").fetchone()[0])
        with patch.object(crawl, 'crawl_one', side_effect=AssertionError('Paused host fetched on resume')):
            asyncio.run(crawl.run(self.args, db, self.root, time.monotonic(), paused))
        self.assertEqual(db.execute('SELECT count(page) FROM jobs').fetchone()[0], saved)
        self.assertGreater(db.execute('SELECT count(*) FROM jobs WHERE page IS NULL').fetchone()[0], 0)
        db.close()

    def test_archive_preserves_bytes_and_refuses_unverified_eviction(self):
        db = self.state([dict(doc_id=i, host='example.org', url=f'https://example.org/{i}') for i in (1, 7)])
        pages = []
        for i in (1, 7):
            raw = f'<p>Original source {i}</p>'.encode()
            page = dict(doc_id=i, raw_path=f'raw/{i}.gz', sha256=hashlib.sha256(raw).hexdigest())
            (self.root / page['raw_path']).write_bytes(gzip.compress(raw))
            db.execute('UPDATE jobs SET page=?,document=? WHERE doc_id=?', [json.dumps(page),
                json.dumps(dict(doc_id=i, text=f'Parsed {i}', status='extracted_unreviewed')), i])
            pages.append(page)
        db.commit()
        store = storage.Storage(self.root, batch_docs=2)
        original = (self.root / pages[0]['raw_path']).read_bytes()
        (self.root / pages[0]['raw_path']).write_bytes(gzip.compress(b'Changed source'))
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            store.seal()
        self.assertEqual(db.execute('SELECT count(*) FROM storage_batches').fetchone()[0], 0)
        (self.root / pages[0]['raw_path']).write_bytes(original)
        self.assertTrue(store.seal())
        manifest = json.loads(db.execute('SELECT manifest FROM storage_batches').fetchone()[0])
        with self.assertRaises(ValueError):
            store.evict(manifest)
        self.assertTrue((self.root / pages[0]['raw_path']).exists())
        backup = self.root / 'remote-fixture'
        shutil.copytree(self.root / 'batches' / manifest['batch_id'], backup)
        db.execute('UPDATE storage_batches SET verified=1')
        db.commit()
        store.evict(manifest)
        shutil.copytree(backup, self.root / 'batches' / manifest['batch_id'])
        with patch.object(storage, 'sha256', wraps=storage.sha256) as digest:
            for page in pages:
                self.assertEqual(store.raw(page, manifest['batch_id']), f'<p>Original source {page["doc_id"]}</p>'.encode())
            self.assertEqual(digest.call_count, 1)  # One archive checksum per batch.
        self.assertEqual({d['doc_id'] for _,d in store.results(db)}, {1, 7})
        store._raw_archive.close()
        db.close()

    def test_malformed_table_preserves_each_cell_once(self):
        soup = BeautifulSoup('<table><tr><td>A<td>B<tr><td>C<td>D</table>', 'html.parser')
        self.assertEqual([b['text'] for b in blocks_from(soup.table, 'body')], ['A | B', 'C | D'])

    def test_invalid_cms_payload_is_not_indexable_text(self):
        html = '<h1>Example</h1><div id="dcontent">' + 'A'*300 + '</div>'
        doc = parse_page(dict(doc_id=1, host='baophutho.vn', url='https://baophutho.vn/a.htm',
                              state='html_response', content_type='text/html'), html.encode())
        self.assertEqual(doc['status'], 'encoded_content')
        self.assertEqual(doc['text'], '')

    def test_longchau_keeps_hidden_clinical_fields_and_faq_without_related_cards(self):
        state = {'props': {'pageProps': {'data': dict(
            slug='benh/example.html', headline='Example disease', description='<p>Overview.</p>',
            risk='<p>Risk factors.</p>', diagnoseAndTreaty='<p>Treatment details.</p>',
            faqs=[dict(question='When to seek help?', answer='<p>Seek help now.</p>')],
            relatedDisease=[dict(name='UNRELATED DISEASE')]), 'mediaHub': None}}}
        html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(state) + '</script>'
        doc = parse_page(dict(doc_id=7, host='nhathuoclongchau.com.vn',
                              url='https://nhathuoclongchau.com.vn/benh/example.html',
                              state='html_response', content_type='text/html'), html.encode())
        for value in ('Overview.', 'Risk factors.', 'Treatment details.', 'When to seek help?', 'Seek help now.'):
            self.assertIn(value, doc['text'])
        self.assertNotIn('UNRELATED DISEASE', doc['text'])
        self.assertTrue(any('props.pageProps.data.risk' in b['source_path'] for b in doc['blocks']))
        self.assertNotIn('block_text_mismatch', doc['warnings'])


if __name__ == '__main__':
    unittest.main()
