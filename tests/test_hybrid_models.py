import json
import sys
import unittest
from pathlib import Path
import tempfile
import glob
import time
from types import SimpleNamespace
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hybrid_inputs import embedding_weights, group_embedding_vectors, validate_query_cache, EMBEDDING_SPECS
import test_hybrid_inputs as fixtures
from hybrid_embedding_cells import ENCODE


class ModelConfig(unittest.TestCase):
    def test_single_model_and_normalized_fusion(self):
        self.assertEqual(embedding_weights('octen8b', False, {}), {'octen8b': 1.0})
        self.assertEqual(embedding_weights('qwen4b', True, dict(qwen4b=1,qwen8b=3,octen8b=0)), {'qwen4b': .25, 'qwen8b': .75})

    def test_invalid_weights(self):
        for bad in (-1, float('nan'), float('inf')):
            with self.assertRaises(AssertionError):
                embedding_weights('qwen4b', True, dict(qwen4b=1,qwen8b=bad,octen8b=0))
        with self.assertRaises(AssertionError):
            embedding_weights('qwen4b', True, dict(qwen4b=0,qwen8b=0,octen8b=0))

    def test_same_dimension_query_cache_cannot_cross_models(self):
        variants=[dict(qi=0,kind='full',text='q')]
        vectors=np.zeros((1,4096),np.float32); vectors[:,0]=1
        saved=dict(model='Qwen/Qwen3-Embedding-8B',task='task',variants=variants,query_ids=[7],
                   query_format='Instruct: {task}\nQuery:{text}',max_length=1024,model_revision='r')
        validate_query_cache(saved,vectors,'qwen8b',variants,'task',[7],revision='r')
        with self.assertRaisesRegex(AssertionError,'model differs'):
            validate_query_cache(saved,vectors,'octen8b',variants,'task',[7])
        with self.assertRaisesRegex(AssertionError,'ID order'):
            validate_query_cache(saved,vectors,'qwen8b',variants,'task',[8])
        with self.assertRaisesRegex(AssertionError,'revision'):
            validate_query_cache(saved,vectors,'qwen8b',variants,'task',[7],revision='different')

    def test_generated_encode_cell_reads_distinct_model_caches(self):
        variants=[dict(qi=0,kind='full',text='q')]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); inp=root/'input'; out=root/'output'; inp.mkdir();out.mkdir()
            weights={'qwen8b':.4,'octen8b':.6}
            for index,key in enumerate(weights):
                folder=inp/key;folder.mkdir()
                vectors=np.zeros((1,4096),np.float32);vectors[:,index]=1
                np.save(folder/f'query_variant_vectors_{key}.npy',vectors)
                saved=dict(model=EMBEDDING_SPECS[key]['model'],task='task',variants=variants,query_ids=[7],
                           query_format='Instruct: {task}\nQuery:{text}',max_length=1024,model_revision='r')
                (folder/f'query_variants_{key}.json').write_text(json.dumps(saved))
            env=dict(np=np,Path=Path,json=json,time=time,ACTIVE_MODEL_WEIGHTS=weights,
                     EMBEDDING_SPECS=EMBEDDING_SPECS,MODEL_REVISIONS={k:'r' for k in weights},
                     VARIANTS=variants,TASK='task',QUERY_IDS=[7],ENCODE_HERE=False,OUT=out,TIMINGS={},
                     validate_query_cache=validate_query_cache,note=lambda _:None,
                     glob=SimpleNamespace(glob=lambda pattern,**kw:glob.glob(pattern.replace('/kaggle/input',str(inp)),**kw)))
            exec(ENCODE,env)
            self.assertEqual(env['QUERY_VECTORS']['qwen8b'][0,0],1)
            self.assertEqual(env['QUERY_VECTORS']['octen8b'][0,1],1)


class EightBInputs(unittest.TestCase):
    setUp = fixtures.HybridInputs.setUp
    def test_octen_requires_its_document_prefix(self):
        from hybrid_inputs import validate_hybrid_inputs
        for v in self.vector_files:
            array=np.load(v); np.save(v,np.zeros((len(array),4096),np.float16))
            m=v.parent.parent/'embed_manifest.json'
            data=json.loads(m.read_text()); data.update(model=EMBEDDING_SPECS['octen8b']['model'],dim=4096,doc_prefix=' ')
            m.write_text(json.dumps(data))
        groups=group_embedding_vectors(self.vector_files,{'octen8b':1.0})
        _,_,info=validate_hybrid_inputs(self.chunk_files,groups['octen8b'],['vi','en','zh'],self.manifest,model_key='octen8b')
        self.assertEqual(info['dim'],4096)
        m=self.vector_files[0].parent.parent/'embed_manifest.json'
        data=json.loads(m.read_text()); data['doc_prefix']=''; m.write_text(json.dumps(data))
        with self.assertRaisesRegex(AssertionError,'document prefix'):
            validate_hybrid_inputs(self.chunk_files,groups['octen8b'],['vi','en','zh'],self.manifest,model_key='octen8b')


if __name__=='__main__': unittest.main()
