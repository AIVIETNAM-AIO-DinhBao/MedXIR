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
import pubmed_europepmc
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

    def test_wiki_tail_drops_navigation_but_keeps_linked_clinical_lists(self):
        def page(tail):
            html = ('<h1 id="firstHeading">Entry</h1><div id="bodyContent"><p>' + '正文内容。' * 50 + '</p>'
                    '<h2>临床意义</h2><p>增加：<a href="/w/a">心肌梗塞</a>，<a href="/w/b">高血压</a>。</p>' + tail +
                    '<h2>参考文献</h2><p>[1] 指南.</p><h2>参看</h2><ul><li><a href="/w/c">疫苗</a></li></ul>'
                    '<h2>怎样看化验单专题</h2><p>关于医学化验单：</p><ul><li><a href="/w/d">化验</a></li></ul>'
                    '<h2>健康问答网关于疫苗的相关提问</h2><table><tr><td><a href="/q">问题?</a></td></tr></table>'
                    '<h2>更多医学百科条目</h2><div id="catlinks"><a href="/c">分类</a></div></div>')
            return parse_page(dict(doc_id=1, host='www.a-hospital.com', url='https://www.a-hospital.com/w/x',
                                   state='html_response', content_type='text/html'), html.encode())
        doc = page('')
        for value in ('心肌梗塞', '高血压', '[1] 指南.'):
            self.assertIn(value, doc['text'])
        for value in ('疫苗', '化验', '问题?', '更多医学百科条目', '参看'):
            self.assertNotIn(value, doc['text'])
        self.assertTrue(any(r['reason'] == 'wiki_tail_navigation' for r in doc['removed']))
        # An unrecognized link-dense last section is kept but stays in review.
        self.assertIn('wiki_tail_requires_review', doc['warnings'])
        doc = page('<h2>治疗</h2><p>' + '应及时就医并规范治疗。' * 3 + '</p>')
        self.assertEqual(doc['warnings'], [])
        self.assertEqual(doc['status'], 'extracted_unreviewed')
        # A lookup page made only of navigation sections keeps its text as a listing.
        html = ('<h1 id="firstHeading">头部疾病查询</h1><div id="bodyContent"><h2>按疾病发生部位查询疾病</h2>'
                '<ul><li><a href="/w/a">头痛</a></li><li><a href="/w/b">偏头痛</a></li></ul></div>')
        doc = parse_page(dict(doc_id=2, host='www.a-hospital.com', url='https://www.a-hospital.com/w/y',
                              state='html_response', content_type='text/html'), html.encode())
        self.assertIn('偏头痛', doc['text'])
        self.assertEqual(doc['page_type'], 'listing')
        self.assertIn('listing_not_single_article', doc['warnings'])

    def test_short_qa_is_complete_only_with_question_and_answer(self):
        def page(answer):
            html = ('<div class="ask_tit">斑秃需要戒酒吗？</div><div class="ask_hid"><p class="txt_ms">斑秃需要戒酒吗？</p></div>'
                    '<div class="selected"><div class="sele_all"><div class="sele_txt">' + answer + '</div></div></div>')
            return parse_page(dict(doc_id=1, host='ask.39.net', url='https://ask.39.net/question/1.html',
                                   state='html_response', content_type='text/html'), html.encode())
        doc = page('喝酒会加重斑秃，建议戒酒。')
        self.assertNotIn('short_content_requires_review', doc['warnings'])
        self.assertEqual(doc['status'], 'extracted_unreviewed')
        # An empty answer box is not an answer.
        doc = page('')
        self.assertIn('short_content_requires_review', doc['warnings'])
        self.assertEqual(doc['status'], 'needs_review')

    def test_legacy_39_and_cnkang_templates_and_msd_topic_index(self):
        cases = [('care.39.net', '<div class="wrap"><p>' + '偏方正文。' * 10 + '</p></div><div class="wrap tools">分享</div>'),
                 ('fk.39.net', '<div class="artbox"><div class="article"><p>' + '妇科正文。' * 10 + '</p></div></div>'),
                 ('man.39.net', '<div class="art"><div class="content"><p>' + '男性正文。' * 10 + '</p></div></div>'),
                 ('www.cnkang.com', '<table><tr><td id="iContent"><p>' + '皮肤过敏。' * 10 + '</p></td></tr></table>')]
        for host, body in cases:
            doc = parse_page(dict(doc_id=1, host=host, url=f'http://{host}/a/1.html', state='html_response',
                                  content_type='text/html'), ('<title>T</title><body>' + body + '</body>').encode())
            self.assertNotEqual(doc['status'], 'unknown_template', host)
            self.assertIn('正文' if host.endswith('39.net') else '皮肤过敏', doc['text'])
            self.assertNotIn('分享', doc['text'])
        doc = parse_page(dict(doc_id=2, host='www.msdmanuals.cn', url='https://www.msdmanuals.cn/home/infections',
                              state='html_response', content_type='text/html'),
                         '<title>T</title><div class="Section_content__x"><a href="/a">感染概述</a></div>'.encode())
        self.assertEqual((doc['page_type'], doc['status']), ('listing', 'needs_review'))
        self.assertIn('chapter_index', doc['warnings'])

    def test_vi_magazine_video_login_and_empty_body_pages(self):
        def parse(host, html, url='https://example.vn/a.htm'):
            return parse_page(dict(doc_id=1, host=host, url=url, state='html_response',
                                   content_type='text/html'), ('<title>T</title>' + html).encode())
        body = '<p>' + 'Nội dung tạp chí. ' * 20 + '</p>'
        self.assertIn('Nội dung tạp chí', parse('suckhoedoisong.vn', f'<div class="sp-detail-content">{body}</div>')['text'])
        self.assertIn('Nội dung tạp chí', parse('vov2.vov.vn', '<article class="node node--emagazine">'
                                                f'<div class="content node-emagazine">{body}</div></article>')['text'])
        sapo = '<h2 class="detail-sapo">SKĐS - Tóm tắt bài video.</h2><div class="detail-content"></div>'
        doc = parse('suckhoedoisong.vn', sapo + '<div class="detail-frame-video"><div data-vid="v.mp4"></div></div>')
        self.assertEqual(doc['text'], 'SKĐS - Tóm tắt bài video.')
        self.assertIn('media_transcript_not_verified', doc['warnings'])
        doc = parse('suckhoedoisong.vn', sapo)
        self.assertIn('body_empty_in_source', doc['warnings'])
        self.assertEqual(doc['status'], 'needs_review')
        # Prose elsewhere means the template changed: keep it a parser failure.
        doc = parse('suckhoedoisong.vn', sapo + '<section>' + body + '</section>')
        self.assertEqual(doc['status'], 'empty_content')
        doc = parse('thaythuocvietnam.vn', '<meta property="og:title" content="Đăng nhập - Thầy Thuốc Việt Nam">')
        self.assertEqual((doc['status'], doc['warnings']), ('source_error', ['login_required']))

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

    def test_reparse_survives_batch_sealed_by_storage_connection_mid_run(self):
        # Sealing writes through a second SQLite connection; reparse must not hold
        # a stale WAL read snapshot across it (seen as "database is locked").
        root = self.root / 'out' / 'shard-000-of-001'
        (root / 'raw').mkdir(parents=True)
        rows = [dict(doc_id=i, host='baoquangtri.vn', url=f'https://baoquangtri.vn/{i}') for i in range(1, 121)]
        self.state(rows).close()
        shutil.move(self.root / 'state.sqlite', root / 'state.sqlite')
        db = sqlite3.connect(root / 'state.sqlite')
        for row in rows:
            raw = f'<div class="td-post-content"><p>Bài {row["doc_id"]}</p></div>'.encode()
            page = dict(row, state='html_response', http_status=200, content_type='text/html',
                        raw_path=f'raw/{row["doc_id"]}.gz', sha256=hashlib.sha256(raw).hexdigest())
            (root / page['raw_path']).write_bytes(gzip.compress(raw))
            db.execute('UPDATE jobs SET page=?,document=? WHERE doc_id=?', [json.dumps(page),
                       json.dumps(dict(doc_id=row['doc_id'], status='needs_review', parser_version='old')), row['doc_id']])
        db.commit()
        db.close()
        config = self.root / 'storage.json'
        config.write_text('{}')
        argv = ['crawl.py', '--reparse', '--parse-workers', '1', '--plan', str(self.args.plan),
                '--output', str(self.root / 'out'), '--storage-config', str(config),
                '--batch-docs', '50', '--progress-every', '1000']
        local = lambda root, config, docs, mb, prune: storage.Storage(root, None, docs, mb, False)
        with patch.object(sys, 'argv', argv), patch.object(crawl, 'Storage', side_effect=local), \
                patch.object(crawl, 'write_report', side_effect=lambda root, storage=None: followup.write_report(root)):
            crawl.main()
        db = sqlite3.connect(root / 'state.sqlite')
        versions = Counter(json.loads(d)['parser_version'] for (d,) in db.execute('SELECT document FROM jobs'))
        self.assertEqual(versions, Counter({crawl.VERSION: 120}))
        self.assertGreater(db.execute('SELECT count(*) FROM storage_batches').fetchone()[0], 0)
        db.close()

    def test_europepmc_records_become_documents_and_missing_ids_are_reported(self):
        plan = self.root / 'en.jsonl'
        plan.write_text(''.join(json.dumps(dict(doc_id=i, host='pubmed.ncbi.nlm.nih.gov',
                        url=f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/')) + '\n'
                        for i, pmid in [(1, '11'), (2, '22'), (3, '33')]))
        db, plan_sha = pubmed_europepmc.open_state(self.root / 'epmc', plan)
        records = [dict(id='11', source='MED', title='A <i>trial</i>.', pubYear='2020',
                        abstractText='<h4>Background</h4>Why it <sup>2</sup>matters.<h4>Results</h4>It worked.'),
                   dict(id='22', source='MED', title='Title only.')]
        def respond(request):
            self.assertIn('EXT_ID:11 OR EXT_ID:22 OR EXT_ID:33', request.url.params['query'])
            return httpx.Response(200, json=dict(hitCount=2, resultList=dict(result=records)))
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            found = pubmed_europepmc.search(client, ['11', '22', '33'])
        docs = {pmid: pubmed_europepmc.to_document(i, f'u{i}', pmid, found.get(pmid), 't')
                for i, pmid in [(1, '11'), (2, '22'), (3, '33')]}
        self.assertEqual([(b['kind'], b['text']) for b in docs['11']['blocks']],
                         [('heading', 'A trial.'), ('heading', 'Background'), ('paragraph', 'Why it 2matters.'),
                          ('heading', 'Results'), ('paragraph', 'It worked.')])
        self.assertEqual(docs['11']['status'], 'extracted_unreviewed')
        self.assertEqual((docs['22']['status'], docs['22']['warnings']), ('needs_review', ['abstract_missing']))
        self.assertEqual(docs['33']['status'], 'not_found')
        with db:
            for i, pmid in [(1, '11'), (2, '22'), (3, '33')]:
                db.execute('UPDATE items SET status=?,document=?,record=? WHERE doc_id=?',
                           [docs[pmid]['status'], json.dumps(docs[pmid]), json.dumps(found.get(pmid)), i])
        self.assertTrue(pubmed_europepmc.seal(db, self.root / 'epmc', plan_sha, 10, force=True))
        counts = pubmed_europepmc.write_report(db, self.root / 'epmc', plan_sha, None)
        self.assertEqual(counts, Counter(extracted_unreviewed=1, needs_review=1, not_found=1))
        with gzip.open(self.root / 'epmc' / 'followup' / 'documents.csv.gz', 'rt') as report:
            self.assertEqual([row.split(',')[3] for row in report.read().splitlines()[1:]], ['needs_review', 'not_found'])
        work = dict(id='https://openalex.org/W1', title='Preprint.', type='preprint',
                    abstract_inverted_index={'Viral': [0], 'load': [1, 4], 'and': [3], 'fell': [2]})
        doc = pubmed_europepmc.openalex_document(3, 'u3', '33', work, 't')
        self.assertEqual((doc['source'], doc['status'], doc['blocks'][1]['text']),
                         ('openalex', 'extracted_unreviewed', 'Viral load fell and load'))
        with self.assertRaisesRegex(ValueError, 'Not a PubMed'):
            plan.write_text(json.dumps(dict(doc_id=9, host='x.org', url='https://x.org/1')) + '\n')
            pubmed_europepmc.open_state(self.root / 'other', plan)
        db.close()

    def test_retry_host_unpauses_only_named_hosts(self):
        root = self.root / 'out' / 'shard-000-of-001'
        (root / 'raw').mkdir(parents=True)
        self.state([dict(doc_id=1, host='a.vn', url='https://a.vn/1')]).close()
        shutil.move(self.root / 'state.sqlite', root / 'state.sqlite')
        paused = {'a.vn': dict(reason='parser_quality'), 'b.vn': dict(reason='deferred:blocked')}
        with sqlite3.connect(root / 'state.sqlite') as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('paused_hosts',?)", [json.dumps(paused)])
        seen = []
        async def fake_run(args, db, root, started, paused, storage=None):
            seen.append(dict(paused))
        argv = ['crawl.py', '--resume', '--plan', str(self.args.plan), '--output', str(self.root / 'out')]
        with patch.object(crawl, 'run', side_effect=fake_run), patch.object(crawl, 'write_report',
                side_effect=lambda root, storage=None: followup.write_report(root)):
            with patch.object(sys, 'argv', argv + ['--retry-host', 'a.vn']), self.assertRaises(SystemExit):
                crawl.main()  # Exit 2: the job is still pending in this fake run.
            # Reusing the command after a host was already unpaused is harmless.
            with patch.object(sys, 'argv', argv + ['--retry-host', 'a.vn']), self.assertRaises(SystemExit):
                crawl.main()
        self.assertEqual(seen, [{'b.vn': dict(reason='deferred:blocked')}] * 2)
        with sqlite3.connect(root / 'state.sqlite') as db:
            saved = json.loads(db.execute("SELECT value FROM metadata WHERE key='paused_hosts'").fetchone()[0])
        self.assertEqual(saved, {'b.vn': dict(reason='deferred:blocked')})

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
