# Kaggle dataset: medxir-corpus

**URL:** https://www.kaggle.com/datasets/boinhbo/medxir-corpus (private)
**Phiên bản:** v1, upload 22/10/2026. Build bằng `scripts/build_kaggle_dataset.py` từ `artifacts/corpus_v1/`.
**Đường dẫn trong notebook:** `/kaggle/input/medxir-corpus/`

## Cấu trúc

```
docs/en/part-v1-000.parquet          239.918 bài (Europe PMC)
docs/vi/part-v1-000..006.parquet     688.655 bài
docs/zh/part-v1-000.parquet           98.943 bài
registry.parquet                     1.220.598 dòng, mỗi id chính thức một dòng
manifest.json                        số lượng, sha256 từng file, phiên bản parser, ghi chú
```

### `docs/<lang>/*.parquet`

| Cột | Ý nghĩa |
|---|---|
| `doc_id` | `id` trong `links_corpus_vi_en_zh.parquet`, dùng làm nhãn khi nộp bài |
| `lang`, `url`, `host`, `title` | Thông tin bài |
| `text` | Nội dung đã trích xuất |
| `status` | `extracted_unreviewed` hoặc `needs_review` (parser nghi ngờ, vẫn giữ) |
| `parser_version` | `parser-v2.3/2.4/2.5`, `europepmc-v1`, `openalex-v1` |
| `source` | `crawl`, `europepmc`, `cc` (Common Crawl), `wayback`, `pharmacity` |
| `n_chars`, `text_sha256` | Độ dài; hash để phát hiện bài trùng nội dung |

### `registry.parquet`

`doc_id, url, lang, host, state, reason, source, status, parser_version, n_chars, text_sha256, added_in`

| `state` | VI | ZH | EN |
|---|---:|---:|---:|
| `in_corpus` | 688.655 | 98.943 | 239.918 |
| `blocked` (120ask, familydoctor, zysjonline, iiyi, laodong) | 31.282 | 131.993 | 0 |
| `pending` (host đang pause, crawl lại được) | 17.648 | 8.531 | 0 |
| `failed` (404, chuyển về trang chủ, lỗi parse) | 3.013 | 533 | 82 |

Corpus phủ 97,1% số id không bị chặn.

## Đọc trên Kaggle

```python
import pandas as pd, glob
vi = pd.concat(pd.read_parquet(p) for p in sorted(glob.glob('/kaggle/input/medxir-corpus/docs/vi/*.parquet')))
```

## Cập nhật (v1.1 trở đi)

1. Lấy danh sách cần crawl: `registry` với `state != 'in_corpus'` (thường là `pending`).
2. Ghi bài mới vào part mới, ví dụ `docs/vi/part-v1.1-000.parquet`. Không ghi đè part cũ.
3. Build lại registry và manifest, rồi chạy
   `uv run kaggle datasets version -p artifacts/kaggle/medxir-corpus --dir-mode zip -m "v1.1"`.
   Thiếu `--dir-mode zip` thì Kaggle bỏ qua thư mục `docs/`.

## Việc đã biết cho v1.1

- ZH shard 1 crawl bằng parser v2.3: các host 39.net, cnkang, msdmanuals cần reparse bằng v2.4+.
- Pending lớn nhất: ask.39.net 8.531, longchau 7.413, vinmec 4.526, vov2 2.273, baogialai 1.617, vietnamplus 914.
- Text ZH: khoảng 1.077 bài còn `<br>`; nhiều bài có đoạn "核心提示" lặp ở đầu. Nên làm sạch ở bước chunking.
