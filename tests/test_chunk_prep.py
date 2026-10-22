"""Offline checks for source cleaning, duplicate groups and the generated chunking notebook.

Run: .venv/bin/python -m unittest tests/test_chunk_prep.py -v
"""
import contextlib
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_chunk_notebook
import chunking
import dedup_groups
import text_clean


class CleanTextChecks(unittest.TestCase):
    def test_line_break_tags_become_newlines(self):
        for tag in ['<br>', '<br/>', '<br />', '<BR>', '＜br＞', '＜br ／＞', '&lt;br&gt;']:
            with self.subTest(tag=tag):
                self.assertEqual(text_clean.clean_text(f'a{tag}b', 'zh'), 'a\nb')

    def test_consecutive_tags_make_one_blank_line_at_most(self):
        self.assertEqual(text_clean.clean_text('a<br><br><br>b', 'zh'), 'a\n\nb')
        self.assertEqual(text_clean.clean_text('a\n\n<br>\n\nb', 'zh'), 'a\n\nb')
        self.assertEqual(text_clean.clean_text('a\n<br>b', 'zh'), 'a\nb')

    def test_repeated_lead_in_is_dropped(self):
        text = ('核心提示：婴儿泪囊炎 婴儿泪囊炎眼睛有分泌物是一种眼睛轻度感染性疾病……\n\n'
                '婴儿泪囊炎眼睛有分泌物是一种眼睛轻度感染性疾病。一是可能由于分娩过程')
        self.assertEqual(text_clean.clean_text(text, 'zh'),
                         '婴儿泪囊炎眼睛有分泌物是一种眼睛轻度感染性疾病。一是可能由于分娩过程')

    def test_lead_in_with_only_label_is_dropped(self):
        self.assertEqual(text_clean.clean_text('核心提示：\n\n澳大利亚一项调查', 'zh'), '澳大利亚一项调查')

    def test_standalone_lead_in_is_kept(self):
        text = '核心提示：这是一段独立的摘要，不会在文章其他地方出现。\n\n正文讲的是完全不同的另一件事。'
        self.assertEqual(text_clean.clean_text(text, 'zh'), text)

    def test_lead_in_rule_is_zh_only(self):
        text = '核心提示：婴儿泪囊炎的症状……\n\n婴儿泪囊炎的症状很多。'
        self.assertEqual(text_clean.clean_text(text, 'en'), text)

    def test_clean_text_is_a_no_op_on_clean_text(self):
        text = '第一段，没有标签。\n\n第二段：有冒号但不是核心提示。\n\nLine with  double  spaces.'
        for lang in ['zh', 'vi', 'en']:
            with self.subTest(lang=lang):
                self.assertEqual(text_clean.clean_text(text, lang), text)

    def test_chunks_of_cleaned_text_are_verbatim_slices(self):
        text = '核心提示：某句话……\n\n' + '这是一句很长的中文句子。' * 120 + '<br>' + '另一段。' * 80
        cleaned = text_clean.clean_text(text, 'zh')
        for chunk in chunking.chunk_document(cleaned, 'zh'):
            self.assertEqual(cleaned[chunk.start:chunk.end], chunk.text)


class DuplicateGroupChecks(unittest.TestCase):
    def registry(self, rows):
        columns = ['doc_id', 'url', 'host', 'state', 'text_sha256']
        return pd.DataFrame(rows, columns=columns)

    def test_hash_groups_classification(self):
        registry = self.registry([
            (1, 'https://a.vn/x.html', 'a.vn', 'in_corpus', 'h1'),
            (2, 'https://www.a.vn/x.html/', 'a.vn', 'in_corpus', 'h1'),
            (3, 'https://a.vn/only', 'a.vn', 'in_corpus', 'h2'),
            (4, 'https://b.vn/p', 'b.vn', 'in_corpus', 'h3'),
            (5, 'https://c.vn/q', 'c.vn', 'in_corpus', 'h3'),
            (6, 'https://d.vn/a/1', 'd.vn', 'in_corpus', 'h4'),
            (7, 'https://d.vn/b/2', 'd.vn', 'in_corpus', 'h4'),
            (8, 'https://a.vn/x.html', 'a.vn', 'blocked', 'h2'),
            (9, 'https://e.vn/z', 'e.vn', 'in_corpus', 'h5'),
        ])
        groups = dedup_groups.hash_groups(registry, excluded_ids=[9]).set_index('text_sha256')
        self.assertEqual(set(groups.index), {'h1', 'h2', 'h3', 'h4'})
        self.assertEqual(groups.loc['h1', 'group_type'], 'url_variant')
        self.assertEqual(groups.loc['h1', 'rep_doc_id'], 1)
        self.assertEqual(groups.loc['h1', 'doc_ids'], [1, 2])
        self.assertEqual(groups.loc['h2', 'group_type'], 'single')
        self.assertEqual(groups.loc['h2', 'doc_ids'], [3])
        self.assertEqual(groups.loc['h3', 'group_type'], 'cross_host')
        self.assertEqual(groups.loc['h4', 'group_type'], 'same_host_diff_path')
        self.assertEqual(groups.loc['h4', 'n_ids'], 2)
        self.assertEqual(list(groups.fanout_safe), [True, True, False, False])

    def test_excluded_id_leaves_its_group(self):
        registry = self.registry([
            (1, 'https://a.vn/x', 'a.vn', 'in_corpus', 'h1'),
            (2, 'https://b.vn/x', 'b.vn', 'in_corpus', 'h1'),
        ])
        groups = dedup_groups.hash_groups(registry, excluded_ids=[1])
        self.assertEqual(groups.group_type.tolist(), ['single'])
        self.assertEqual(groups.rep_doc_id.tolist(), [2])

    def test_pharmacity_exclusions_by_slug(self):
        records = [
            dict(doc_id=10, url='https://pmc-ecm-healthblog.beta.pharmacity.io/10-cach-kich/',
                 final_url='https://www.pharmacity.vn/10-cach-kich.htm'),
            dict(doc_id=11, url='https://pmc-ecm-healthblog.beta.pharmacity.io/10-cach-kich/',
                 final_url='https://www.pharmacity.vn/xem-sex.htm'),
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'documents.jsonl.gz'
            with gzip.open(path, 'wt', encoding='utf-8') as file:
                file.writelines(json.dumps(record) + '\n' for record in records)
            exclusions = dedup_groups.pharmacity_exclusions(path)
        self.assertEqual(exclusions.doc_id.tolist(), [11])
        self.assertEqual(exclusions.reason.tolist(), [dedup_groups.REASON])


class NotebookChecks(unittest.TestCase):
    def test_notebook_matches_its_sources(self):
        exclusions = ROOT / 'artifacts' / 'aux_v1' / 'exclusions.csv'
        notebook = ROOT / 'src' / 'notebooks' / 'kaggle_chunk_corpus.ipynb'
        if not (exclusions.exists() and notebook.exists()):
            self.skipTest('artifacts/aux_v1 or the notebook is not built here')
        with contextlib.chdir(ROOT):  # the builder reads scripts/ relative to the repo root
            cells = build_chunk_notebook.build_cells(build_chunk_notebook.SCRIPTS,
                                                     build_chunk_notebook.excluded_ids(exclusions))
        built = json.dumps(build_chunk_notebook.notebook(cells), ensure_ascii=False, indent=1) + '\n'
        self.assertEqual(built, notebook.read_text(encoding='utf-8'))
        for name in build_chunk_notebook.SCRIPTS:
            self.assertNotIn("if __name__ == '__main__':", build_chunk_notebook.copied_source(ROOT / 'scripts' / name))


if __name__ == '__main__':
    unittest.main()
