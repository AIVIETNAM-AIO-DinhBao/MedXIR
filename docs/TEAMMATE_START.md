# Khởi chạy máy teammate — shard 1

Máy A đã chạy shard 0. Máy B dùng đúng source snapshot/plan dưới đây, remote rclone tên `gdrive` đã có quyền ghi vào folder MedXIR. Gói source không chứa token. Thực hiện trong thư mục mới; cần uv, rclone, Python 3.12+.

```bash
mkdir MedXIR-recrawl-20261015
cd MedXIR-recrawl-20261015
rclone copyto 'gdrive,root_folder_id=1tfvL8OJIRejT_c-4wfaQnTZecT67qJld,team_drive=:/coordination/recrawl_20261015/source-610e033b5abba1f4.tar.gz' medxir-source.tar.gz
echo '610e033b5abba1f439f498ccb6cde807762c244328e7637112e57599d6c40bda  medxir-source.tar.gz' | sha256sum -c -
tar -xzf medxir-source.tar.gz
uv sync
uv run python scripts/run_recrawl.py --shard-id 1 --download
```

Chỉ giải nén sau khi checksum báo OK. Runner tải manifest, ba plan và storage config; kiểm hash plan rồi chạy shard 1, tự resume nếu có checkpoint. Mặc định tổng 16 worker, 8 VI + 8 ZH; EN hoãn và có báo cáo riêng. Không chạy shard 0 trên máy B.

Lệnh trên chạy foreground; giữ terminal hoặc dùng tmux. Ctrl+C dừng các child và giữ checkpoint. Chạy lại đúng lệnh cuối để resume. Log, trạng thái và danh sách ID cần xử lý nằm trong `artifacts/recrawl_20261015/`; chi tiết ở `docs/RECRAWL_RUNBOOK.md`.
