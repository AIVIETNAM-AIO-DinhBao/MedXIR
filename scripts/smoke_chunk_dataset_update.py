"""Tiny fixture: additive partitions, preserved identities and conservative alias mapping."""
from pathlib import Path
import tempfile,json,hashlib
import pyarrow as pa
import pyarrow.parquet as pq
from package_chunk_dataset_update import package,sha


def main():
    with tempfile.TemporaryDirectory(prefix='chunk-update-fixture-') as temp:
        root=Path(temp);base=root/'base';release=root/'release';base.mkdir();release.mkdir()
        groups=[];paths=[]
        def chunk(i,lang):
            h=hashlib.sha256(str(i).encode()).hexdigest()
            return dict(chunk_id=h[:16]+'-0',text_sha256=h,rep_doc_id=i,lang=lang,order=0,start=0,end=4,chunk_text='text',n_units=1,title='title',clean_version='clean-v1',chunk_params='350/50/40')
        def group(row):
            return dict(text_sha256=row['text_sha256'],rep_doc_id=row['rep_doc_id'],doc_ids=[row['rep_doc_id']],n_ids=1,group_type='single',fanout_safe=True)
        i=0
        for lang,count in [('en',1),('vi',7),('zh',1)]:
            folder=base/'chunks'/lang;folder.mkdir(parents=True)
            for part in range(count):
                i+=1;r=chunk(i,lang);groups.append(group(r));p=folder/f'part-{part:03d}.parquet';pq.write_table(pa.Table.from_pylist([r]),p);paths.append(p.relative_to(base).as_posix())
        pq.write_table(pa.Table.from_pylist(groups),base/'hash_groups.parquet')
        bm=dict(clean_version='clean-v1',chunk_params='350/50/40',id_scheme='{text_sha256[:16]}-{order}',excluded_ids=118,
            languages={l:dict(texts=n,doc_ids=n,chunks=n) for l,n in [('en',1),('vi',7),('zh',1)]})
        (base/'chunk_manifest.json').write_text(json.dumps(bm))
        new=[chunk(100+i,l) for i,l in enumerate(['vi','en','zh'])]
        pq.write_table(pa.Table.from_pylist(new),release/'chunk_corpus_additions_20261029_v2.parquet')
        pq.write_table(pa.Table.from_pylist([group(r) for r in new]),release/'hash_groups_additions_20261029_v2.parquet')
        pq.write_table(pa.Table.from_pylist([dict(doc_id=999,text_sha256=groups[0]['text_sha256'],decision='mapping_review_required')]),release/'aliases_to_existing_20261029_v2.parquet')
        pq.write_table(pa.Table.from_pylist([dict(doc_id=r['rep_doc_id'],lang=r['lang']) for r in new]+[dict(doc_id=999,lang='en')]),release/'documents_additions_20261029_v2.parquet')
        rm=dict(new_chunks=3,chunks_by_language={l:1 for l in ['vi','en','zh']},alias_document_ids=1,accepted_document_ids=4,
            files={p.name:dict(sha256=sha(p)) for p in release.glob('*.parquet')})
        (release/'release_manifest.json').write_text(json.dumps(rm))
        spec=dict(baseline_manifest=bm,release_manifest_sha256=sha(release/'release_manifest.json'),old_chunk_paths=sorted(paths),old_chunks=9)
        out=root/'out';result=package(base,release,out,spec)
        assert result['complete'] and result['total_chunks']==12 and result['chunk_parts']==12
        assert result['indexed_document_ids']==12 and result['pending_alias_document_ids']==1
        assert (out/'chunks/vi/part-007.parquet').exists() and (out/'chunks/en/part-001.parquet').exists() and (out/'chunks/zh/part-001.parquet').exists()
        assert all(sha(out/p)==sha(base/p) for p in paths)
        saved=json.loads((out/'chunk_manifest.json').read_text());assert sum(v['chunks'] for v in saved['languages'].values())==12
        assert sum(v['doc_ids'] for v in saved['languages'].values())==12
        print('PASS: old parts byte-identical; next language part numbers correct; IDs preserved; groups appended; pending alias not auto-fanned out')


if __name__=='__main__':main()
