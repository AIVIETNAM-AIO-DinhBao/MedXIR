"""Rebuild the chunk lists of the hybrid submission with neighbouring chunks merged into longer spans.

A reference passage counts as found when a submitted text of the same document covers >= 40% of it (token
LCS). Chunk recall (0.18) is far below document recall (0.47): the right documents are found, the passages
are not. Here the top-ranked chunks bring their neighbours (order - 1, order + 1) along, and contiguous
chunks of one text are merged into one span (the 50-word overlaps are dropped once). The byte budget per
query stays that of the hybrid submission, so lower-ranked chunks make room for the neighbours.

The document lists are kept as submitted; documents of new spans are appended (as in assemble()).
Optional per variant: n_docs extends the document list with the next pool documents (best chunk first);
quota reserves that share of the chunk bytes for ZH/EN texts; doc_quota swaps the lowest VI documents of the
list for the best ZH/EN pool documents (both lost on the leaderboard, 29/10); doc_rr re-orders the
candidate documents by weighted RRF of their submitted rank and document-reranker scores (doc_rank.parquet,
written by the doc-rerank notebook, kaggle_doc_rerank.ipynb), so the reranker decides which make the cut.
conf_gamma moves chunk bytes between queries, total unchanged: a query's confidence is its number of pool
chunks with rerank score >= conf_t; confident queries lose budget (their lowest-ranked chunks go) and the
budget goes to queries with few strong chunks, which reach deeper into their pool.
Runs on a Kaggle CPU session (Internet OFF is fine). Inputs:
  medxir-chunk-corpus      chunks/*/part-*.parquet, hash_groups.parquet
  medxir-hybrid-retrieval  pool.parquet, medxir-hybrid-submission.zip   (notebook output or uploaded dataset)
  doc_rank.parquet         only for doc_rr variants (in OUT_DIR or an input)
Output: one ZIP per variant in /kaggle/working (or OUT_DIR), plus expand_manifest.json.
"""
import glob
import json
import math
import os
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

# nb: number of top chunks whose neighbours are added (0 = only merge, no new neighbours);
#     'whole' = probe: full texts of the top documents instead of ranked chunks.
# n_docs: document list length (0 = as submitted). quota / doc_quota: min ZH/EN share of chunk bytes / documents.
# doc_rr: {score column of doc_rank.parquet: RRF weight}; w_base: RRF weight of the submitted document rank;
# keep_top: that many top candidates kept as submitted, the rest of the list re-ordered.
# conf_gamma: budget weight (confidence / median confidence) ** -gamma, clipped to CONF_CLIP, renormalised.
# min_chars: spans shorter than this are not submitted. A span matches when it covers 40% of a reference
# passage, so a 5-character ZH entry ('食管炎') can only match an equally short passage; such spans were 11% of
# the entries of the 0.2464 ZIP (< 200 chars) for 0.35% of its text.
# min_score: ranked chunks with a rerank score below it are not taken (nor their neighbours); the bytes a query
# leaves unused go to the queries that used their whole budget, by the same confidence weights. bge scores are
# high: at 0.2 the cut removes ~46 chunks in 16% of the queries (the unsure ones) and none elsewhere.
VARIANTS = {
    'nb0': dict(nb=0), 'nb10': dict(nb=10), 'nb30': dict(nb=30), 'nb60': dict(nb=60), 'wholedoc': dict(nb='whole'),
    'nb60-d300': dict(nb=60, n_docs=300),
    'nb60-q20': dict(nb=60, quota=0.2),
    'nb60-q20-dq20': dict(nb=60, quota=0.2, doc_quota=0.2),
    'nb60-drr-bge': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=1.0),
    'nb60-drr-bge-only': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0),
    # Qwen3-Reranker-4B scores only the zone around the cut (ranks 120-360): the top 120 stay as submitted.
    'nb60-drr-qwen4b': dict(nb=60, doc_rr={'Qwen3-Reranker-4B': 1.0}, w_base=1.0, keep_top=120),
    'nb60-drr-qwen4b-only': dict(nb=60, doc_rr={'Qwen3-Reranker-4B': 1.0}, w_base=0.0, keep_top=120),
    'nb60-drr-both': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0, 'Qwen3-Reranker-4B': 1.0}, w_base=1.0,
                          keep_top=120),
    # Bge-only document list, chunk bytes moved between queries. 30/10 on v2 (bge-only 0.2464): glow (to unsure
    # queries) 0.2352, ghigh (to confident queries) 0.2477; macro F2 rewards the queries that already score.
    'nb60-drr-bge-only-glow': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0, conf_gamma=0.5, conf_t=0.5),
    'nb60-drr-bge-only-ghigh': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0, conf_gamma=-0.5, conf_t=0.5),
    'nb60-drr-bge-only-ghigh1': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0, conf_gamma=-1.0,
                                     conf_t=0.5),
    # Precision: no tiny spans; 'nball' gives every ranked chunk its neighbours (no single chunks filling the
    # budget), the entry shape of the x1-c70 submission (Chunk P 0.208 vs 0.155).
    'nb60-drr-bge-only-ghigh1-min200': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0, conf_gamma=-1.0,
                                            conf_t=0.5, min_chars=200),
    'nb60-drr-bge-only-ghigh-min200-s20': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0,
                                               conf_gamma=-0.5, conf_t=0.5, min_chars=200, min_score=0.2),
    'nb60-drr-bge-only-ghigh1-min200-s20': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0,
                                                conf_gamma=-1.0, conf_t=0.5, min_chars=200, min_score=0.2),
    'nb60-drr-bge-only-ghigh-min200': dict(nb=60, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0, conf_gamma=-0.5,
                                           conf_t=0.5, min_chars=200),
    'nball-drr-bge-only-ghigh-min200': dict(nb=10 ** 6, doc_rr={'bge-reranker-v2-m3': 1.0}, w_base=0.0,
                                            conf_gamma=-0.5, conf_t=0.5, min_chars=200),
}
ONLY = ['nb60-d300']   # variants to build this run; [] = all
BUDGET_SCALE = 1.08   # 87.5 MB hybrid ZIP * 1.08 stays below the 100 MB limit
MAX_ZIP_MB = 99.0
CONF_CLIP = (0.5, 2.0)
RRF_K = 60
INPUT = os.environ.get('INPUT_ROOT', '/kaggle/input')
OUT = Path(os.environ.get('OUT_DIR', '/kaggle/working'))


def one_match(pattern):
    hits = sorted(p for p in glob.glob(f'{INPUT}/**/{pattern}', recursive=True))
    if len(hits) != 1:
        for root in sorted(glob.glob(f'{INPUT}/*/*/*')) or sorted(glob.glob(f'{INPUT}/*')):
            print('input:', root, sorted(os.listdir(root))[:8] if os.path.isdir(root) else '')
    assert len(hits) == 1, f'{pattern}: expected one match, found {hits[:10]}'
    return Path(hits[0])


def load_submission():
    # An uploaded dataset is unzipped by Kaggle: accept the JSON too.
    hits = sorted(glob.glob(f'{INPUT}/**/medxir-hybrid-submission.json', recursive=True))
    if hits:
        return json.loads(Path(hits[0]).read_text(encoding='utf-8'))
    with zipfile.ZipFile(one_match('medxir-hybrid-submission.zip')) as archive:
        return json.loads(archive.read(archive.namelist()[0]))


def merge_run(rows):
    """One text out of chunks of one document text with consecutive orders (rows sorted by order)."""
    text, end = rows[0]['text'], rows[0]['end']
    for row in rows[1:]:
        text += row['text'][max(0, end - row['start']):]
        end = row['end']
    return text


def build_query(ranked, n_neighbour, budget, rows, by_pos, docs_of, quota=0.0, scores=None, min_score=0.0):
    """Selected chunk ids for one query: ranked chunks (+ neighbours of the top ones) until the budget is used.

    quota > 0: a first pass caps VI chunks at (1 - quota) of the budget so ZH/EN chunks further down the pool
    get the rest; a second pass fills what ZH/EN could not use.
    """
    chosen, used, vi_used = [], 0, 0
    for vi_cap in ([budget * (1 - quota)] if quota else []) + [budget]:
        for rank, cid in enumerate(ranked):
            if min_score and scores[rank] < min_score:
                break   # pool is in score order
            group = [cid]
            if rank < n_neighbour:
                sha, order = rows[cid]['sha'], rows[cid]['order']
                group += [by_pos[(sha, o)] for o in (order - 1, order + 1) if (sha, o) in by_pos]
            for c in group:
                if c in chosen:
                    continue
                cost = len(rows[c]['text'].encode('utf-8')) * len(docs_of(c))
                vi = rows[c].get('lang', 'vi') == 'vi'
                if used + cost > budget or (vi and vi_used + cost > vi_cap):
                    continue  # a long or wide-fanout chunk does not fit; smaller ones further down may
                chosen.append(c)
                used += cost
                vi_used += cost if vi else 0
    return chosen


def doc_list(docs, ranked, docs_of, lang_of, n_docs=0, doc_quota=0.0):
    """Submitted documents with the ZH/EN quota applied, then extended to n_docs with pool documents."""
    docs = list(docs)
    pool_docs = list(dict.fromkeys(d for cid in ranked for d in docs_of(cid)))
    if doc_quota:
        listed = set(docs)
        extra = [d for d in pool_docs if d not in listed and lang_of.get(d, 'vi') != 'vi']
        need = math.ceil(doc_quota * len(docs)) - sum(lang_of.get(d, 'vi') != 'vi' for d in docs)
        vi_slots = [i for i, d in enumerate(docs) if lang_of.get(d, 'vi') == 'vi']
        slots = vi_slots[len(vi_slots) - min(max(need, 0), len(extra), len(vi_slots)):]
        for i, doc in zip(slots, extra):   # lowest VI slots, best ZH/EN document first
            docs[i] = doc
    if n_docs > len(docs):
        listed = set(docs)
        docs += [d for d in pool_docs if d not in listed][:n_docs - len(docs)]
    return docs


def best_chunks(ranked, docs_of):
    """{doc: its pool chunks, best first}. Documents filled from beyond the reranked pool have none."""
    best = {}
    for cid in ranked:
        for doc in docs_of(cid):
            best.setdefault(doc, []).append(cid)
    return best


def doc_text(best, rows, by_text, lead_chars=400, n_chunks=1):
    """Text a document reranker sees: the start of the document text (title / lead) + its best pool chunks.

    best: the document's pool chunks, best first; the n_chunks best are shown in text order.
    """
    shown = sorted(best[:n_chunks], key=lambda c: rows[c]['order'])
    parts = [rows[c]['text'] for c in shown]
    lead = rows[by_text[rows[shown[0]]['sha']][0]]
    if lead['order'] != rows[shown[0]]['order']:
        parts.insert(0, lead['text'][:lead_chars])
    return '\n...\n'.join(parts)


def fuse_docs(candidates, scores, weights, w_base=1.0, k=RRF_K, offset=0):
    """Candidates (submitted order) re-ordered by weighted RRF of their position and reranker scores.

    scores: {column: {doc: score}}; a document without a score in a column gets no term from it.
    offset: submitted rank of the first candidate (when the documents above it are kept fixed).
    """
    fused = {doc: w_base / (k + offset + rank) for rank, doc in enumerate(candidates)}
    for column, weight in weights.items():
        scored = sorted((d for d in candidates if d in scores[column]), key=lambda d: -scores[column][d])
        for rank, doc in enumerate(scored):
            fused[doc] += weight / (k + rank)
    return sorted(candidates, key=lambda d: -fused[d])


def spans(chosen, rows):
    """Merge chosen chunks of the same text with consecutive orders. Returns [(first chunk id, text)] in rank order."""
    by_sha = {}
    for cid in chosen:
        by_sha.setdefault(rows[cid]['sha'], []).append(cid)
    first_rank = {cid: i for i, cid in enumerate(chosen)}
    out = []
    for cids in by_sha.values():
        cids = sorted(cids, key=lambda c: rows[c]['order'])
        run = [cids[0]]
        for c in cids[1:]:
            if rows[c]['order'] == rows[run[-1]]['order'] + 1:
                run.append(c)
            else:
                out.append(run)
                run = [c]
        out.append(run)
    out.sort(key=lambda run: min(first_rank[c] for c in run))
    return [(run[0], merge_run([rows[c] for c in run])) for run in out]


def build_whole_docs(ranked, docs, budget, rows, by_text, docs_of):
    """Probe: every chunk of the submitted documents, best document first, until the budget is used.

    Tells whether chunk recall is lost inside the right documents (recall jumps) or by missing documents.
    """
    text_of = {}
    for cid in ranked:
        for doc in docs_of(cid):
            text_of.setdefault(doc, rows[cid]['sha'])
    chosen, used = [], 0
    for doc in docs:
        if doc not in text_of:
            continue
        for c in by_text[text_of[doc]]:
            cost = len(rows[c]['text'].encode('utf-8')) * len(docs_of(c))
            if used + cost > budget:
                return chosen
            if c not in chosen:
                chosen.append(c)
                used += cost
    return chosen


def load_inputs(pool_path=None, base=None, chunk_files=None):
    """Pool ranking, base submission, every chunk of the candidate texts and the document groups.

    The hybrid notebook passes its own pool.parquet, in-memory submission and validated chunk parts;
    otherwise they are found under INPUT.
    """
    pool = pd.read_parquet(pool_path or one_match('pool.parquet'),
                           columns=['query_id', 'chunk_id', 'final_rank', 'final_score'])
    pool = pool.sort_values(['query_id', 'final_rank'])
    ranked = {qid: g['chunk_id'].tolist() for qid, g in pool.groupby('query_id', sort=False)}
    score = {qid: g['final_score'].tolist() for qid, g in pool.groupby('query_id', sort=False)}
    base = load_submission() if base is None else base
    needed_sha_prefix = {cid.rsplit('-', 1)[0] for ids in ranked.values() for cid in ids}

    # Every chunk of the candidate texts: the neighbours are rows of the same text_sha256.
    rows, by_pos = {}, {}
    chunk_files = chunk_files or sorted(glob.glob(f'{INPUT}/**/chunks/*/part-*.parquet', recursive=True))
    assert chunk_files, 'no chunk parts: attach medxir-chunk-corpus'
    columns = ['chunk_id', 'text_sha256', 'rep_doc_id', 'order', 'start', 'end', 'chunk_text']
    for path in chunk_files:
        table = pq.read_table(path, columns=columns)
        prefix = pc.utf8_slice_codeunits(table['text_sha256'], 0, 16)
        table = table.filter(pc.is_in(prefix, value_set=pa.array(sorted(needed_sha_prefix))))
        lang = Path(path).parent.name   # chunks/<lang>/part-*.parquet
        for cid, sha, doc, order, start, end, text in zip(*(table[c].to_pylist() for c in columns)):
            rows[cid] = dict(sha=sha, doc=int(doc), order=int(order), start=int(start), end=int(end), text=text,
                             lang=lang)
            by_pos[(sha, int(order))] = cid
        print(Path(path).parent.name, Path(path).name, f'{len(rows):,} rows kept', flush=True)
    missing = {cid for ids in ranked.values() for cid in ids} - set(rows)
    assert not missing, f'{len(missing)} pool chunks not in the chunk corpus (chunk id != sha prefix?), e.g. {sorted(missing)[:3]}'

    groups = pd.read_parquet(one_match('hash_groups.parquet'), columns=['text_sha256', 'doc_ids', 'fanout_safe'])
    group = {sha: [int(d) for d in ids] for sha, ids, safe in
             zip(groups['text_sha256'], groups['doc_ids'], groups['fanout_safe']) if bool(safe)}
    del groups

    def docs_of(cid):
        return group.get(rows[cid]['sha']) or [rows[cid]['doc']]

    by_text = {}
    for cid in sorted(rows, key=lambda c: rows[c]['order']):
        by_text.setdefault(rows[cid]['sha'], []).append(cid)
    return dict(ranked=ranked, score=score, base=base, rows=rows, by_pos=by_pos, by_text=by_text, docs_of=docs_of,
                lang_of={doc: row['lang'] for cid, row in rows.items() for doc in docs_of(cid)})


def load_doc_scores():
    """{query_id: {column: {doc: score}}} from doc_rank.parquet (OUT_DIR first, then the inputs)."""
    path = OUT / 'doc_rank.parquet'
    frame = pd.read_parquet(path if path.exists() else one_match('doc_rank.parquet'))
    columns = [c for c in frame.columns if c not in ('query_id', 'doc_id', 'base_rank')]
    out = {}
    for qid, g in frame.groupby('query_id'):
        out[int(qid)] = {c: {int(d): float(s) for d, s in zip(g['doc_id'], g[c]) if s == s} for c in columns}
    return out


def confidence_budget(budget, score, gamma, threshold):
    """Per-query budgets re-weighted by (confidence / median) ** -gamma, the total kept."""
    conf = {qid: sum(s >= threshold for s in score.get(qid, [])) + 1 for qid in budget}
    median = sorted(conf.values())[len(conf) // 2]
    weight = {qid: min(max((c / median) ** -gamma, CONF_CLIP[0]), CONF_CLIP[1]) for qid, c in conf.items()}
    norm = sum(budget.values()) / sum(budget[q] * weight[q] for q in budget)
    return {qid: budget[qid] * weight[qid] * norm for qid in budget}


def chosen_bytes(chosen, rows, docs_of):
    return sum(len(rows[c]['text'].encode('utf-8')) * len(docs_of(c)) for c in chosen)


def score_cut_budget(budget, ctx, n_neighbour, min_score):
    """Budgets after the score cut: what cut queries leave unused goes to the queries the cut did not stop,
    in proportion to their budgets."""
    used, full = {}, []
    for qid, b in budget.items():
        args = (ctx['ranked'][qid], n_neighbour, b, ctx['rows'], ctx['by_pos'], ctx['docs_of'])
        cut = chosen_bytes(build_query(*args, scores=ctx['score'][qid], min_score=min_score), ctx['rows'], ctx['docs_of'])
        if cut < chosen_bytes(build_query(*args), ctx['rows'], ctx['docs_of']):
            used[qid] = cut
        else:
            full.append(qid)
    freed = sum(budget[q] - used[q] for q in used)
    grow = 1 + freed / max(sum(budget[q] for q in full), 1)
    print(f'score cut {min_score}: {len(used)} queries cut, {freed / 1e6:.1f} MB moved to {len(full)} queries', flush=True)
    return {qid: used[qid] if qid in used else budget[qid] * grow for qid in budget}


def build_variants(ctx, only=None, doc_scores=None):
    ranked, base, rows, by_pos, by_text, docs_of, lang_of = (
        ctx[k] for k in ('ranked', 'base', 'rows', 'by_pos', 'by_text', 'docs_of', 'lang_of'))
    budget = {e['id']: BUDGET_SCALE * sum(len(c['chunk_text'].encode('utf-8')) for c in e['relevant_chunks'])
              for e in base}
    only = ONLY if only is None else only
    manifest = {'budget_scale': BUDGET_SCALE, 'variants': {}}
    for name, spec in VARIANTS.items():
        if only and name not in only:
            continue
        if spec.get('doc_rr'):
            doc_scores = doc_scores or load_doc_scores()
            some = next(iter(doc_scores.values()))
            absent = [c for c in spec['doc_rr'] if c not in some]
            if absent:
                print(name, 'skipped: no doc_rank column', absent, flush=True)
                continue
        n_neighbour = spec['nb']
        q_budget = (confidence_budget(budget, ctx['score'], spec['conf_gamma'], spec.get('conf_t', 0.5))
                    if spec.get('conf_gamma') else budget)
        if spec.get('min_score'):
            q_budget = score_cut_budget(q_budget, ctx, n_neighbour, spec['min_score'])
        scale = 1.0
        while True:
            submission, n_spans, n_chunks, n_docs, other_docs, other_bytes, all_bytes, moved = [], 0, 0, 0, 0, 0, 0, 0
            dropped = 0
            for entry in base:
                qid = entry['id']
                if n_neighbour == 'whole':
                    chosen = build_whole_docs(ranked[qid], entry['relevant_docs'], q_budget[qid] * scale,
                                              rows, by_text, docs_of)
                else:
                    chosen = build_query(ranked[qid], n_neighbour, q_budget[qid] * scale, rows, by_pos, docs_of,
                                         spec.get('quota', 0.0), ctx['score'][qid], spec.get('min_score', 0.0))
                if spec.get('doc_rr'):
                    q_scores = doc_scores.get(qid, {})
                    candidates = list(dict.fromkeys(
                        doc_list(entry['relevant_docs'], ranked[qid], docs_of, lang_of, n_docs=10 ** 6)
                        + [d for c in spec['doc_rr'] for d in q_scores.get(c, {})]))
                    keep = spec.get('n_docs') or len(entry['relevant_docs'])
                    fixed = candidates[:spec.get('keep_top', 0)]
                    docs = fixed + fuse_docs(candidates[len(fixed):], q_scores, spec['doc_rr'], spec.get('w_base', 1.0),
                                             offset=len(fixed))[:keep - len(fixed)]
                    moved += len(set(docs) - set(entry['relevant_docs']))
                else:
                    docs = doc_list(entry['relevant_docs'], ranked[qid], docs_of, lang_of,
                                    spec.get('n_docs', 0), spec.get('doc_quota', 0.0))
                for c in chosen:
                    size = len(rows[c]['text'].encode('utf-8'))
                    all_bytes += size
                    other_bytes += size if rows[c]['lang'] != 'vi' else 0
                listed = set(docs)
                chunks = []
                for cid, text in spans(chosen, rows):
                    if len(text) < spec.get('min_chars', 0):
                        dropped += 1
                        continue
                    for doc in docs_of(cid):
                        chunks.append(dict(doc_id=doc, chunk_text=text, chunk_order=rows[cid]['order']))
                        if doc not in listed:
                            listed.add(doc)
                            docs.append(doc)
                n_spans += len(chunks)
                n_docs += len(docs)
                other_docs += sum(lang_of.get(d, 'vi') != 'vi' for d in docs) / max(len(docs), 1)
                n_chunks += len(chosen)
                submission.append(dict(id=qid, relevant_docs=docs, relevant_chunks=chunks))
            zip_path = OUT / f'medxir-expand-{name}-submission.zip'
            json_name = zip_path.name.replace('.zip', '.json')
            with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                archive.writestr(json_name, json.dumps(submission, ensure_ascii=False, separators=(',', ':')))
            mb = zip_path.stat().st_size / 1e6
            if mb <= MAX_ZIP_MB:
                break
            scale *= 0.95 * MAX_ZIP_MB / mb
        stats = dict(spec, zip_mb=round(mb, 1), budget_scale_applied=round(scale, 3),
                     chunks_per_query=round(n_chunks / len(base), 1), entries_per_query=round(n_spans / len(base), 1),
                     docs_per_query=round(n_docs / len(base), 1), short_dropped_per_query=round(dropped / len(base), 1),
                     zh_en_doc_share=round(other_docs / len(base), 3),
                     zh_en_chunk_byte_share=round(other_bytes / max(all_bytes, 1), 3))
        if spec.get('doc_rr'):
            stats['new_docs_per_query'] = round(moved / len(base), 1)   # documents not in the submitted list
        manifest['variants'][name] = stats
        print(name, stats, flush=True)
    (OUT / 'expand_manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest


def main():
    build_variants(load_inputs())


if __name__ == '__main__':
    main()
