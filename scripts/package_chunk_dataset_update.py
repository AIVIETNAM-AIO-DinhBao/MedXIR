"""Kaggle CPU: preserve baseline parts and append verified language partitions."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import hashlib,json,shutil,zipfile
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


def sha(path):
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def package(baseline,release,out,spec):
    baseline,release,out=map(Path,(baseline,release,out));out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((baseline/'chunk_manifest.json').read_text())
    assert manifest==spec['baseline_manifest']
    assert sha(release/'release_manifest.json')==spec['release_manifest_sha256']
    rm=json.loads((release/'release_manifest.json').read_text())
    for name in ['chunk_corpus_additions_20261029_v2.parquet','hash_groups_additions_20261029_v2.parquet','aliases_to_existing_20261029_v2.parquet','documents_additions_20261029_v2.parquet']:
        assert sha(release/name)==rm['files'][name]['sha256']
    parts=sorted(baseline.glob('chunks/*/part-*.parquet'))
    assert [p.relative_to(baseline).as_posix() for p in parts]==spec['old_chunk_paths']
    ids=set();base_counts=Counter();base_schemas={};old_files={}
    for path in parts:
        rel=path.relative_to(baseline);target=out/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);digest=sha(path);assert sha(target)==digest
        old_files[rel.as_posix()]=dict(sha256=digest,bytes=path.stat().st_size)
        lang=path.parent.name;base_schemas[lang]=pq.read_schema(path)
        for batch in pq.ParquetFile(path).iter_batches(columns=['chunk_id','lang']):
            current=batch.column(0).to_pylist();assert len(set(current))==len(current) and not ids.intersection(current)
            assert set(batch.column(1).to_pylist())=={lang}
            ids.update(current);base_counts[lang]+=len(current)
    assert sum(base_counts.values())==spec['old_chunks']
    additions=pq.read_table(release/'chunk_corpus_additions_20261029_v2.parquet')
    added_ids=additions['chunk_id'].to_pylist()
    assert len(set(added_ids))==len(added_ids)==rm['new_chunks'] and not ids.intersection(added_ids)
    new_counts={};new_paths={}
    for lang in ['vi','en','zh']:
        current=additions.filter(pc.equal(additions['lang'],lang))
        assert len(current)==rm['chunks_by_language'][lang]
        previous=[int(p.stem.split('-')[-1]) for p in parts if p.parent.name==lang]
        rel=f'chunks/{lang}/part-{max(previous)+1:03d}.parquet';target=out/rel
        assert not target.exists()
        current=current.select(base_schemas[lang].names).cast(base_schemas[lang].remove_metadata())
        pq.write_table(current,target,compression='zstd')
        saved=pq.read_table(target)
        assert saved.equals(current) and saved['chunk_id'].to_pylist()==current['chunk_id'].to_pylist()
        new_counts[lang]=len(current);new_paths[lang]=rel
        print(f'Added {rel}: {len(current):,} rows',flush=True)
    old_groups=pq.read_table(baseline/'hash_groups.parquet')
    new_groups=pq.read_table(release/'hash_groups_additions_20261029_v2.parquet')
    old_hashes=set(old_groups['text_sha256'].to_pylist());new_hashes=set(new_groups['text_sha256'].to_pylist())
    assert len(old_hashes)==len(old_groups) and len(new_hashes)==len(new_groups) and not old_hashes.intersection(new_hashes)
    new_groups=new_groups.select(old_groups.schema.names).cast(old_groups.schema.remove_metadata())
    old_plain=old_groups.replace_schema_metadata(None)
    combined=pa.concat_tables([old_plain,new_groups]);pq.write_table(combined,out/'hash_groups.parquet',compression='zstd')
    assert pq.read_table(out/'hash_groups.parquet').slice(0,len(old_groups)).equals(old_plain)
    old_doc_ids=set();new_doc_ids=set()
    for group in old_groups.select(['doc_ids','n_ids']).to_pylist():
        members=group['doc_ids'];assert len(set(members))==len(members)==group['n_ids'] and not old_doc_ids.intersection(members)
        old_doc_ids.update(members)
    for group in new_groups.select(['doc_ids','n_ids']).to_pylist():
        members=group['doc_ids'];assert len(set(members))==len(members)==group['n_ids'] and not new_doc_ids.intersection(members) and not old_doc_ids.intersection(members)
        new_doc_ids.update(members)
    doc_lang={r['doc_id']:r['lang'] for r in pq.read_table(release/'documents_additions_20261029_v2.parquet',columns=['doc_id','lang']).to_pylist()}
    added_doc_counts=Counter(doc_lang[i] for i in new_doc_ids)
    added_text_counts=Counter(doc_lang[i] for i in new_groups['rep_doc_id'].to_pylist())
    pending=pq.read_table(release/'aliases_to_existing_20261029_v2.parquet')
    assert len(pending)==rm['alias_document_ids'] and len(new_doc_ids)+len(pending)==rm['accepted_document_ids']
    (out/'metadata').mkdir();(out/'provenance').mkdir()
    shutil.copyfile(release/'aliases_to_existing_20261029_v2.parquet',out/'metadata/aliases_pending_review.parquet')
    shutil.copyfile(baseline/'chunk_manifest.json',out/'provenance/chunk_manifest_v1.json')
    shutil.copyfile(release/'release_manifest.json',out/'provenance/additions_release_manifest.json')
    result=dict(dataset_version='v2',dataset_built_at=datetime.now(timezone.utc).isoformat(),
        clean_version=manifest['clean_version'],chunk_params=manifest['chunk_params'],id_scheme=manifest['id_scheme'],
        excluded_ids=manifest['excluded_ids'],hash_groups=len(combined),group_types=dict(Counter(combined['group_type'].to_pylist())),
        languages={lang:dict(texts=manifest['languages'][lang]['texts']+added_text_counts[lang],
            doc_ids=manifest['languages'][lang]['doc_ids']+added_doc_counts[lang],chunks=base_counts[lang]+new_counts[lang]) for lang in ['vi','en','zh']},
        total_chunks=len(ids)+len(added_ids),chunk_parts=len(parts)+3,baseline_chunk_parts_unchanged=True,
        additions=dict(document_ids=rm['accepted_document_ids'],indexed_document_ids=len(new_doc_ids),
            pending_alias_document_ids=len(pending),new_chunks=len(added_ids),parts=new_paths,
            release_manifest_sha256=spec['release_manifest_sha256'],source_kernel='ntklinhfitus/medxir-recovery-finalize-20261029-v2'),
        baseline_chunk_files=old_files,embedding_changes=False)
    (out/'chunk_manifest.json').write_text(json.dumps(result,indent=2))
    report=dict(complete=True,total_chunks=result['total_chunks'],new_chunks=len(added_ids),chunk_parts=result['chunk_parts'],
        old_chunk_files_unchanged=True,old_group_rows_unchanged=True,chunk_ids_unique=True,chunk_ids_preserved=True,
        old_chunks=len(ids),languages=result['languages'],pending_alias_document_ids=len(pending),
        indexed_document_ids=len(old_doc_ids)+len(new_doc_ids),new_indexed_document_ids=len(new_doc_ids),
        files={p.relative_to(out).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(out.rglob('*')) if p.is_file()})
    return report


def main():
    spec=json.loads(Path('/kaggle/working/chunk_update_spec.json').read_text())
    base=list(Path('/kaggle/input').rglob('chunk_manifest.json'));releases=list(Path('/kaggle/input').rglob('release_manifest.json'))
    assert len(base)==len(releases)==1
    payload=Path('/kaggle/working/dataset_payload')
    report=package(base[0].parent,releases[0].parent,payload,spec)
    archive=Path('/kaggle/working/dataset_payload.zip')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
        for p in sorted(payload.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(payload).as_posix())
    report['archive']=dict(bytes=archive.stat().st_size,sha256=sha(archive))
    Path('/kaggle/working/chunk_dataset_update_manifest.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))


if __name__=='__main__':main()
