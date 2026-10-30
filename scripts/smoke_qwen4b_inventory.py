"""Small offline fixture: old-ID skips, input reuse and unique new inference."""
from pathlib import Path
from unittest.mock import patch
import hashlib,json,tempfile,shutil
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import prepare_qwen4b_delta as job
import embed_qwen4b_delta as gpu_job

def main():
 with tempfile.TemporaryDirectory(prefix='medxir-embed-plan-') as temp:
  root=Path(temp);inp=root/'input';inp.mkdir();(root/'working').mkdir()
  base=inp/'base';(base/'vectors').mkdir(parents=True)
  old=[]
  for p in range(9):
   folder=base/'chunks'/'vi';folder.mkdir(parents=True,exist_ok=True)
   rs=[]
   for i in range(3):
    text=f'Old article {p} paragraph {i}.';cid=hashlib.sha256(text.encode()).hexdigest()[:16]+'-0'
    rs.append(dict(chunk_id=cid,chunk_text=text,lang='vi'));old.append(rs[-1])
   key=f'vi-part-{p:03d}'
   pq.write_table(pa.Table.from_pylist(rs),folder/f'part-{p:03d}.parquet')
   pq.write_table(pa.Table.from_pylist([dict(chunk_id=r['chunk_id']) for r in rs]),base/'vectors'/f'{key}-ids.parquet')
   vec=np.zeros((3,2560),np.float16);vec[:,0]=1;np.save(base/'vectors'/f'{key}.npy',vec)
  manifests=[]
  for i in range(3):
   d=inp/f'vecs-{i}';d.mkdir();f=d/'embed_manifest.json'
   f.write_text(json.dumps(dict(model='Qwen/Qwen3-Embedding-4B',dim=2560,dtype='torch.bfloat16',max_length=1024,batch=8,complete=True,nan_rows=0,parts_done=3,parts_total=3,shard=i)))
   manifests.append(job.sha(f))
  added=[dict(chunk_id='a'*16+'-0',chunk_text=old[0]['chunk_text'],lang='vi',text_sha256='a'*64),
         dict(chunk_id='b'*16+'-0',chunk_text='A completely new medical passage.',lang='vi',text_sha256='b'*64),
         dict(chunk_id='c'*16+'-0',chunk_text='A completely new medical passage.',lang='vi',text_sha256='c'*64),
         dict(old[1],text_sha256='d'*64),
         dict(chunk_id='e'*16+'-0',chunk_text='Another new medical passage.',lang='vi',text_sha256='e'*64)]
  release=inp/'release';release.mkdir();chunk=release/'chunk_corpus_additions_20261029_v2.parquet'
  pq.write_table(pa.Table.from_pylist(added),chunk)
  m=dict(parent_release_manifest_sha256='fixture',baseline_manifest={'fixture':True},new_chunks=5,files={chunk.name:dict(sha256=job.sha(chunk))})
  f=release/'release_manifest.json';f.write_text(json.dumps(m))
  model=inp/'model/models';(model/'Qwen3-Embedding-4B').mkdir(parents=True)
  package=model/'MODELS.json';package.write_text(json.dumps(dict(models={'Qwen/Qwen3-Embedding-4B':dict(sha='fixture',folder='models/Qwen3-Embedding-4B',files=[])})))
  spec=dict(release_manifest_sha256=job.sha(f),baseline_chunk_manifest=m['baseline_manifest'],baseline_embed_manifest_sha256=manifests,
            model_revision='fixture',model_package_manifest_sha256=job.sha(package),expected_old_chunks=27)
  def route(value):return root/str(value).removeprefix('/kaggle/') if str(value).startswith('/kaggle/') else Path(value)
  with patch.object(job,'Path',route):job.run(spec)
  out=root/'working/qwen4b_additions_plan';r=json.loads((out/'embedding_plan_manifest.json').read_text())
  assert r['already_embedded_chunk_ids']==1 and r['output_chunks']==4
  assert r['actions']==dict(copy_old=1,infer=2,copy_new=1)
  assert r['old_reuse_unique_inputs']==1 and r['old_chunks_verified']==27
  tasks=pq.read_table(out/'embedding_tasks.parquet').to_pylist()
  assert tasks[2]['canonical_output_row']==1 and tasks[2]['action']=='copy_new'
  assert np.load(out/'reused_old_vectors.npy').shape==(1,2560)
  assert np.load(out/'golden_vectors.npy').shape==(27,2560)
  print('PASS: old IDs skipped, old inputs copied, duplicate new inputs inferred once, vector/ID alignment')
  mounted=inp/'plan/qwen4b_additions_plan';shutil.copytree(out,mounted)
  gpu_spec=dict(embedding_plan_manifest_sha256=job.sha(mounted/'embedding_plan_manifest.json'))
  forwarded=[]
  def fake_loader(path):
   tally=dict(forward_inputs=0,tokens=0,inputs_at_length_cap=0)
   def embed(texts):
    forwarded.extend(texts);tally['forward_inputs']+=len(texts)
    v=np.zeros((len(texts),2560),np.float16)
    for i,t in enumerate(texts):v[i,0 if t.startswith('Old article') else 1 if t.startswith('A completely') else 2]=1
    return v
   return embed,tally,dict(gpu='fixture RTX 6000',torch='fixture',transformers='fixture')
  destination=root/'gpu-first'
  with patch.object(gpu_job,'make_embedder',fake_loader),patch.object(gpu_job,'PART_ROWS',2):
   gpu_job.run(gpu_spec,inp,destination)
  done=json.loads((destination/'incremental_embed_manifest.json').read_text())
  assert done['complete'] and done['completed_rows']==4 and done['parts_done']==2
  assert len(forwarded)==29 and forwarded.count('A completely new medical passage.')==1
  matrices=[np.load(destination/p['vectors']['path']) for p in done['parts']]
  exported_ids=[cid for p in done['parts'] for cid in pq.read_table(destination/p['ids']['path'],columns=['chunk_id']).column(0).to_pylist()]
  assert exported_ids==[r['chunk_id'] for r in tasks]
  assert old[1]['chunk_id'] not in exported_ids
  vectors=np.vstack(matrices);assert np.array_equal(vectors[1],vectors[2])
  assert np.array_equal(vectors[0],np.load(out/'reused_old_vectors.npy')[0])
  prior=inp/'previous-run';shutil.copytree(destination,prior)
  interrupted=dict(done,parts=done['parts'][:1],parts_done=1,completed_rows=2,complete=False)
  (prior/'incremental_embed_manifest.json').write_text(json.dumps(interrupted))
  forwarded.clear()
  with patch.object(gpu_job,'make_embedder',fake_loader),patch.object(gpu_job,'PART_ROWS',2):
   gpu_job.run(gpu_spec,inp,root/'gpu-resumed')
  resumed=json.loads((root/'gpu-resumed/incremental_embed_manifest.json').read_text())
  assert resumed['complete'] and resumed['resumed_rows']==2
  assert len(forwarded)==28 and 'A completely new medical passage.' not in forwarded
  assert all(np.array_equal(np.load(root/'gpu-resumed'/p['vectors']['path']),matrices[i]) for i,p in enumerate(resumed['parts']))
  with (prior/done['parts'][0]['vectors']['path']).open('r+b') as handle:handle.write(b'X')
  forwarded.clear()
  with patch.object(gpu_job,'make_embedder',fake_loader),patch.object(gpu_job,'PART_ROWS',2):
   try:gpu_job.run(gpu_spec,inp,root/'gpu-corrupt')
   except AssertionError:pass
   else:raise AssertionError('Corrupt checkpoint was accepted')
  assert not forwarded
  print('PASS: cross-part copy, immutable old vectors, exact output IDs, resume skips completed inference, corrupt checkpoint rejected')

if __name__=='__main__':main()
