# MedXIR

Hệ thống truy hồi thông tin y sinh đa ngôn ngữ cho cuộc thi ViBioMIR: nhận câu hỏi tiếng Việt, tìm tài liệu và đoạn nội dung liên quan từ các nguồn tiếng Việt, tiếng Trung và tiếng Anh.

**Hiện trạng:** đã khảo sát nguồn và triển khai parser trên dữ liệu pilot. Chưa crawl toàn bộ corpus, chưa chunk/index hay chạy baseline retrieval. Xem [PROGRESS.md](PROGRESS.md) để biết kết quả và công việc còn lại.

## Dữ liệu và hướng giải quyết

Dữ liệu cục bộ gồm **4.394.718 URL trên 97 host** và **1.200 truy vấn**. Corpus chỉ cung cấp `id/url`, không có sẵn nội dung tài liệu; truy vấn có `id/query`, chưa có nhãn liên quan cục bộ.

Pipeline dự kiến:

```text
URL chính thức → tải và lưu raw → parse, làm sạch có cấu trúc
→ chunk → sparse + dense retrieval → chọn tài liệu/chunk → JSON nộp bài
```

Giữ nguyên ID tài liệu chính thức, dù ID không liên tục. Ưu tiên độ phủ nguồn, nội dung sạch và đầy đủ trước khi tối ưu retrieval. Thiết kế chi tiết nằm trong [PROPOSED_SOLUTION.md](PROPOSED_SOLUTION.md).

## Cấu trúc repo

| Đường dẫn | Vai trò |
|---|---|
| `OVERVIEW.md` | Đề bài, mục tiêu và quy định cuộc thi |
| `PROPOSED_SOLUTION.md` | Đề xuất giải pháp version 1 |
| `PROGRESS.md` | Tiến trình, kết quả thực tế và việc còn lại |
| `docs/` | Đặc tả dữ liệu, đánh giá, nộp bài và danh mục template |
| `dataset/` | Hai file Parquet được cấp và tài liệu dữ liệu |
| `notebooks/host_template_audit.ipynb` | Lấy mẫu các host, tải và khảo sát cấu trúc trang |
| `notebooks/parser_review.ipynb` | Chạy parser trên pilot, kiểm tra và đối chiếu kết quả |
| `scripts/content_parser.py` | Logic parser theo host, dùng lại được cho crawler |
| `artifacts/` | Raw và kết quả chạy; không được Git theo dõi |
| `pyproject.toml`, `uv.lock` | Cấu hình và khóa thư viện bằng uv |

## Cài đặt

Cần Python 3.12 trở lên và uv. Chạy tại thư mục gốc repo:

```bash
uv sync
```

Trong IDE, chọn interpreter/kernel `.venv/bin/python` để chạy notebook. Đặt dữ liệu được cấp tại:

```text
dataset/links_corpus.parquet
dataset/query.parquet
```

## Chạy pilot và xem kết quả

1. Mở `notebooks/host_template_audit.ipynb`, xem cấu hình rồi chạy các cell. Notebook khảo sát toàn bộ danh sách host; số mẫu tùy host và cấu hình. `DOWNLOAD = True` cho phép tải URL chưa có cache; đặt `False` để chỉ dùng dữ liệu đã tải.
2. Mở `notebooks/parser_review.ipynb` và chạy các cell để parse, kiểm tra và xem nội dung nguồn so với nội dung trích.
3. Mở `artifacts/parser_review/review.html` trong trình duyệt để lọc theo host, ID, trạng thái hoặc cảnh báo.

Có thể chạy riêng parser từ thư mục gốc, sau khi đã có dữ liệu pilot:

```bash
uv run python scripts/content_parser.py
```

Lệnh này đọc cache, không tải trang mới. Có thể đổi đường dẫn bằng `--pilot` và `--output`.

Đầu ra được giữ gọn:

```text
artifacts/
├── host_template_audit/
│   ├── pages.jsonl          # Manifest: ID, URL, trạng thái tải và đường dẫn raw
│   ├── host_summary.csv     # Tổng hợp theo host
│   └── raw/                # Response gốc nén gzip
└── parser_review/
    ├── documents.jsonl     # Nội dung có cấu trúc và thông tin kiểm tra
    └── review.html         # Báo cáo đối chiếu
```

Teammate cần tự chạy pilot hoặc nhận cache riêng vì `artifacts/` không nằm trong Git.

## Đọc kết quả parser

Parser giữ title, các block theo thứ tự, câu hỏi/câu trả lời, metadata, tài nguyên và dấu vết nguồn. Các phần bị loại được lưu kèm lý do; vùng ngoài selector cũng được đưa vào báo cáo để phát hiện bỏ sót.

- `extracted_unreviewed`: trích được, chưa có cảnh báo tự động; chưa được duyệt sạch.
- `needs_review`: cần đối chiếu, chẳng hạn thiếu tab chi tiết, nội dung trả phí hoặc media chưa có transcript.
- `encoded_content`: chưa giải mã được thân bài thành text hữu ích.
- Các trạng thái tải lỗi, challenge hoặc chuyển về trang chủ được giữ riêng, không coi là nội dung bài.

Pilot không bảo đảm đã bao phủ mọi template trong corpus. Kiểm tra bảo toàn chữ chỉ áp dụng cho vùng đã chọn sau khi cắt UI; cần đối chiếu nguồn để phát hiện phần hữu ích bị bỏ ngoài vùng đó.

## Tài liệu tham chiếu trong repo

- [Đề bài](OVERVIEW.md)
- [Dữ liệu](docs/DATA.md)
- [Cách đánh giá](docs/EVALUATION.md)
- [Định dạng nộp bài](docs/SUBMISSION.md)
- [Template và ngoại lệ theo host](docs/HOST_TEMPLATES.md)
- [Giải pháp V1](PROPOSED_SOLUTION.md)
- [Tiến trình](PROGRESS.md)
