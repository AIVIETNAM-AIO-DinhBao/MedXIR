import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import build_hybrid_notebook
import hybrid_retrieval as hr

LONG_QUERY = ('Em bị viêm xung huyết 1/3 dưới thực quản, viêm dạ dày trợt xung huyết mạn tính, sẹo loét hành tá tràng. '
              'Cho em hỏi các bệnh này của em có nặng và có thể khỏi được không. Em cảm ơn bác sĩ.')


class QueryVariants(unittest.TestCase):
    def test_chitchat_removed_medical_words_kept(self):
        cleaned = hr.clean_query('Chào bác sĩ, cho em hỏi viêm dạ dày có lây không? Em cảm ơn bác sĩ ạ.')
        self.assertNotIn('cho em hỏi', cleaned.lower())
        self.assertNotIn('cảm ơn', cleaned.lower())
        self.assertIn('viêm dạ dày có lây không', cleaned)

    def test_short_query_not_split(self):
        self.assertEqual(hr.sub_queries('Môi nổi mụn mủ vàng là dấu hiệu của bệnh lý gì'), [])

    def test_long_query_split_into_sentences_and_clauses(self):
        subs = hr.sub_queries(LONG_QUERY, max_subq=10)
        self.assertIn('viêm dạ dày trợt xung huyết mạn tính', subs)
        self.assertIn('sẹo loét hành tá tràng', subs)
        self.assertTrue(all(len(s.split()) >= 3 for s in subs))
        self.assertEqual(len(subs), len({s.lower() for s in subs}))
        self.assertLessEqual(len(hr.sub_queries(LONG_QUERY, max_subq=2)), 2)


class Fusion(unittest.TestCase):
    def test_single_list_keeps_order(self):
        ids = ['c3', 'c1', 'c2']
        self.assertEqual([cid for cid, _ in hr.rrf([(ids, 1.0)])], ids)

    def test_agreement_beats_single_list(self):
        fused = hr.rrf([(['a', 'b'], 1.0), (['b', 'c'], 0.5)])
        self.assertEqual(fused[0][0], 'b')


class Documents(unittest.TestCase):
    # Doc A: four good chunks; doc B: one slightly better lucky chunk.
    SCORED = [('b1', 0.72), ('a1', 0.71), ('a2', 0.70), ('a3', 0.69), ('a4', 0.66)]

    @staticmethod
    def docs_of(cid):
        return [cid[0].upper()]

    def test_max_and_top3(self):
        self.assertEqual(hr.doc_ranking(self.SCORED, self.docs_of, 'max'), ['B', 'A'])
        self.assertEqual(hr.doc_ranking(self.SCORED, self.docs_of, 'top3'), ['A', 'B'])
        with self.assertRaises(ValueError):
            hr.doc_ranking(self.SCORED, self.docs_of, 'mean')

    def test_assemble_matches_old_dense_lists(self):
        # Old: docs in order of their best chunk (first appearance), chunk docs appended when missing.
        fanout = {'x': [1, 2], 'y': [3], 'z': [1], 'w': [4]}
        scored = [(c, s) for c, s in zip(['x', 'y', 'z', 'w'], [0.9, 0.8, 0.7, 0.6])]
        docs, chunks = hr.assemble(scored, [], fanout.get, n_docs=2, n_chunks=2, agg='max')
        self.assertEqual(docs, [1, 2, 3])  # 3 appended: chunk 'y' is submitted but outside the top-2 documents
        self.assertEqual(chunks, [(1, 'x'), (2, 'x'), (3, 'y')])

    def test_fill_tops_up_documents(self):
        docs, _ = hr.assemble([('a1', 1.0)], ['b1', 'c1', 'a2'], self.docs_of, n_docs=3, n_chunks=1)
        self.assertEqual(docs, ['A', 'B', 'C'])


class Notebook(unittest.TestCase):
    def test_inactive_bm25_does_not_change_dense_only_scoring_mode(self):
        import numpy as np
        env = dict(np=np, rrf=hr.rrf, USE_QUERY_SPLIT=False, USE_PRF=False,
                   USE_BM25=True, BM25_ACTIVE=False, USE_RERANK=False, QUERY_TEXT=['q'],
                   DENSE_FULL_IDS=np.array([['a', 'b']]), SUB_LISTS=[[]], PRF_IDS=None,
                   BM25_IDS=[[]], RRF_K=60, W_SUB=0.5, W_PRF=0.5, W_BM25=0.3, POOL_MAX=600)
        exec(build_hybrid_notebook.FUSE, env)
        self.assertTrue(env['ONLY_DENSE'])
        self.assertEqual([cid for cid, _ in env['POOLS'][0]], ['a', 'b'])

    def test_every_code_cell_parses_and_config_is_first(self):
        nb = build_hybrid_notebook.notebook(build_hybrid_notebook.CELLS)
        code = [''.join(c['source']) for c in nb['cells'] if c['cell_type'] == 'code']
        for source in code:
            ast.parse(source)
        self.assertIn('USE_QUERY_SPLIT', code[0])
        for switch in ['USE_CUSTOM_INSTRUCT', 'USE_PRF', 'USE_BM25', 'USE_RERANK', 'DOC_AGG']:
            self.assertIn(switch, code[0])
        json.dumps(nb)


if __name__ == '__main__':
    unittest.main()
