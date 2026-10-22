# Chunking corpus trên Kaggle

Notebook `src/notebooks/kaggle_chunk_corpus.ipynb` chia mọi văn bản duy nhất của `boinhbo/medxir-corpus` thành chunk bằng đúng `chunk_document()` trong `scripts/chunking.py`. Notebook chạy trên CPU, không cần internet.

## Notebook làm gì

| Bước | Việc |
|---|---|
| 1 | Tìm thư mục dữ liệu dưới `/kaggle/input`, đọc `manifest.json`, in phiên bản |
| 2 | Đọc `registry.parquet`, tính `hash_groups` (đã loại 118 id Pharmacity), ghi `hash_groups.parquet` |
| 3 | Với từng ngôn ngữ và từng part: lấy một dòng cho mỗi `text_sha256`, `clean_text`, `chunk_document` (Pool 4 tiến trình), kiểm tra `cleaned[start:end] == chunk_text` |
| 4 | Ghi `chunks/<lang>/part-NNN.parquet` và `chunk_manifest.json` |
| 5 | In bảng tóm tắt |

Mã trong notebook là bản sao nguyên văn của `scripts/chunking.py`, `scripts/text_clean.py` và `scripts/dedup_groups.py` (chỉ bỏ khối `if __name__ == '__main__'`). Không sửa notebook bằng tay.

## Đầu vào và đầu ra

| Loại | Đường dẫn | Nội dung |
|---|---|---|
| Đầu vào | `/kaggle/input/medxir-corpus/` | `registry.parquet`, `manifest.json`, `docs/<lang>/part-v1-*.parquet` |
| Đầu ra | `/kaggle/working/chunks/<lang>/part-NNN.parquet` | Chunk, một dòng mỗi chunk |
| Đầu ra | `/kaggle/working/hash_groups.parquet` | Một dòng mỗi `text_sha256` |
| Đầu ra | `/kaggle/working/chunk_manifest.json` | Số lượng, phân vị, số văn bản bị làm sạch, thời gian |

Cột của `chunks/<lang>/*.parquet`:

| Cột | Ý nghĩa |
|---|---|
| `chunk_id` | `{text_sha256[:16]}-{order}`, ví dụ `16e48fe2485a248e-0` |
| `text_sha256` | Hash của văn bản gốc, khóa nối với `hash_groups` |
| `rep_doc_id` | doc_id đại diện của nhóm (nhỏ nhất) |
| `lang`, `order` | Ngôn ngữ, thứ tự chunk (bắt đầu từ 0) |
| `start`, `end` | Offset trong văn bản **đã làm sạch** |
| `chunk_text` | `cleaned[start:end]` |
| `n_units` | Kích thước chunk (từ cho vi/en, ký tự/2 cho zh) |
| `title`, `clean_version`, `chunk_params` | Tiêu đề bài, `clean-v1`, `350/50/40` (limit/overlap/minimum) |

`chunk_id` duy nhất trên toàn corpus vì mỗi văn bản chỉ được chunk một lần, trong part chứa đại diện của nó.

## Dùng `hash_groups` khi nộp bài

Mỗi chunk gắn với `text_sha256`. Để nộp, tra `hash_groups` và nhân chunk ra cho từng `doc_id` trong `doc_ids`.

| `group_type` | Số nhóm | Fan-out mặc định |
|---|---:|---|
| `single` | 1.020.477 | Có |
| `url_variant` | 1.576 (3.152 id) | Có |
| `same_host_diff_path` | 1.665 | Không |
| `cross_host` | 65 | Không |

Chỉ `fanout_safe = True` (`single`, `url_variant`) được nhân ra mặc định. Hai nhóm còn lại có nội dung trùng nhưng trang khác nhau (chuyển hướng tới bài gộp, abstract đăng lại, trang giữ chỗ), nên chỉ bật khi đã kiểm tra thủ công.

## Chạy lại script và notebook

```bash
uv run python scripts/dedup_groups.py          # ghi artifacts/aux_v1/exclusions.csv, hash_groups.parquet
uv run python scripts/build_chunk_notebook.py  # ghi src/notebooks/kaggle_chunk_corpus.ipynb
.venv/bin/python -m unittest tests/test_chunk_prep.py -v
```

Test kiểm tra notebook khớp với các script nguồn.

## Các bước trên Kaggle

1. Tạo notebook: **Code → New Notebook → File → Import Notebook**, chọn `src/notebooks/kaggle_chunk_corpus.ipynb`.
2. Thêm dữ liệu: **+ Add Input → boinhbo/medxir-corpus**.
3. Settings: Accelerator **None** (CPU). Internet bật hay tắt đều được (notebook không tải gì từ mạng).
4. **Run All**.
5. Khi xong: **Save Version → output → New Dataset**, đặt tên `medxir-chunks` (`boinhbo/medxir-chunks`).

Cách CLI thay thế (chạy trên máy bạn, sau khi notebook đã push và chạy xong):

```bash
kaggle kernels output boinhbo/<ten-notebook> -p artifacts/kaggle/chunk_output
# thêm dataset-metadata.json với "id": "boinhbo/medxir-chunks", rồi:
kaggle datasets create -p artifacts/kaggle/chunk_output --dir-mode zip
```

## Thời gian và dung lượng ước tính

Đo trên máy local, 16 CPU, Pool 4, mẫu ngẫu nhiên 2.000 văn bản mỗi ngôn ngữ (VI 14.000 vì 7 part):

| Ngôn ngữ | Văn bản duy nhất | Chunk (s / 1.000 văn bản) | Chunk ước tính |
|---|---:|---:|---:|
| vi | 685.354 | 1,17 | ~2,2 triệu |
| en | 239.775 | 0,25 | ~252 nghìn |
| zh | 98.654 | khoảng 0,3 | 242.465 (đã đếm đủ) |

Tổng thời gian local ước tính khoảng 20 phút (gồm `hash_groups` khoảng 1,5 phút). CPU Kaggle chưa đo, nên đặt kỳ vọng 30–45 phút. Dung lượng output ước tính khoảng 1,6 GB.

## Lưu ý

- Làm sạch đổi văn bản ở 34.706 bài zh (35%): bỏ đoạn 核心提示 lặp lại và đổi thẻ `<br>` thành xuống dòng. Chunk lấy từ văn bản đã làm sạch, nên với 68 chunk zh chứa thẻ `<br>` đã đổi, `chunk_text` không còn khớp nguyên văn trong dữ liệu xuất. `scripts/validate_submission.py --texts` sẽ báo cảnh báo cho các chunk này.
- EN có 8 bài với thẻ `<br>` (đổi), 2 bài với `</br>` (không đổi). VI không có thẻ nào trong mẫu.
- `chunk_params` là `350/50/40`, theo `chunk_document()`. Không đổi logic chunk.
