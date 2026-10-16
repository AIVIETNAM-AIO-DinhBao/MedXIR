# MedXIR

Hệ thống truy hồi thông tin y sinh đa ngôn ngữ cho cuộc thi ViBioMIR: nhận câu hỏi tiếng Việt, tìm tài liệu và đoạn nội dung liên quan từ các nguồn tiếng Việt, tiếng Trung và tiếng Anh.

**Hiện trạng (15/10/2026):** corpus hiện hành có 1.220.598 URL / 94 host; parser `parser-v2.3`. Đã tạo ba plan ngôn ngữ, chia hai shard, cấu hình Drive theo folder ID con và hàng đợi tải lỗi/parse lỗi/thiếu nội dung cập nhật định kỳ. Có 811.616 ID được mở crawl ban đầu; 408.982 ID hoãn do nguồn bị chặn hoặc PubMed chưa có parser. Lần crawl mới dùng [RECRAWL_RUNBOOK.md](docs/RECRAWL_RUNBOOK.md); các lệnh/corpus cũ ở phần dưới và runbook cũ là lịch sử.

## Dữ liệu và hướng giải quyết

Dữ liệu hiện hành là `dataset/links_corpus_vi_en_zh.parquet`, gồm **1.220.598 URL trên 94 host** và **1.200 truy vấn**. `dataset/links_corpus.parquet` trong checkout hiện tại là Git LFS pointer, chưa phải dữ liệu đọc được. Corpus chỉ cung cấp `id/url`, không có sẵn nội dung tài liệu; truy vấn có `id/query`, chưa có nhãn liên quan cục bộ.

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
| `docs/` | Đặc tả dữ liệu, đánh giá, nộp bài, template và [runbook crawl hai máy](docs/CRAWL_RUNBOOK.md) |
| `dataset/` | Hai file Parquet được cấp và tài liệu dữ liệu |
| `notebooks/host_template_audit.ipynb` | Lấy mẫu các host, tải và khảo sát cấu trúc trang |
| `notebooks/parser_review.ipynb` | Chạy parser trên pilot, kiểm tra và đối chiếu kết quả |
| `scripts/content_parser.py` | Logic parser theo host, dùng lại được cho crawler |
| `scripts/crawl.py` | Lập kế hoạch, tải URL, lưu checkpoint và gọi parser |
| `scripts/storage.py` | Đóng batch, upload/xác minh Google Drive, đọc lại document/raw |
| `storage.json` | Đích Google Drive dùng chung, cố định ID thư mục; không có token |
| `tests/test_pipeline.py` | Kiểm tra offline việc chia shard, resume/tạm dừng, bảo toàn raw và parse cấu trúc |
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

Lệnh này đọc cache, không tải trang mới và không cập nhật checkpoint crawler.

```bash
# Đổi thư mục pilot và thư mục báo cáo.
uv run python scripts/content_parser.py \
  --pilot artifacts/host_template_audit --output artifacts/parser_review

# Parse một manifest crawler ra báo cáo riêng; yêu cầu toàn bộ raw còn local.
uv run python scripts/content_parser.py \
  --pilot artifacts/crawl/shard-000-of-001 --output artifacts/parser_review

# Xem các tùy chọn parser.
uv run python scripts/content_parser.py --help
```

| Tùy chọn parser | Mặc định | Ý nghĩa |
|---|---|---|
| `--pilot` | `artifacts/host_template_audit` | **Thư mục** có `pages.jsonl` và raw gzip theo đường dẫn trong manifest |
| `--output` | `artifacts/parser_review` | Thư mục xuất `documents.jsonl` và `review.html`; ghi lại kết quả khi chạy lại |
| `--help` | — | Hiển thị trợ giúp |

Nếu raw đã được dọn lên cloud, dùng `crawl.py --reparse --storage-config storage.json` thay cho lệnh parser độc lập. Notebook `parser_review.ipynb` cũng dùng cùng module parser; khởi động lại kernel khi cần nạp code vừa sửa.

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
- `unknown_template`: chưa chọn được vùng nội dung; giữ raw và text nguồn để bổ sung selector.
- `empty_content`: nhận ra vùng nội dung nhưng vùng đó rỗng trong HTML nguồn.
- `source_error`: nguồn trả trang lỗi trong HTTP 200, như PHP fatal error hoặc soft 404.
- `parser_error`: code parser gặp exception; khác với lỗi tải và lỗi từ nguồn.
- Các trạng thái tải lỗi, challenge hoặc chuyển về trang chủ được giữ riêng, không coi là nội dung bài.

Parser hiện dùng version `parser-v2.2`. Giữ `normalizations` cho escaped HTML và payload CMS đã giải mã, `removed` có text/link của phần bị loại và `pagination_links` cho trang tiếp theo. Long Châu đọc các trường bài/hồ sơ bệnh từ `__NEXT_DATA__`, gồm phần chưa hiển thị, FAQ và nguồn tham khảo; `source_path` chỉ rõ trường JSON. Cảnh báo phân trang thể hiện **chưa tải phần tiếp theo**; parser không tự gửi request. Với ảnh/PDF/media, giữ URL/caption và cảnh báo nội dung chưa được chuyển thành text đầy đủ.

Pilot không bảo đảm đã bao phủ mọi template trong corpus. Kiểm tra bảo toàn chữ chỉ áp dụng cho vùng đã chọn sau khi cắt UI; cần đối chiếu nguồn để phát hiện phần hữu ích bị bỏ ngoài vùng đó.

## Crawl mở rộng

Chạy tại thư mục gốc:

```bash
# Tạo kế hoạch 10.000 URL nếu plan chưa tồn tại; chưa gửi request.
uv run python scripts/crawl.py --prepare-only

# Crawl và tạo báo cáo mẫu khi kết thúc hoặc dừng bằng Ctrl+C.
uv run python scripts/crawl.py --review-limit 200
```

Kế hoạch lấy tất cả host có HTML trong pilot và còn URL mới. Mỗi host nhận tối đa 20 mẫu ban đầu, phần còn lại phân bổ theo căn bậc hai số URL để cân bằng độ phủ host nhỏ và nguồn lớn. Trong host, các nhóm thành phần đầu của đường dẫn được xen kẽ; đây là heuristic lấy mẫu, không phải nhãn template đã xác minh. URL pilot được loại khỏi đợt mở rộng.

Mặc định tối đa 12 HTTP request đồng thời, `--host-workers 1`, cách nhau ít nhất 1,5 giây trên một nguồn. Các subdomain thuộc `39.net`, `familydoctor.com.cn`, `iiyi.com` dùng chung giới hạn worker/delay; các host khác có giới hạn riêng. Redirect và retry cũng qua các giới hạn này. Timeout HTTP là 20 giây, thời gian tải mỗi hop tối đa 40 giây; chờ đến lượt không tính là timeout mạng. Tối đa hai retry thêm, mỗi response tối đa 10 MiB.

Sau ba lỗi truy cập liên tiếp hoặc `Retry-After` trên 60 giây, crawler tạm dừng cả nhóm nguồn. Parser gặp ba lỗi liên tiếp, hoặc ít nhất 5 lỗi trong 50 kết quả gần nhất, sẽ tạm dừng riêng host đó. Lỗi parser ở đây gồm unknown, exception, chưa giải mã được body hoặc mismatch chữ; cảnh báo media/wiki không tự làm dừng. Raw đã tải được giữ, URL chưa gửi tiếp tục nằm trong hàng chờ. Trạng thái tạm dừng lưu trong SQLite và được giữ qua resume; khi còn pending, lệnh crawl kết thúc với mã 2.

Chạy lại cùng lệnh để tiếp tục các URL chưa hoàn thành. URL đã có kết quả, kể cả lỗi sau retry, được giữ nguyên; các lỗi đó cần xem xét riêng trước khi tải lại. Dừng bằng Ctrl+C để xuất kết quả đã có.

```bash
# Tiếp tục checkpoint hiện tại và xem tiến trình trực tiếp trong terminal.
uv run python scripts/crawl.py --resume --review-limit 200

# Xem tiến độ từ checkpoint, dùng được cả khi crawler đang chạy.
uv run python scripts/crawl.py --status

# Tùy chỉnh tần suất in tiến độ (mặc định mỗi 25 URL).
uv run python scripts/crawl.py --resume --progress-every 10

# Sau khi đã xử lý nguyên nhân/chờ nguồn cho phép truy cập trở lại:
uv run python scripts/crawl.py --resume --retry-paused
```

`--resume` yêu cầu checkpoint và plan đã tồn tại; lệnh thường cũng tự bỏ qua URL đã hoàn thành. Tiến trình hiển thị số đã lưu/tổng, phần trăm, tốc độ của lần chạy hiện tại và host vừa xử lý. `--status` chỉ đọc checkpoint, không tải URL hoặc tạo lại plan.

`review.html` của crawler chọn tối đa một mẫu mỗi cặp host/trạng thái, ưu tiên lỗi và cảnh báo, rồi giới hạn theo `--review-limit`. Báo cáo này không chứa mọi document; xem `documents.jsonl` để đọc đầy đủ kết quả. Parser pilot độc lập xuất toàn bộ mẫu pilot vào báo cáo.

```text
artifacts/crawl/
├── plan.jsonl
└── shard-000-of-001/
    ├── state.sqlite       # Checkpoint chính: hàng chờ, kết quả tải/parse, cấu hình
    ├── raw/               # Response gốc nén gzip
    ├── summary.json       # Cập nhật theo --progress-every và khi kết thúc
    ├── pages.jsonl        # Xuất khi kết thúc/dừng
    ├── documents.jsonl    # Xuất khi kết thúc/dừng
    └── review.html        # Nếu có --review-limit; mẫu theo host/trạng thái
```

Khi sửa parser, có thể parse lại raw hoặc chỉ xuất checkpoint mà không tải mới. Dừng crawler trước khi dùng các lệnh này trên cùng shard:

```bash
# Chỉ parse các document có parser_version khác version hiện tại.
# Dừng bằng Ctrl+C; chạy lại cùng lệnh để tiếp tục phần chưa cập nhật.
uv run python scripts/crawl.py --reparse --parse-workers 4 \
  --progress-every 100 --review-limit 200

# Parse lại cả document đã ở version hiện tại, ví dụ sửa rule nhưng chưa đổi VERSION.
uv run python scripts/crawl.py --reparse --force-reparse --parse-workers 4

# Chỉ xuất kết quả đã lưu, không fetch và không parse.
uv run python scripts/crawl.py --export-only --review-limit 200

# Raw đã dọn local: đọc lại batch trên Google Drive khi cần.
# Lệnh này cũng đóng/upload batch kết quả parse mới; không tự dọn local.
uv run python scripts/crawl.py --reparse --storage-config storage.json \
  --parse-workers 4 --progress-every 100 --review-limit 200

# Trong chế độ cloud, cần --export-only để xuất đầy đủ JSONL local.
uv run python scripts/crawl.py --export-only --storage-config storage.json --review-limit 200

# Nếu đã reparse local, xuất bản batch kết quả mới lên Drive, giữ dữ liệu local.
uv run python scripts/storage.py upload
```

### Tạo đợt mới hoặc chọn toàn bộ nguồn đủ điều kiện

```bash
# Một đợt nhỏ mới, giữ nguyên plan/checkpoint hiện tại.
uv run python scripts/crawl.py --prepare-only --limit 5000 \
  --plan artifacts/crawl_next/plan.jsonl --output artifacts/crawl_next

# Tạo plan toàn bộ: dùng lệnh đầy đủ ở mục Chạy toàn bộ corpus bên dưới.

# Crawl từ plan mới, hiển thị tiến độ trong terminal.
uv run python scripts/crawl.py \
  --plan artifacts/crawl_next/plan.jsonl --output artifacts/crawl_next \
  --workers 32 --host-workers 1 --delay 2 --progress-every 100
```

Plan mới loại ID đã thử trong pilot; các dòng `skipped_host` chưa tải vẫn được giữ. Dùng `--exclude-completed PATH/state.sqlite` để loại ID đã hoàn thành ở đợt khác, kể cả kết quả lỗi; có thể truyền nhiều lần. File `plan.summary.json` ghi số lượng theo host và host bị hoãn. Các tùy chọn chọn URL chỉ có tác dụng khi tạo plan mới. `--output` không tự thay đổi đường dẫn `--plan`; hãy truyền cả hai khi đổi đợt.

### Toàn bộ tùy chọn crawler

Chọn một chế độ trong `--prepare-only`, `--status`, `--reparse`, `--export-only`; không chọn thì crawl. `--resume` yêu cầu checkpoint đã tồn tại và có thể dùng với crawl/reparse/export. Không chạy các lệnh sửa checkpoint đồng thời trên cùng shard.

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--corpus` | `dataset/links_corpus.parquet` | Parquet URL/ID chính thức |
| `--pilot` | `artifacts/host_template_audit/pages.jsonl` | **File** manifest pilot để chọn host và loại ID đã lấy mẫu |
| `--plan` | `artifacts/crawl/plan.jsonl` | Plan dùng chung; file đã tồn tại được tái sử dụng nguyên trạng |
| `--output` | `artifacts/crawl` | Thư mục cha của các checkpoint shard |
| `--limit` | `10000` | Số URL khi tạo plan; `0` lấy tất cả URL đủ điều kiện |
| `--seed` | `20261010` | Seed lấy mẫu khi tạo plan mới |
| `--exclude-completed` | Không có | Checkpoint chứa ID đã hoàn thành cần loại khỏi plan mới; lặp lại được |
| `--include-host` | Không có | Thêm host đã xác minh ngoài danh sách pilot; lặp lại được |
| `--all-hosts` | Tắt | Plan mới gồm cả host chưa lấy được HTML trong pilot; không bảo đảm truy cập thành công |
| `--shard-count` | `1` | Tổng số shard; cố định trong suốt đợt |
| `--shard-id` | `0` | Shard máy này xử lý, từ 0 tới `shard-count - 1` |
| `--workers` | `12` | Số request HTTP đang tải tối đa |
| `--host-workers` | `1` | Số request đang tải tối đa theo host/nhóm subdomain dùng chung |
| `--delay` | `1.5` | Giây giữa các lần bắt đầu request theo host/nhóm; nhân theo số shard |
| `--timeout` | `20` | Timeout HTTP, giây; tải mỗi hop giới hạn gấp đôi, không gồm chờ đến lượt |
| `--retries` | `2` | Số lần thử thêm sau lần tải đầu |
| `--max-bytes` | `10485760` | Giới hạn response đã giải nén, byte (10 MiB) |
| `--progress-every` | `25` | In tiến độ sau mỗi N URL/document, áp dụng crawl và reparse |
| `--review-limit` | `0` | Số mẫu tối đa trong HTML; `0` không tạo báo cáo HTML |
| `--prepare-only` | Tắt | Chỉ tạo/tái sử dụng plan, không gửi request |
| `--resume` | Tắt | Yêu cầu checkpoint/plan hiện có; crawl bỏ qua mọi URL đã lưu kết quả |
| `--retry-paused` | Tắt | Cho các URL pending của host bị tạm dừng chạy lại; chỉ dùng ở chế độ crawl, không tải lại ID đã có kết quả |
| `--status` | Tắt | Chỉ đọc tiến độ; dùng được khi crawler đang chạy |
| `--reparse` | Tắt | Parse raw đã lưu, bỏ qua document cùng parser version; không crawl nguồn |
| `--force-reparse` | Tắt | Dùng cùng `--reparse` để xử lý cả document cùng version |
| `--parse-workers` | `2` | Số process CPU cho reparse; tách biệt worker HTTP, có thể dùng `1` |
| `--export-only` | Tắt | Chỉ xuất checkpoint thành JSONL/summary/HTML theo cấu hình |
| `--storage-config` | Không dùng | File đích cloud dùng chung, ví dụ `storage.json` |
| `--batch-docs` | `10000` | Đóng batch khi đủ số document |
| `--batch-mb` | `256` | Đóng batch khi đủ MiB raw gzip; ngưỡng nào tới trước áp dụng trước |
| `--prune-local` | Tắt | Dọn raw/batch và rút gọn document sau xác minh cloud; cần storage config |
| `--cache-limit-gb` | `4` | Mốc dừng cho raw/batch local khi crawl có cloud (GiB); không gồm SQLite/export |
| `--help` | — | Hiển thị trợ giúp |

```bash
uv run python scripts/crawl.py --help
```

### Chia cho hai máy

Sao chép **cùng file `plan.jsonl`** cho hai máy, dùng cùng `--shard-count` và mỗi máy một `--shard-id`:

```bash
# Máy A
uv run python scripts/crawl.py --shard-count 2 --shard-id 0 --review-limit 200

# Máy B
uv run python scripts/crawl.py --shard-count 2 --shard-id 1 --review-limit 200

# Xem tiến độ riêng shard máy A; thêm --resume vào lệnh máy A/B để chạy tiếp.
uv run python scripts/crawl.py --shard-count 2 --shard-id 0 --status
```

Phân việc bằng `doc_id % shard_count`, không thay ID gốc. Kết quả nằm ở các thư mục shard riêng. Crawler tự nhân khoảng cách request với số shard để giảm tốc độ mỗi máy; đây không phải bộ điều phối tốc độ chung, các máy vẫn có thể gửi request cùng lúc. Nếu cần giới hạn tổng nghiêm ngặt theo host, cần bộ điều phối chung. Không đổi kế hoạch/số shard trong thư mục checkpoint đã dùng.

Đợt đang có checkpoint dùng một shard (`--shard-count 1`, mặc định). Chạy `--resume` với cấu hình này để giữ kết quả đã tải. Chuyển sang hai shard tạo thư mục checkpoint mới và không tự chuyển kết quả từ một shard; nên chốt số shard trước mỗi đợt.

Kế hoạch đã tồn tại được tái sử dụng nguyên trạng; đổi `--limit`/`--seed` không viết đè kế hoạch cũ. Muốn đợt mới, dùng đường dẫn kế hoạch và output mới. `--limit 0` chọn toàn bộ URL mới của các host đủ điều kiện; chỉ dùng sau khi đã duyệt kết quả đợt mở rộng.

## Google Drive: lưu cuốn chiếu và dùng nhiều máy

### Kết nối một lần

Cài [rclone](https://rclone.org/install/) trên từng máy. Máy hiện tại đã có binary ở `~/.local/bin/rclone`; không cần thêm thư viện Python.

```bash
# Tạo remote tên gdrive, loại Google Drive; đăng nhập tài khoản lưu dữ liệu.
~/.local/bin/rclone config

# Máy đầu tiên: tạo cấu hình chung chứa remote và ID thư mục MedXIR.
uv run python scripts/storage.py init --remote gdrive:MedXIR
uv run python scripts/storage.py prepare
```

Sao chép **cùng `storage.json` và cùng `plan.jsonl`** sang các máy còn lại. Mỗi máy tự đăng nhập rclone, đặt remote tên `gdrive`, rồi chạy lại `storage.py init` để kiểm tra truy cập. Mọi upload/download dùng `folder_id` làm gốc, nên không lệ thuộc việc mỗi tài khoản có một thư mục trùng tên. Teammate dùng tài khoản khác cần được cấp quyền sửa vào cùng thư mục Google Drive. **Không tự tạo lại storage.json trên từng máy**: việc khởi tạo đích chỉ làm một lần. Token ở cấu hình rclone ngoài repo; không chia sẻ token qua Git. `storage.json` không chứa token/mật khẩu và có thể chia sẻ với teammate.

### Chuyển 10.000 mẫu hiện có lên Google Drive

Ngày 11/10/2026: batch 10.000 mẫu (~277,1 MiB) đã upload và đọc lại để xác minh SHA-256 toàn bộ payload/manifest; dữ liệu local vẫn giữ nguyên. Thư mục chung: [MedXIR trên Google Drive](https://drive.google.com/drive/folders/1tfvL8OJIRejT_c-4wfaQnTZecT67qJld). Remote báo quota 400 GiB, khoảng 371 GiB trống trước lần upload này.

Sau khi sửa parser, đã upload thêm batch `parser-v2.1` (~278,4 MiB), cũng xác minh checksum và giữ local. Đã upload và xác minh thêm batch `parser-v2.2` cho cùng 10.000 ID. Ba batch là ba phiên bản của cùng 10.000 ID; `export-remote` lấy bản mới hơn. Chi tiết batch và kết quả ở [PROGRESS.md](PROGRESS.md#11-parser-v21-và-hướng-dẫn-cli--11102026).

```bash
# Đóng batch local, không cần đăng nhập; không xóa raw.
uv run python scripts/storage.py pack

# Upload, đọc lại byte để đối chiếu SHA-256, rồi dọn raw/batch local.
uv run python scripts/storage.py upload --prune-local

# Đọc trạng thái batch, không cần mạng.
uv run python scripts/storage.py status
```

Upload lỗi thì giữ dữ liệu local. Chạy lại `upload` thử lại batch chưa xác minh; file đã upload được tái sử dụng. `--prune-local` là tùy chọn: không bật thì giữ bản local đầy đủ. Việc đối chiếu SHA-256 đọc lại dữ liệu từ Google Drive, nên có thêm băng thông download. Không coi việc chỉ khớp kích thước là đủ để xóa raw.

Sau dọn, SQLite giữ URL, trạng thái, cảnh báo, locator và thông tin nguồn; document đầy đủ nằm trong batch. SQLite tái sử dụng các trang trống, nhưng kích thước file không tự giảm ngay. Khi crawler đã dừng, có thể thu hồi phần trống:

```bash
uv run python -c 'import sqlite3; db=sqlite3.connect("artifacts/crawl/shard-000-of-001/state.sqlite"); db.execute("VACUUM"); db.close()'
```

Các file `pages.jsonl`/`documents.jsonl` đã xuất trước đây được giữ lại. Có thể xóa các bản xuất này sau khi bạn đã upload thành công; chúng là bản xuất, không phải checkpoint chính.

### Crawl tiếp có upload cuốn chiếu

```bash
uv run python scripts/crawl.py --resume \
  --storage-config storage.json --prune-local \
  --workers 32 --host-workers 1 --delay 2
```

Mặc định đóng batch khi đạt **10.000 document hoặc 256 MiB raw đã nén**; batch cuối được đóng khi chạy xong. Một luồng upload riêng hoạt động cùng crawler; không tự khởi chạy crawler nền. `--batch-docs`/`--batch-mb` đổi ngưỡng. Upload lỗi thì dừng crawler và giữ checkpoint; resume sẽ thử lại. `--cache-limit-gb 4` là mốc dừng cho raw/batch local, kiểm tra giữa các vòng upload và mỗi 100 URL; không phải giới hạn cứng toàn bộ dung lượng repo. Cần chừa chỗ cho batch đang đóng, request đang chạy và SQLite.

Trong chế độ lưu cloud, cuối lần chạy chỉ cập nhật summary, không tự dựng lại hai file JSONL lớn. Muốn xuất đầy đủ, dùng `--export-only --storage-config storage.json`; `--review-limit` và `--reparse` cũng đọc được batch đã dọn local. Ctrl+C giữ checkpoint; nếu upload đang chạy, việc kết thúc có thể chờ thao tác lưu trữ hiện tại.

Hai máy dùng các cờ tương tự, cùng plan, **khác shard**:

```bash
# Chạy một lần trên máy A trước khi hai máy bắt đầu.
# Google Drive cho phép thư mục trùng tên, nên chuẩn bị các thư mục chung tuần tự.
uv run python scripts/storage.py prepare --shard-count 2

# Máy A
uv run python scripts/crawl.py --shard-count 2 --shard-id 0 \
  --storage-config storage.json --prune-local --workers 32 --host-workers 1 --delay 2

# Máy B
uv run python scripts/crawl.py --shard-count 2 --shard-id 1 \
  --storage-config storage.json --prune-local --workers 32 --host-workers 1 --delay 2
```

Trên Google Drive:

```text
MedXIR/runs/<SHA-256-plan>/shard-000-of-002/batches/<UUID>/
├── raw.tar                 # Chứa các raw/*.gz; không nén gzip lần nữa
├── pages.jsonl.gz          # Nguồn, ID, URL và digest raw
├── documents.jsonl.gz      # Toàn bộ kết quả parse, gồm block/QA/cảnh báo/audit
└── manifest.json           # Chỉ xuất hiện sau khi payload đã xác minh
```

UUID ngăn ghi đè batch giữa các máy; `doc_id % shard_count` chia việc. Hai máy chạy cùng shard sẽ vẫn crawl trùng, nên phải phân shard đúng. Số shard là cấu hình cố định của mỗi plan/checkpoint; đợt 10.000 hiện tại vẫn là một shard.

### Đọc để chunking/indexing

Không cần giải nén hàng triệu file ra đĩa. Đọc JSONL gzip tuần tự; chunk từ `blocks`/`answers`, giữ `doc_id` và cảnh báo. Raw chỉ cần tải lại khi sửa parser.

```bash
# Xuất kết quả hiện tại của một shard, kể cả document đã dọn local.
uv run python scripts/storage.py documents --destination documents.jsonl.gz

# Khôi phục một response gốc. Tải một raw.tar cần thiết, đọc member trong tar.
uv run python scripts/storage.py raw --doc-id 911500 --destination page.html

# Máy làm indexing không cần SQLite của các máy crawl: đọc tất cả shard của một plan.
sha256sum artifacts/crawl/plan.jsonl
uv run python scripts/storage.py export-remote \
  --plan-sha <SHA256-vừa-in> --destination corpus.jsonl.gz
```

`export-remote` chỉ tải `documents.jsonl.gz`, kiểm tra digest, đọc từng batch và bỏ file cache. Nếu parse lại tạo nhiều phiên bản một ID, chọn batch có thời gian tạo mới nhất; các máy cần đồng bộ đồng hồ. File tổng xuất ra vẫn chiếm đĩa. Checkpoint và plan local nên giữ lại; bản V1 chưa tự backup/khôi phục toàn bộ SQLite từ cloud.

`raw.tar` chứa từng response `.gz` riêng; không cần giải nén toàn bộ corpus để chunk/index. Dùng `documents` hoặc `export-remote` để đọc nội dung đã parse; chỉ dùng `raw`/`--reparse` khi cần xử lý lại nguồn. Reparse tạo batch mới, giữ batch cũ trên cloud; batch mới hiện vẫn chứa raw nên cần tính phần dung lượng tăng thêm.

### Tùy chọn script storage

`uv run python scripts/storage.py --help` hiển thị đầy đủ trợ giúp. Các action: `init` kiểm tra/tạo đích dùng chung; `prepare` chuẩn bị thư mục shard; `pack` đóng batch local; `upload` upload/xác minh; `status` xem batch; `documents` xuất kết quả một checkpoint; `raw` lấy response gốc; `export-remote` hợp mọi shard của một plan.

| Tùy chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `--root` | `artifacts/crawl/shard-000-of-001` | Thư mục checkpoint của action pack/upload/status/documents/raw; với full crawl, truyền `artifacts/crawl_full/shard-000-of-002` hoặc shard 1 tương ứng |
| `--config` | `storage.json` | Cấu hình đích chung; tương ứng `--storage-config` của crawler |
| `--remote` | `gdrive:MedXIR` | Đích khi init lần đầu; cấu hình đã tồn tại được kiểm tra lại |
| `--plan` | `artifacts/crawl/plan.jsonl` | Plan dùng cho prepare |
| `--shard-count` | `1` | Số shard dùng cho prepare |
| `--batch-docs` / `--batch-mb` | `10000` / `256` | Ngưỡng đóng batch khi pack/upload |
| `--prune-local` | Tắt | Dọn local sau upload/xác minh; không dùng với pack |
| `--doc-id` | Không có | ID cần lấy với raw |
| `--destination` | Không có | File đầu ra của documents/raw/export-remote |
| `--plan-sha` | Không có | SHA-256 đầy đủ của plan khi export-remote |

Ví dụ upload thủ công shard 0 của full crawl, sau khi crawler đã dừng:

```bash
uv run python scripts/storage.py upload \
  --root artifacts/crawl_full/shard-000-of-002 --config storage.json --prune-local
```

### Tăng tốc crawler

`--workers` giới hạn request tổng; `--host-workers` giới hạn request đang tải theo hostname. Khoảng cách bắt đầu request vẫn do `--delay` chi phối, kể cả redirect/retry. Tăng worker tổng sẽ ít tác dụng khi hàng chờ chỉ còn vài host.

Bắt đầu **32 worker tổng, 1/nhóm nguồn, delay 2 giây**. Đợt 1.000 URL mới đã chạy với 32 worker; cấu hình 2/host trước đó gặp CAPTCHA ở nhóm 39.net. Đã sửa giới hạn chung cho subdomain và hoàn tất các URL còn pending với 1/nhóm, delay 2. Tăng lên 48–64 tổng chỉ khi có nhiều nguồn hoạt động và tỷ lệ lỗi không tăng; không tăng đồng loạt worker theo nguồn.

Với nhiều máy, delay nhân theo số shard; không bảo đảm giới hạn tổng chính xác giữa các máy. Riêng 963.438 URL CNKang ở 2 giây/request có cận dưới **22,3 ngày**, chưa tính retry, redirect và latency. Tăng worker không vượt được cận này. Chưa có số đo tốc độ ổn định trong nhiều ngày.

## Chạy toàn bộ corpus

**Đủ để bắt đầu crawl có kiểm soát**, với raw lưu lại, checkpoint/resume, gate tự tạm dừng nguồn và upload có checksum. Chưa thể xác nhận lấy đủ nội dung cả 4.394.718 URL: 85 host đã quan sát được HTML nội dung (3.632.818 URL trong corpus); 12 host còn bị chặn/lỗi truy cập (761.900 URL). Nhóm 39.net có thể xuất hiện CAPTCHA theo thời điểm. Không đưa trang lỗi hoặc payload chưa parse vào dữ liệu indexing.

Plan hiện đã tạo tại `artifacts/crawl_full/plan.jsonl`: **4.381.984 URL còn lại**, sau khi loại hợp ID đã thử ở pilot và các checkpoint sau đây. `--all-hosts` giữ cả nguồn đang bị chặn để crawler ghi nhận và tạm dừng khi cần. Nó không làm nguồn trở nên truy cập được. Chưa khởi chạy full crawl.

```bash
# Tạo plan trên máy có các kết quả trước đó; existing plan được tái sử dụng.
uv run python scripts/crawl.py --prepare-only --limit 0 --all-hosts \
  --exclude-completed artifacts/crawl/shard-000-of-001/state.sqlite \
  --exclude-completed artifacts/readiness_check/shard-000-of-001/state.sqlite \
  --exclude-completed artifacts/readiness_check/reopened/shard-000-of-001/state.sqlite \
  --plan artifacts/crawl_full/plan.jsonl --output artifacts/crawl_full

# Chốt 2 shard: chuẩn bị thư mục Drive một lần, rồi copy cùng plan/storage.json.
uv run python scripts/storage.py prepare --plan artifacts/crawl_full/plan.jsonl --shard-count 2
sha256sum artifacts/crawl_full/plan.jsonl

# Máy A: chạy trực tiếp để xem tiến độ.
uv run python scripts/crawl.py \
  --plan artifacts/crawl_full/plan.jsonl --output artifacts/crawl_full \
  --shard-count 2 --shard-id 0 --workers 32 --host-workers 1 --delay 2 \
  --storage-config storage.json --prune-local --progress-every 100

# Máy B: cùng lệnh, chỉ đổi --shard-id 0 thành --shard-id 1.
# Kiểm tra tiến độ máy A, không crawl thêm.
uv run python scripts/crawl.py --status \
  --plan artifacts/crawl_full/plan.jsonl --output artifacts/crawl_full \
  --shard-count 2 --shard-id 0
```

Chạy lại lệnh máy A/B với `--resume` để tiếp tục. Nguồn đã pause được giữ qua lần chạy; sau khi kiểm tra lỗi và chờ nguồn hồi phục, thêm `--retry-paused` để thử **URL pending**. Các URL đã hoàn thành với trạng thái lỗi không tự retry. Khi còn pending do pause, crawler xuất kết quả và trả exit code 2; xem summary/status để xử lý, không lặp retry liên tục.

Hai máy kiểm tra SHA-256 plan giống nhau và phải có cùng code parser. Mỗi shard chỉ có một tiến trình crawler; checkpoint nằm riêng từng máy. Sao lưu plan và SQLite khi crawler đã dừng; bản hiện tại chưa tự khôi phục checkpoint từ Drive. Nếu chỉ chạy một máy, chốt `--shard-count 1` từ đầu và chuẩn bị thư mục Drive tương ứng; không đổi số shard trong checkpoint đang dùng.

**Dung lượng:** ước lượng có trọng số theo host từ mẫu hiện có khoảng 57,5 GiB raw gzip cho 3,55 triệu URL của 83 host ban đầu; overhead tar có thể thêm khoảng 6,8 GiB. Chưa gồm document/pages, 80.023 URL ở hai nguồn vừa mở lại, nguồn đang bị chặn hoặc các phiên bản reparse. Đây là dự toán, cần đo lại sau các batch đầu. Drive hiện báo quota 400 GiB, không phải 5 TB.

**Dữ liệu giai đoạn sau:** full plan đã loại ID cũ, nên export cloud của riêng full plan sẽ không chứa toàn bộ dữ liệu trước đó. Kết hợp kết quả pilot, 10.000 mẫu, 1.000 mẫu kiểm chứng, 40 mẫu nguồn mở lại và full crawl theo `doc_id`; chọn phiên bản mới nhất, giữ provenance. Hai đợt kiểm chứng mới hiện còn local, chưa upload. Chỉ chunk các bản có nội dung phù hợp; phân trang chưa tải, PDF/ảnh/audio và QA thiếu đáp án vẫn có cảnh báo để xử lý riêng.

Kiểm tra offline các cơ chế giữ dữ liệu, chia shard và tạm dừng:

```bash
uv run python -m unittest discover -s tests -v
```


## Crawl lại corpus 15/10/2026 (nhiều máy)

Đây là lần crawl hiện hành, dùng plan theo ngôn ngữ trong `artifacts/recrawl_20261015/`. Chi tiết và lý do từng bước: [RECRAWL_RUNBOOK.md](docs/RECRAWL_RUNBOOK.md). Phần "Chạy toàn bộ corpus" phía trên là lịch sử của corpus cũ.

Phân công: máy chính chạy **shard 0**, teammate chạy **shard 1**. Mỗi người có thể dùng nhiều máy (công ty, nhà) để tiếp tục tiến độ của nhau, với quy tắc:

- **Tại mọi thời điểm chỉ một máy được chạy một shard.** Hai máy cùng shard ID sẽ ghi đè checkpoint.
- Khi đổi máy: máy cũ dừng hoàn toàn, **publish** checkpoint; máy mới **fetch** checkpoint rồi mới resume. Không chạy crawl trước khi fetch, nếu không máy mới sẽ tạo checkpoint trống và lệnh fetch từ chối ghi đè.
- Plan, storage config và parser phải cùng phiên bản trên mọi máy. Kiểm SHA-256 plan trước khi chạy.

### Lệnh thường dùng

```bash
# Một lần, máy điều phối: tạo plan và thư mục Drive
uv run python scripts/prepare_recrawl.py --prepare-cloud

# Chạy thử 300 URL/ngôn ngữ, giữ raw local
uv run python scripts/run_recrawl.py --shard-id 0 --max-new 300 --batch-docs 200 --keep-local

# Chạy dài shard 0 (máy chính)
uv run python scripts/run_recrawl.py --shard-id 0

# Chạy dài shard 1 (máy teammate)
uv run python scripts/run_recrawl.py --shard-id 1 --download

# Xem tiến độ, không crawl thêm
uv run python scripts/crawl.py --status --output artifacts/recrawl_20261015/vi --shard-count 2 --shard-id 0

# Xuất danh sách lỗi/cần review
uv run python scripts/view_followup.py --export artifacts/recrawl_20261015/failed_or_review.csv
```

### Đổi máy cho một shard

Ví dụ chuyển ZH shard 1 từ máy công ty sang máy nhà:

```bash
# 1. Máy công ty: Ctrl+C, chờ thoát hẳn, rồi publish checkpoint
uv run python scripts/checkpoint_handoff.py publish \
  --root artifacts/recrawl_20261015/zh/shard-001-of-002 \
  --config artifacts/recrawl_20261015/storage.zh.json \
  --plan artifacts/recrawl_20261015/zh/plan.jsonl \
  --shard-count 2 --shard-id 1 --prune-local

# 2. Máy nhà: lấy plan/config, fetch checkpoint, rồi resume
uv run python scripts/run_recrawl.py --shard-id 1 --download --download-only
uv run python scripts/checkpoint_handoff.py fetch \
  --root artifacts/recrawl_20261015/zh/shard-001-of-002 \
  --config artifacts/recrawl_20261015/storage.zh.json \
  --plan artifacts/recrawl_20261015/zh/plan.jsonl \
  --shard-count 2 --shard-id 1
uv run python scripts/run_recrawl.py --shard-id 1
```

Lặp lại theo từng ngôn ngữ đang chạy (`vi`, `zh`). EN hiện hoãn hoàn toàn, không có lệnh crawl EN.

Exit code: `0` xong; `2` còn pending/hoãn, chạy lại để tiếp tục; `1` lỗi cần đọc log, upload hoặc cache trong `artifacts/recrawl_20261015/logs/`. Chỉ coi một đợt là xong khi exit là `0` hoặc `2` và upload đã được xác minh.

Không tự retry các URL đã có kết quả lỗi. Reparse sau khi sửa parser chỉ chạy khi crawler của shard/ngôn ngữ đó đã dừng; xem mục "Danh sách cần quay lại xử lý" trong RECRAWL_RUNBOOK.

## Tài liệu tham chiếu trong repo

- [Đề bài](OVERVIEW.md)
- [Dữ liệu](docs/DATA.md)
- [Cách đánh giá](docs/EVALUATION.md)
- [Định dạng nộp bài](docs/SUBMISSION.md)
- [Template và ngoại lệ theo host](docs/HOST_TEMPLATES.md)
- [Runbook crawl lại corpus 15/10/2026](docs/RECRAWL_RUNBOOK.md)
- [Runbook thu thập và parse trên hai máy (corpus cũ)](docs/CRAWL_RUNBOOK.md)
- [Giải pháp V1](PROPOSED_SOLUTION.md)
- [Tiến trình](PROGRESS.md)
