import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hybrid_inputs import validate_hybrid_inputs


class HybridInputs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / 'corpus'
        self.manifest = self.corpus / 'chunk_manifest.json'
        self.chunk_files, self.vector_files = [], []
        self.counts = {}
        # Twelve parts, including a smaller-than-top-k EN additions part.
        for lang, count in [('vi', 8), ('en', 2), ('zh', 2)]:
            self.counts[lang] = 0
            for part in range(count):
                n = 1 if lang == 'en' and part == 1 else 3
                ids = [f'{lang}-{part}-{i}' for i in range(n)]
                c = self.corpus / 'chunks' / lang / f'part-{part:03d}.parquet'
                c.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(pa.table({'chunk_id': ids, 'lang': [lang] * n}), c)
                # Different dataset roots ensure manifests are collected per source.
                v = self.root / f'vectors-{lang}-{part}' / 'vectors' / f'{lang}-part-{part:03d}.npy'
                v.parent.mkdir(parents=True)
                np.save(v, np.zeros((n, 2560), dtype=np.float16))
                pq.write_table(pa.table({'chunk_id': ids}), v.with_name(v.stem + '-ids.parquet'))
                (v.parent.parent / 'embed_manifest.json').write_text(json.dumps(dict(
                    complete=True, model='Qwen/Qwen3-Embedding-4B', dim=2560,
                    max_length=1024, dtype='torch.bfloat16')))
                self.chunk_files.append(c); self.vector_files.append(v)
                self.counts[lang] += n
        self.manifest.write_text(json.dumps(dict(dataset_version='v2', languages={
            lang: dict(chunks=n) for lang, n in self.counts.items()})))

    def check(self, langs=('vi', 'en', 'zh'), vectors=None):
        return validate_hybrid_inputs(self.chunk_files, self.vector_files if vectors is None else vectors,
                                      langs, self.manifest)

    def test_all_twelve_parts_and_one_row_en(self):
        chunks, vectors, info = self.check()
        self.assertEqual(len(chunks), 12)
        self.assertEqual(set(chunks), set(vectors))
        self.assertEqual(info['rows_by_language'], self.counts)
        self.assertEqual(info['vector_count'], sum(self.counts.values()))

    def test_language_subset_and_unselected_missing_vectors(self):
        only_vi = [p for p in self.vector_files if p.stem.startswith('vi-')]
        chunks, vectors, info = self.check(('vi',), only_vi)
        self.assertEqual(len(vectors), 8)
        self.assertEqual(info['rows_by_language'], {'vi': self.counts['vi']})
        self.assertTrue(all(Path(p).parent.name == 'vi' for p in chunks.values()))

    def test_missing_additions_vector_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'missing vectors'):
            self.check(vectors=self.vector_files[:-1])

    def test_duplicate_part_cannot_be_silently_overwritten(self):
        with self.assertRaisesRegex(AssertionError, 'Duplicate part'):
            self.check(vectors=self.vector_files + [self.vector_files[0]])

    def test_same_count_wrong_id_order_is_rejected(self):
        v = self.vector_files[0]
        path = v.with_name(v.stem + '-ids.parquet')
        ids = pq.read_table(path)['chunk_id'].to_pylist()
        pq.write_table(pa.table({'chunk_id': ids[::-1]}), path)
        with self.assertRaisesRegex(AssertionError, 'ID order mismatch'):
            self.check()

    def test_wrong_dimensions_are_rejected(self):
        np.save(self.vector_files[0], np.zeros((3, 4096), dtype=np.float16))
        with self.assertRaises(AssertionError): self.check()

    def test_duplicate_ids_across_parts_are_rejected(self):
        first, second = self.vector_files[:2]
        ids = pq.read_table(second.with_name(second.stem + '-ids.parquet'))['chunk_id'].to_pylist()
        ids[0] = pq.read_table(first.with_name(first.stem + '-ids.parquet'))['chunk_id'][0].as_py()
        pq.write_table(pa.table({'chunk_id': ids}), second.with_name(second.stem + '-ids.parquet'))
        c = self.chunk_files[1]
        pq.write_table(pa.table({'chunk_id': ids, 'lang': ['vi'] * len(ids)}), c)
        with self.assertRaisesRegex(AssertionError, 'Duplicate chunk IDs'):
            self.check()

    def test_corpus_version_and_manifest_counts_are_checked(self):
        m = json.loads(self.manifest.read_text()); m['dataset_version'] = 'v1'
        self.manifest.write_text(json.dumps(m))
        with self.assertRaisesRegex(AssertionError, 'Expected corpus'):
            self.check()
        m['dataset_version'] = 'v2'; m['languages']['vi']['chunks'] += 1
        self.manifest.write_text(json.dumps(m))
        with self.assertRaisesRegex(AssertionError, 'manifest count mismatch'):
            self.check()

    def test_wrong_embedding_model_is_rejected(self):
        p = self.vector_files[0].parent.parent / 'embed_manifest.json'
        m = json.loads(p.read_text()); m['model'] = 'another-model'; p.write_text(json.dumps(m))
        with self.assertRaisesRegex(AssertionError, 'Incompatible vector manifest'):
            self.check()


if __name__ == '__main__': unittest.main()
