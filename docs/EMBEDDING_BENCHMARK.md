# Benchmark embedding Qwen3 trên Kaggle

Hai notebook, sinh bởi `scripts/build_embed_benchmark_notebook.py` (không sửa tay, sửa generator rồi chạy lại):

| File | Việc |
|---|---|
| `src/notebooks/kaggle_embed_benchmark.ipynb` | 100 chunk mẫu, so Qwen3-Embedding-0.6B và 4B, 1 GPU và 2 GPU |
| `src/notebooks/kaggle_package_models.ipynb` | Tải hai model đã ghim revision, đóng gói để chạy offline |

Dataset dùng: `boinhbo/medxir-corpus` (v1). Model offline sẽ là `boinhbo/qwen3-embedding` (tạo ở bước B).

## A. Benchmark trên T4 x2 (Internet ON)

| Bước | Thao tác |
|---|---|
| 1 | Kaggle: New Notebook → File → Import Notebook → chọn `src/notebooks/kaggle_embed_benchmark.ipynb` |
| 2 | Add data → tìm `boinhbo/medxir-corpus` |
| 3 | Settings: Accelerator = GPU T4 x2; Internet = On |
| 4 | Run All. Lần đầu tải 0.6B và 4B từ Hugging Face (khoảng 9 GB, có thể mất vài chục phút, tính vào load_s) |
| 5 | Đọc output theo bảng dưới |

Đọc output:

| Mục | Xem gì | Mong đợi |
|---|---|---|
| Section 3 | `chunks per language` và bảng chunk | VI 81, EN 9, ZH 10 |
| Section 4 | bảng token: `mean`, `p90`, `over_max_len` | `over_max_len` gần 0 với `MAX_LENGTH = 1024` |
| Section 6 | cột `real_tok_per_s`, `peak_vram_gb`, `nan_rows`, `max_norm_dev` | `nan_rows` = 0; `max_norm_dev` < 1e-2; dòng `OOM` nếu có |
| Bảng estimate | `hours` và `gpu_hours` cho từng model và cấu hình | `gpu_hours` so với quota |
| File | `/kaggle/working/embed_benchmark.json` và `.csv` | tải về để lưu |

Quota GPU của Kaggle khoảng 30 giờ/tuần (**cần kiểm tra** trên trang tài khoản). Với 2 GPU, cột `gpu_hours` = giờ thực tế x 2.

## B. Chuẩn bị model offline (CPU, Internet ON)

| Bước | Thao tác |
|---|---|
| 1 | Import `src/notebooks/kaggle_package_models.ipynb` |
| 2 | Settings: Internet = On. Accelerator không cần |
| 3 | Run All. Kiểm tra `MODELS.json`: có `sha`, `created_at`, tổng dung lượng dưới 20 GB |
| 4 | Lưu output thành dataset `boinhbo/qwen3-embedding`. Cấu trúc cần có: `models/Qwen3-Embedding-0.6B/` và `models/Qwen3-Embedding-4B/` |

Bước 4: chưa xác minh trên giao diện Kaggle hiện tại. Nếu không thấy cách lưu output thành dataset, báo lại để chọn cách khác. Không tải 4B về máy.

## C. Benchmark trên RTX Pro 6000 (Internet OFF)

| Bước | Thao tác |
|---|---|
| 1 | Import lại `src/notebooks/kaggle_embed_benchmark.ipynb` (cùng file với A) |
| 2 | Add data: `boinhbo/medxir-corpus` và `boinhbo/qwen3-embedding` |
| 3 | Settings: Accelerator = RTX Pro 6000 (tên hiển thị chưa xác minh); Internet = Off |
| 4 | Run All |

Notebook tự tìm model trong `/kaggle/input`, đặt `HF_HUB_OFFLINE=1`, bỏ bước `pip`. Trên RTX Pro 6000 dtype là bf16 và chỉ có một GPU, nên bảng chỉ có cấu hình `1 GPU`.

## Quy tắc quyết định

Hôm nay là 22/10. Hạn chốt là khoảng 26/10.

- Nếu ước lượng `gpu_hours` của 4B cho toàn corpus vượt phần quota còn lại trước 26/10, chuyển sang phương án dự phòng:
  - Embed toàn corpus bằng 0.6B.
  - Chỉ dùng 4B (hoặc reranker) cho ứng viên: 1.200 query x top 50 document.
- Ngược lại, dùng 4B cho toàn corpus.

## Lưu ý

- 100 chunk là mẫu nhỏ. Tốc độ chỉ mang tính tham khảo, sai số khoảng ±30%.
- Độ dài token trung bình theo ngôn ngữ đo trên pool khoảng 2.800 chunk (không phải 100 chunk), vì EN và ZH chỉ có 9 và 10 chunk trong mẫu.
- Thời gian tokenize không tính vào tốc độ.
