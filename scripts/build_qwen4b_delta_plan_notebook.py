"""Build the CPU inventory job after the canonical additions have been fetched."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 release=ROOT/'runs/smoke/recovery-finalize-20261029-v2-cpu/artifacts/recovery_release_20261029_v2/release_manifest.json'
 m=json.loads(release.read_text(encoding='utf-8'))
 baseline=ROOT/'artifacts/embedding_inventory_20261030/baseline'
 manifest_paths=[baseline/f's{i}/embed_manifest.json' for i in range(3)]
 model_path=ROOT/'artifacts/embedding_inventory_20261030/model-package/models/MODELS.json'
 models=json.loads(model_path.read_text(encoding='utf-8'))
 spec=dict(release_manifest_sha256=sha(release),baseline_chunk_manifest=m['baseline_manifest'],
  baseline_embed_manifest_sha256=[sha(p) for p in manifest_paths],
  expected_old_chunks=sum(json.loads(p.read_text())['chunks'] for p in manifest_paths),
  model_revision=models['models']['Qwen/Qwen3-Embedding-4B']['sha'],model_package_manifest_sha256=sha(model_path))
 assert spec['expected_old_chunks']==2621553
 source=(ROOT/'scripts/prepare_qwen4b_delta.py').read_text(encoding='utf-8')
 code='SPEC='+repr(spec)+'\nSOURCE='+repr(source)+'''\nfrom pathlib import Path
import json,subprocess,sys
p=Path('/kaggle/working/prepare_qwen4b_delta.py');p.write_text(SOURCE,encoding='utf-8')
Path('/kaggle/working/embedding_spec.json').write_text(json.dumps(SPEC))
subprocess.run([sys.executable,'-u',str(p)],check=True)
'''
 cell=dict(cell_type='code',id='verify-inventory',metadata={},execution_count=None,outputs=[],source=code.splitlines(True))
 nb=dict(cells=[cell],metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3')),nbformat=4,nbformat_minor=5)
 p=ROOT/'src/notebooks/kaggle_qwen4b_delta_plan.ipynb';p.write_text(json.dumps(nb,ensure_ascii=False,indent=1),encoding='utf-8')
 config='''kernel:
  id: ntklinhfitus/medxir-qwen4b-delta-plan-20261030
  title: medxir-qwen4b-delta-plan-20261030
  code_file: src/notebooks/kaggle_qwen4b_delta_plan.ipynb
  kernel_type: notebook
  enable_gpu: false
  enable_internet: false
  dataset_sources:
    - ntklinhfitus/medxir-chunk-corpus
    - boinhbo/medxir-vectors-qwen3-4b-s0
    - boinhbo/medxir-vectors-qwen3-4b-s1
    - boinhbo/medxir-vectors-qwen3-4b-s2
  kernel_sources: [ntklinhfitus/medxir-recovery-finalize-20261029-v2]
  kernel_source_expectations:
    - kernel: ntklinhfitus/medxir-recovery-finalize-20261029-v2
      version: 1
      manifest: recovery_release_20261029_v2/release_manifest.json
      manifest_sha256: '''+spec['release_manifest_sha256']+'''
  timeout_seconds: 3600
  poll_interval_seconds: 30
  fetch_files: [qwen4b_additions_plan/embedding_plan_manifest.json, qwen4b_additions_plan/reuse_provenance.parquet, qwen4b_additions_plan/golden_texts.parquet]
  required_outputs: [qwen4b_additions_plan/embedding_plan_manifest.json]
  metrics_file: qwen4b_additions_plan/embedding_plan_manifest.json
'''
 (ROOT/'configs/kaggle/qwen4b_delta_plan_20261030_cpu.yaml').write_text(config,encoding='utf-8')
 print(p)
if __name__=='__main__':main()
