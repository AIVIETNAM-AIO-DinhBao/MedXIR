"""Tiny isolated fixture for parent-release filtering and recovery; no network."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import recovery_release_job as job


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SequentialPool:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def imap(self, function, tasks, **kwargs):
        return map(function, tasks)


def main():
    with tempfile.TemporaryDirectory(prefix='medxir-finalize-') as temp:
        root = Path(temp)
        inp, work = root / 'input', root / 'working'
        inp.mkdir(); work.mkdir()
        source = sqlite3.connect(root / 'source.sqlite')
        source.execute('CREATE TABLE pages(id,url,final_url,ctype,html,status)')
        docs = []
        for i, text in [(1, 'An already embedded representative document. ' * 10),
                        (2, 'Short'), (3, 'A recovered previously missing article. ' * 12),
                        (4, 'A different URL must never be imported under this ID. ' * 10)]:
            docs.append(dict(doc_id=i, url=f'https://example.test/{i}', host='example.test',
                             lang='vi', title='', text=text, baseline_state='failed', status='needs_review',
                             parser_version='fixture', source='fixture', warnings_json='[]'))
        match_rows = []
        for i in [2, 3, 4]:
            d = dict(docs[i-1])
            if i == 2: d['text'] = 'This is recovered full content for the stub article. ' * 10
            d.update(accepted=True, warnings=[])
            source.execute('INSERT INTO pages VALUES (?,?,?,?,?,200)',
                           (i, d['url'] if i != 4 else 'https://example.test/wrong', d['url'], 'text/html', json.dumps(d).encode()))
            match_rows.append(dict(doc_id=i, url=d['url'], host=d['host'], lang='vi', source_path='db/test.sqlite'))
        source.commit(); source.close()
        data = (root / 'source.sqlite').read_bytes()
        pq.write_table(pa.Table.from_pylist(match_rows), inp / 'source_matches.parquet')
        (inp / 'source_files.json').write_text(json.dumps([dict(path='db/test.sqlite', size=len(data), lfs=dict(oid=digest(data)))]))
        extra_path = root / 'extra_sources.json'
        extra_path.write_text(json.dumps([dict(path=n, size=len(data), lfs=dict(oid=digest(data)))
                                          for n in ['db_cc/example.test.sqlite','db_wayback/example.test.sqlite']]))
        raw = ('\n'.join(json.dumps(d) for d in docs[:2]) + '\n').encode()
        (inp / 'seed_documents.jsonl.gz').write_bytes(gzip.compress(raw))
        baseline = dict(dataset_version='v1', dataset_built_at='fixture', chunk_params='350/50/40', clean_version='clean-v1')
        (inp / 'chunk_manifest.json').write_text(json.dumps(baseline))
        pq.write_table(pa.Table.from_pylist([dict(text_sha256=digest(docs[0]['text'].encode()), rep_doc_id=100,
                                                 group_type='single', fanout_safe=True)]), inp / 'hash_groups.parquet')
        parent = inp / 'parent'; parent.mkdir()
        parent_docs = parent / 'documents_additions_20261029_v2.parquet'
        pq.write_table(pa.Table.from_pylist(docs[:2]), parent_docs)
        (parent / 'file_audit.json').write_text(json.dumps([dict(path='db/test.sqlite', status='parsed')]))
        (parent / 'needs_review.jsonl.gz').write_bytes(gzip.compress((json.dumps(dict(doc_id=3, status='old_failure'))+'\n').encode()))
        assert list(job.read_jsonl(parent / 'needs_review.jsonl.gz')) == [dict(doc_id=3, status='old_failure')]
        (parent / 'needs_review.jsonl').write_bytes(gzip.decompress((parent / 'needs_review.jsonl.gz').read_bytes()))
        (parent / 'needs_review.jsonl.gz').unlink()
        (parent / 'release_manifest.json').write_text(json.dumps(dict(release='fixture', baseline_manifest=baseline,
            accepted_document_ids=2, source_candidate_ids=3,
            files={parent_docs.name:dict(sha256=digest(parent_docs.read_bytes()))})))
        (inp / 'all_vi_new_records.json').write_text(json.dumps([dict(doc_id=i, usable_text=True) for i in [1,2,3]]))
        (inp / 'recovery_manifest.json').write_text(json.dumps(dict(release='fixture',
            source_matches_sha256=digest((inp / 'source_matches.parquet').read_bytes()),
            baseline_version='v1', baseline_built_at='fixture', seed_count=2, seed_raw_sha256=digest(raw),
            source_repository='fixture', source_revision='fixture', all_vi_not_in_v1=3)))

        def path(value):
            return root / str(value).removeprefix('/kaggle/') if str(value).startswith('/kaggle/') else Path(value)

        class Context:
            def Pool(self, count): return SequentialPool()

        with patch.object(job, 'Path', path), patch.object(job, 'urlopen', lambda *a, **k: io.BytesIO(data)), \
             patch.object(job.mp, 'get_context', lambda *a: Context()), \
             patch.object(job.worker, 'parse_html', lambda task: (dict(doc_id=task[0], accepted=False,
                 status='unknown_template', source_path=task[-1]) if task[-1].startswith('db_cc/') else json.loads(task[6]))), \
             patch.dict(os.environ, MEDXIR_PARENT_RELEASE='1', MEDXIR_STUB_IDS='[2]',
                        MEDXIR_EXTRA_SOURCE_FILES=str(extra_path)):
            job.run()
        out = work / 'recovery_release_20261029_v2'
        report = json.loads((out / 'release_manifest.json').read_text())
        assert report['accepted_document_ids'] == 3
        assert report['alias_document_ids'] == 1
        assert report['new_text_groups'] == 2
        assert report['vi_with_text_missing'] == 0
        assert report['removed_short_crawl_stubs_recovered'] == 1
        rejected=[json.loads(line) for line in gzip.decompress((out / 'needs_review.jsonl.gz').read_bytes()).decode().split('\n') if line]
        assert {r['doc_id'] for r in rejected} == {4}
        assert all(r['status'] == 'source_url_mismatch' for r in rejected)
        assert report['source_file_statuses'] == {'parsed': 3}
        assert report['unresolved_source_candidate_ids'] == 1
        assert not list((work / 'recovery_scratch').glob('parsed.sqlite*'))
        assert pq.read_table(out / 'chunk_corpus_additions_20261029_v2.parquet').num_rows == report['new_chunks']
        print('PASS: stub replacement, alternate recovery, URL mismatch rejection, alias reuse, review cleanup, separate chunks')


if __name__ == '__main__': main()
