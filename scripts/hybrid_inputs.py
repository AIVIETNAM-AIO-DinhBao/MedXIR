"""CPU preflight for hybrid retrieval: part discovery, coverage and row alignment."""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

EMBEDDING_SPECS = {
    'qwen4b': dict(model='Qwen/Qwen3-Embedding-4B', dim=2560, doc_prefix='', directory='Qwen3-Embedding-4B'),
    'qwen8b': dict(model='Qwen/Qwen3-Embedding-8B', dim=4096, doc_prefix='', directory='Qwen3-Embedding-8B'),
    'octen8b': dict(model='Octen/Octen-Embedding-8B', dim=4096, doc_prefix=' ', directory='Octen-Embedding-8B'),
}


def embedding_weights(selected, fusion, weights):
    """Normalized model weights: zero-weight models need no attached assets."""
    if not fusion:
        assert selected in EMBEDDING_SPECS, f'Unknown EMBEDDING_MODEL: {selected}'
        return {selected: 1.0}
    assert set(weights) == set(EMBEDDING_SPECS), 'MODEL_WEIGHTS must specify qwen4b, qwen8b, octen8b'
    assert all(isinstance(w, (int, float)) and not isinstance(w, bool) and math.isfinite(w) and w >= 0
               for w in weights.values()), 'Model weights must be finite and nonnegative'
    total = sum(weights.values())
    assert math.isfinite(total) and total > 0, 'At least one model weight must be positive'
    return {k: weights[k] / total for k in EMBEDDING_SPECS if weights[k] > 0}


def group_embedding_vectors(paths, active):
    """Resolve model identity from its manifest, never from dimension or part filename."""
    import re
    by_repo = {EMBEDDING_SPECS[k]['model']: k for k in active}
    found = {k: [] for k in active}
    manifests = {}
    for raw in paths:
        path = Path(raw)
        if not re.fullmatch(r'(vi|en|zh)-part-\d+', path.stem):
            continue
        manifest_path = path.parent.parent / 'embed_manifest.json'
        if manifest_path not in manifests:
            manifests[manifest_path] = json.loads(manifest_path.read_text(encoding='utf-8'))
        key = by_repo.get(manifests[manifest_path].get('model'))
        if key is not None:
            found[key].append(str(path))
    for key in active:
        assert found[key], f'Missing corpus vectors for {key}; attach its three baseline sources and additions dataset'
    return found


def validate_query_cache(saved, vectors, key, variants, task, query_ids, legacy=False, revision=None):
    import numpy as np
    assert saved['variants'] == variants and saved['task'] == task, 'Cached query variants/instruction differ'
    if legacy:
        assert key == 'qwen4b', 'Unlabelled legacy query vectors are supported only for qwen4b'
        assert saved.get('model', EMBEDDING_SPECS[key]['model']) == EMBEDDING_SPECS[key]['model']
    else:
        assert saved['model'] == EMBEDDING_SPECS[key]['model'], 'Cached query model differs'
        assert saved['query_ids'] == list(query_ids), 'Cached query ID order differs'
        assert saved['query_format'] == 'Instruct: {task}\nQuery:{text}' and saved['max_length'] == 1024, 'Cached query encoding contract differs'
        assert revision is None or saved.get('model_revision') == revision, 'Cached model revision differs'
    assert vectors.shape == (len(variants), EMBEDDING_SPECS[key]['dim']) and np.isfinite(vectors).all(), 'Invalid cached query vectors'
    assert np.max(abs(np.linalg.norm(vectors.astype(np.float32), axis=1) - 1)) < .01, 'Cached queries must have unit norm'


def unique_parts(paths, chunk=False):
    result = {}
    for raw in paths:
        path = Path(raw)
        key = f'{path.parent.name}-{path.stem}' if chunk else path.stem
        assert key.split('-')[0] in {'vi', 'en', 'zh'}, f'Unknown language in part: {path}'
        assert key not in result, f'Duplicate part {key}: {result.get(key)} and {path}'
        result[key] = str(path)
    assert result, 'No chunk parts found' if chunk else 'No vector parts found'
    return result


def select_input_parts(chunk_files, vector_files, langs):
    langs = list(langs)
    assert langs and len(set(langs)) == len(langs) and set(langs) <= {'vi', 'en', 'zh'}, f'Invalid LANGS: {langs}'
    chunks = unique_parts(chunk_files, chunk=True)
    vectors = unique_parts(vector_files)
    chunks = {k: p for k, p in chunks.items() if k.split('-')[0] in langs}
    vectors = {k: p for k, p in vectors.items() if k.split('-')[0] in langs}
    assert {k.split('-')[0] for k in chunks} == set(langs), f'Missing chunk languages: {langs}'
    assert set(chunks) == set(vectors), (
        f'Chunk/vector parts differ: missing vectors={sorted(set(chunks)-set(vectors))}; '
        f'extra vectors={sorted(set(vectors)-set(chunks))}')
    return chunks, vectors


def json_fingerprint(path):
    path = Path(path)
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def validate_hybrid_inputs(chunk_files, vector_files, langs, manifest_path, expected_version='v2', progress=None, model_key='qwen4b'):
    import numpy as np
    import pyarrow.parquet as pq

    spec = EMBEDDING_SPECS[model_key]
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert not expected_version or manifest['dataset_version'] == expected_version, (
        f'Expected corpus {expected_version}, found {manifest.get("dataset_version")}')
    chunks, vectors = select_input_parts(chunk_files, vector_files, langs)
    schemas = {}; rows = Counter(); seen = set(); vector_manifests = {}
    for key in sorted(vectors):
        path = Path(vectors[key]); chunk_path = Path(chunks[key]); lang = key.split('-')[0]
        assert chunk_path.resolve().is_relative_to(manifest_path.parent.resolve()), f'Chunk part outside corpus root: {chunk_path}'
        embed_manifest_path = path.parent.parent / 'embed_manifest.json'
        if str(embed_manifest_path) not in vector_manifests:
            m = json.loads(embed_manifest_path.read_text(encoding='utf-8'))
            assert m['complete'] and m['model'] == spec['model'] and m['dim'] == spec['dim'], f'Incompatible vector manifest: {embed_manifest_path}'
            assert m.get('doc_prefix', '') == spec['doc_prefix'], f'Incompatible document prefix: {embed_manifest_path}'
            assert m['max_length'] == 1024 and m['dtype'] == 'torch.bfloat16', f'Incompatible embedding contract: {embed_manifest_path}'
            for field, expected in [('instruction', ''), ('pooling', 'last_token'), ('padding_side', 'left'), ('normalize', True)]:
                assert m.get(field, expected) == expected, f'Incompatible {field}: {embed_manifest_path}'
            vector_manifests[str(embed_manifest_path)] = dict(**json_fingerprint(embed_manifest_path), model=m['model'], dim=m['dim'],
                model_revision=m.get('model_revision'), output_dtype=m.get('output_dtype', 'float16'))
        array = np.load(path, mmap_mode='r', allow_pickle=False)
        assert array.ndim == 2 and array.dtype == np.float16 and array.shape[1] == spec['dim'], (key, array.dtype, array.shape)
        ids_path = path.with_name(f'{key}-ids.parquet')
        ids_table = pq.read_table(ids_path, columns=['chunk_id'])
        chunk_table = pq.read_table(chunk_path, columns=['chunk_id', 'lang'])
        assert ids_table['chunk_id'].null_count == chunk_table['chunk_id'].null_count == 0, f'Null ID in {key}'
        ids = ids_table['chunk_id'].to_numpy(); expected_ids = chunk_table['chunk_id'].to_numpy()
        assert array.shape[0] == len(ids) == len(expected_ids) and len(ids) > 0, f'Row count mismatch: {key}'
        assert np.array_equal(ids, expected_ids), f'Chunk ID order mismatch: {key}'
        assert set(chunk_table['lang'].to_pylist()) == {lang}, f'Wrong language rows: {key}'
        part_ids = set(ids.tolist())
        assert len(part_ids) == len(ids) and not seen.intersection(part_ids), f'Duplicate chunk IDs: {key}'
        seen.update(part_ids); rows[lang] += len(ids)
        schemas[key] = dict(chunk_path=str(chunk_path), vector_path=str(path), ids_path=str(ids_path), rows=len(ids), language=lang)
        del array, ids, expected_ids, ids_table, chunk_table, part_ids
        if progress: progress(f'preflight {key}: {schemas[key]["rows"]:,} aligned IDs')
    for lang in langs:
        assert rows[lang] == manifest['languages'][lang]['chunks'], f'Corpus manifest count mismatch for {lang}: {rows[lang]}'
    provenance = dict(model_key=model_key, model=spec['model'], dim=spec['dim'], corpus_version=manifest['dataset_version'], corpus_manifest=json_fingerprint(manifest_path),
        selected_languages=list(langs), chunk_count=sum(rows.values()), vector_count=sum(rows.values()),
        rows_by_language=dict(rows), part_count=len(schemas), parts=schemas,
        vector_manifests=list(vector_manifests.values()), all_part_ids_aligned=True)
    return chunks, vectors, provenance
