"""Build exact, per-part FAISS IndexFlatIP indexes for completed embedding datasets.

Each index part preserves the row order of its paired embedding-ID Parquet file. The
sidecar row_map.parquet makes FAISS's implicit sequential row IDs explicit and joins
them to the canonical chunk metadata by chunk_id.

Example:
    uv run --extra retrieval python scripts/build_faiss_flat_index.py \
      --vector-root artifacts/kaggle/vectors-qwen3-4b-s0 \
      --vector-root artifacts/kaggle/vectors-qwen3-4b-s1 \
      --vector-root artifacts/kaggle/vectors-qwen3-4b-s2 \
      --chunks-root artifacts/kaggle/chunk-corpus-v1 \
      --hash-groups artifacts/kaggle/chunk-corpus-v1/hash_groups.parquet \
      --output artifacts/faiss/qwen3-embedding-4b
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import faiss
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


PART_RE = re.compile(r"(?P<lang>[a-z]{2})-part-(?P<number>\d{3})\Z")
ROW_MAP_SCHEMA = pa.schema([
    ("faiss_row", pa.int64()),
    ("chunk_id", pa.string()),
    ("text_sha256", pa.string()),
    ("rep_doc_id", pa.int64()),
    ("lang", pa.string()),
    ("chunk_order", pa.int32()),
    ("source_part", pa.string()),
])


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def discover_parts(vector_roots: list[Path]) -> tuple[str, int, list[dict[str, Any]]]:
    parts: dict[str, dict[str, Any]] = {}
    model_name: str | None = None
    dimension: int | None = None
    for root in vector_roots:
        manifest_path = root / "embed_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not manifest.get("complete"):
            raise ValueError(f"embedding manifest is incomplete: {manifest_path}")
        if manifest.get("parts_done") != manifest.get("parts_total"):
            raise ValueError(f"manifest part counts disagree: {manifest_path}")
        if model_name is None:
            model_name, dimension = manifest["model"], int(manifest["dim"])
        elif manifest["model"] != model_name or int(manifest["dim"]) != dimension:
            raise ValueError(f"model/dimension mismatch in {manifest_path}")
        for item in manifest["parts"]:
            key = item["part"]
            if key in parts:
                raise ValueError(f"embedding part appears in multiple roots: {key}")
            match = PART_RE.fullmatch(key)
            if not match:
                raise ValueError(f"unexpected part name: {key}")
            vectors = root / "vectors" / f"{key}.npy"
            ids = root / "vectors" / f"{key}-ids.parquet"
            if not vectors.is_file() or not ids.is_file():
                raise FileNotFoundError(f"missing vector/ID pair for {key}: {vectors}, {ids}")
            parts[key] = {
                "part": key,
                "lang": match.group("lang"),
                "number": int(match.group("number")),
                "chunks": int(item["chunks"]),
                "nan_rows": int(item.get("nan_rows", 0)),
                "vectors_path": vectors,
                "ids_path": ids,
                "manifest_path": manifest_path,
                "shard": manifest.get("shard"),
            }
    if not parts or model_name is None or dimension is None:
        raise ValueError("no embedding parts found")
    return model_name, dimension, sorted(parts.values(), key=lambda p: (p["lang"], p["number"]))


def load_and_check_chunk_metadata(
    part: dict[str, Any], chunks_root: Path, hash_rows: dict[str, int], seen_ids: set[str]
) -> tuple[list[str], pa.Table]:
    chunk_path = chunks_root / "chunks" / part["lang"] / f"part-{part['number']:03d}.parquet"
    if not chunk_path.is_file():
        raise FileNotFoundError(f"canonical chunk part not found: {chunk_path}")
    ids_table = pq.read_table(part["ids_path"], columns=["chunk_id"])
    chunk_table = pq.read_table(
        chunk_path,
        columns=["chunk_id", "text_sha256", "rep_doc_id", "lang", "order"],
    )
    if ids_table.num_rows != part["chunks"] or chunk_table.num_rows != part["chunks"]:
        raise ValueError(
            f"row count mismatch for {part['part']}: manifest={part['chunks']}, "
            f"ids={ids_table.num_rows}, chunks={chunk_table.num_rows}"
        )
    ids = ids_table.column("chunk_id").to_pylist()
    chunk_ids = chunk_table.column("chunk_id").to_pylist()
    if ids != chunk_ids:
        first = next((i for i, pair in enumerate(zip(ids, chunk_ids)) if pair[0] != pair[1]), None)
        raise ValueError(f"vector-ID/chunk row order differs for {part['part']} at row {first}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate chunk_id within {part['part']}")
    overlap = seen_ids.intersection(ids)
    if overlap:
        raise ValueError(f"chunk_id occurs in multiple parts, e.g. {next(iter(overlap))}")
    seen_ids.update(ids)

    shas = chunk_table.column("text_sha256").to_pylist()
    rep_ids = chunk_table.column("rep_doc_id").to_pylist()
    langs = chunk_table.column("lang").to_pylist()
    orders = chunk_table.column("order").to_pylist()
    for row, (chunk_id, sha, rep_id, lang, order) in enumerate(zip(ids, shas, rep_ids, langs, orders)):
        if lang != part["lang"]:
            raise ValueError(f"language mismatch in {chunk_path} row {row}: {lang!r}")
        if chunk_id != f"{sha[:16]}-{order}":
            raise ValueError(f"chunk_id does not match hash/order in {chunk_path} row {row}")
        if hash_rows.get(sha) != rep_id:
            raise ValueError(f"hash_groups mapping mismatch in {chunk_path} row {row}")

    row_map = pa.Table.from_arrays(
        [
            pa.array(range(len(ids)), type=pa.int64()),
            pa.array(ids, type=pa.string()),
            pa.array(shas, type=pa.string()),
            pa.array(rep_ids, type=pa.int64()),
            pa.array(langs, type=pa.string()),
            pa.array(orders, type=pa.int32()),
            pa.array([part["part"]] * len(ids), type=pa.string()),
        ],
        schema=ROW_MAP_SCHEMA,
    )
    return ids, row_map


def write_row_map(path: Path, row_map: pa.Table) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(row_map, temporary, compression="zstd")
    os.replace(temporary, path)


def build_part_index(part: dict[str, Any], dimension: int, batch_rows: int, output: Path) -> dict[str, Any]:
    vectors = np.load(part["vectors_path"], mmap_mode="r", allow_pickle=False)
    if vectors.ndim != 2 or vectors.shape != (part["chunks"], dimension):
        raise ValueError(f"vector shape mismatch for {part['part']}: {vectors.shape}")
    if vectors.dtype != np.float16:
        raise ValueError(f"expected float16 source vectors for {part['part']}, got {vectors.dtype}")
    if part["nan_rows"]:
        raise ValueError(f"manifest reports non-finite vectors in {part['part']}")

    index = faiss.IndexFlatIP(dimension)
    started = time.perf_counter()
    for start in range(0, vectors.shape[0], batch_rows):
        end = min(start + batch_rows, vectors.shape[0])
        batch = np.asarray(vectors[start:end], dtype=np.float32, order="C")
        if not np.isfinite(batch).all():
            raise ValueError(f"non-finite values in {part['part']} rows {start}:{end}")
        norms = np.linalg.norm(batch, axis=1)
        if np.any(norms == 0):
            raise ValueError(f"zero vector in {part['part']} rows {start}:{end}")
        batch /= norms[:, None]
        index.add(batch)
    if index.ntotal != part["chunks"]:
        raise ValueError(f"FAISS row count mismatch for {part['part']}: {index.ntotal}")

    output.mkdir(parents=True, exist_ok=True)
    index_path = output / f"{part['part']}.faiss"
    temporary = index_path.with_suffix(index_path.suffix + ".tmp")
    faiss.write_index(index, str(temporary))
    os.replace(temporary, index_path)
    return {
        "part": part["part"],
        "rows": int(index.ntotal),
        "dimension": int(index.d),
        "index": index_path.name,
        "row_map": f"{part['part']}-row-map.parquet",
        "ids_source": str(part["ids_path"]),
        "chunk_source": str(part["chunk_path"]),
        "normalized_float16_input": True,
        "index_storage_dtype": "float32",
        "metric": "inner_product",
        "seconds": round(time.perf_counter() - started, 2),
        "index_bytes": index_path.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vector-root", action="append", type=Path, required=True,
                        help="Embedding dataset/shard root; repeat for every shard")
    parser.add_argument("--chunks-root", type=Path, required=True,
                        help="Root containing chunks/<lang>/part-NNN.parquet")
    parser.add_argument("--hash-groups", type=Path, required=True,
                        help="Canonical hash_groups.parquet from the same chunk run")
    parser.add_argument("--output", type=Path, required=True, help="Directory for FAISS files and row maps")
    parser.add_argument("--batch-rows", type=int, default=8192, help="Vector conversion/add batch size")
    parser.add_argument("--resume", action="store_true", help="Skip completed, shape-checked part indexes")
    args = parser.parse_args()
    if args.batch_rows <= 0:
        parser.error("--batch-rows must be positive")

    model, dimension, parts = discover_parts(args.vector_root)
    if args.output.exists() and any(args.output.iterdir()) and not args.resume:
        parser.error(f"output directory is non-empty; pass --resume to continue: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    hash_table = pq.read_table(args.hash_groups, columns=["text_sha256", "rep_doc_id"])
    hash_rows = dict(zip(hash_table.column("text_sha256").to_pylist(),
                         hash_table.column("rep_doc_id").to_pylist()))
    if len(hash_rows) != hash_table.num_rows:
        raise ValueError(f"duplicate text_sha256 in {args.hash_groups}")

    manifest: dict[str, Any] = {
        "model": model,
        "dimension": dimension,
        "metric": "inner_product",
        "index_type": "IndexFlatIP",
        "input_dtype": "float16",
        "index_dtype": "float32",
        "vectors_renormalized_after_float16_load": True,
        "source_roots": [str(path) for path in args.vector_root],
        "chunks_root": str(args.chunks_root),
        "hash_groups": str(args.hash_groups),
        "parts": [],
        "complete": False,
    }
    atomic_json(args.output / "index_manifest.json", manifest)
    seen_ids: set[str] = set()
    for part in parts:
        ids, row_map = load_and_check_chunk_metadata(part, args.chunks_root, hash_rows, seen_ids)
        row_map_path = args.output / f"{part['part']}-row-map.parquet"
        index_path = args.output / f"{part['part']}.faiss"
        part["chunk_path"] = args.chunks_root / "chunks" / part["lang"] / f"part-{part['number']:03d}.parquet"

        if args.resume and index_path.is_file() and row_map_path.is_file():
            existing = faiss.read_index(str(index_path))
            stored_row_map = pq.read_table(row_map_path)
            if (
                existing.d != dimension
                or existing.ntotal != part["chunks"]
                or existing.metric_type != faiss.METRIC_INNER_PRODUCT
                or not stored_row_map.equals(row_map)
            ):
                raise ValueError(f"existing output does not match source for {part['part']}")
            print(f"resume: verified {part['part']} ({part['chunks']:,} rows)", flush=True)
            part_result = {
                "part": part["part"], "rows": int(existing.ntotal), "dimension": int(existing.d),
                "index": index_path.name, "row_map": row_map_path.name,
                "ids_source": str(part["ids_path"]), "chunk_source": str(part["chunk_path"]),
                "normalized_float16_input": True, "index_storage_dtype": "float32",
                "metric": "inner_product", "seconds": 0,
                "index_bytes": index_path.stat().st_size,
            }
        else:
            write_row_map(row_map_path, row_map)
            part_result = build_part_index(part, dimension, args.batch_rows, args.output)
            print(f"built {part['part']}: {part_result['rows']:,} rows in {part_result['seconds']}s", flush=True)
        manifest["parts"] = [item for item in manifest["parts"] if item["part"] != part["part"]]
        manifest["parts"].append(part_result)
        manifest["parts"].sort(key=lambda item: item["part"])
        manifest["vectors"] = sum(item["rows"] for item in manifest["parts"])
        manifest["estimated_index_bytes"] = sum(item["index_bytes"] for item in manifest["parts"])
        atomic_json(args.output / "index_manifest.json", manifest)

    if len(seen_ids) != sum(part["chunks"] for part in parts):
        raise ValueError("global chunk_id uniqueness check failed")
    manifest["complete"] = True
    manifest["part_count"] = len(parts)
    manifest["faiss_version"] = getattr(faiss, "__version__", "unknown")
    atomic_json(args.output / "index_manifest.json", manifest)
    print(json.dumps({key: value for key, value in manifest.items() if key != "parts"}, indent=2))


if __name__ == "__main__":
    main()
