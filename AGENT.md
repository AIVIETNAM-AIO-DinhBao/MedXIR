# AGENT.md

Hướng dẫn cho agent (Claude, Codex, …) làm việc trong repo MedXIR.

## Chạy job trên Kaggle: `tools/kaggle`

Mọi job Kaggle chạy qua `python -m tools.kaggle`. Không gọi `kaggle kernels push` trực tiếp.

```bash
uv run python -m tools.kaggle doctor --check-auth --account boinhbo
uv run python -m tools.kaggle accounts list                    # tên profile, không đọc token
uv run python -m tools.kaggle accounts quota                   # giờ GPU còn lại mỗi account
uv run python -m tools.kaggle kernel run --config configs/kaggle/<job>.yaml --source . --account boinhbo
uv run python -m tools.kaggle kernel status boinhbo/<slug> --account boinhbo
uv run python -m tools.kaggle kernel fetch boinhbo/<slug> --destination <dir> --file <path> --account boinhbo
```

- `kernel run` push, chờ, tải `fetch_files`, kiểm tra `required_outputs` và ghi bản ghi vào `runs/<run-id>/` (`metadata.json`, log, `artifacts/`). Lệnh chặn tới khi job xong, nên chạy nền.
- `kernel push` chỉ push rồi trả về ngay (`QUEUED`). Theo dõi tiếp bằng `status` hoặc `wait`.
- `--kind experiment` (mặc định) đòi `code_file` đã được git commit. `--kind smoke` dùng để thử nhanh code chưa commit.

### Quy ước

| Mục | Quy ước |
|---|---|
| Code chạy trên Kaggle | Đặt trong `src/` (tool bắt buộc). Notebook nằm ở `src/notebooks/`, được sinh bởi `scripts/build_*_notebook.py`. Sửa generator, không sửa notebook |
| Config | `configs/kaggle/<job>.yaml`, khóa `kernel:`. Slug của `id` phải trùng slug của `title` |
| Account | Account token lưu trong `~/.config/agent-auto-research/kaggle/<user>.token`. Dataset `boinhbo/medxir-corpus` là private của **boinhbo**, nên job đọc corpus phải dùng `--account boinhbo`. `--account auto` (chọn theo quota) chỉ dùng cho job không cần dataset private, hoặc sau khi đã share dataset cho các account khác |
| Internet | Chỉ job chạy RTX Pro 6000 bắt buộc Internet OFF. Model lấy từ output của `boinhbo/medxir-package-models` (`kernel_sources`). CPU và T4 không bắt buộc |
| Phần cứng | `machine_shape` CLI hỗ trợ: `NvidiaTeslaT4` (T4 ×2), `NvidiaTeslaP100`, `Tpu1VmV38`. RTX Pro 6000 chưa có trong CLI 2.2.4, phải chạy qua giao diện Kaggle |
| Nối job | Job sau đọc output job trước bằng `kernel_sources: [boinhbo/<slug>]`, không cần tạo dataset |
| Bản ghi | `runs/` và `.work/` nằm trong `.gitignore` |

### Các job hiện có

| Config | Kernel | Phần cứng | Output chính |
|---|---|---|---|
| `configs/kaggle/chunk_corpus.yaml` | `boinhbo/medxir-chunk-corpus` | CPU | `chunks/<lang>/part-*.parquet`, `hash_groups.parquet`, `chunk_manifest.json` |
| `configs/kaggle/embed_benchmark_t4.yaml` | `boinhbo/medxir-embed-benchmark-t4` | T4 ×2, Internet ON | `embed_benchmark.json/.csv` |
| `configs/kaggle/package_models.yaml` | `boinhbo/medxir-package-models` | CPU, Internet ON | `models/<name>/`, `models/MODELS.json` |
| `configs/kaggle/embed_benchmark_rtx6000.yaml` | `boinhbo/medxir-embed-benchmark-rtx6000` | RTX Pro 6000 (chọn trong UI), Internet OFF | `embed_benchmark.json/.csv`. Cần chạy `package_models` trước |
| `configs/kaggle/embed_corpus_06b_rtx6000.yaml` | `boinhbo/medxir-embed-corpus-06b` | RTX Pro 6000 (chọn trong UI), Internet OFF | `vectors/*.npy` (float16, 1024 chiều), `embed_manifest.json`. Cần `chunk_corpus` và `package_models` |

## Quy tắc chung của dự án

- Chunk: dùng nguyên `chunk_document()` trong `scripts/chunking.py` (350/50/40; số từ với VI/EN, số chữ / 2 với ZH). `chunk_text` luôn là `text[start:end]` của văn bản đã làm sạch (`scripts/text_clean.py`).
- Không xóa `doc_id`. Nội dung trùng thì embed một lần, rồi mở rộng kết quả qua `hash_groups` (mặc định chỉ mở rộng nhóm `fanout_safe`). 118 id pharmacity bị redirect sai nằm trong `artifacts/aux_v1/exclusions.csv`.
- Không vượt chặn của website (không dùng VPN, không xoay IP).
- Model: mã nguồn mở, tối đa 15B, phát hành trước 01/08/2026 (OVERVIEW.md).
- Commit thì được, nhưng chỉ push lên `origin` khi người dùng yêu cầu.
- Test: `uv run python -m unittest discover -s tests`.
