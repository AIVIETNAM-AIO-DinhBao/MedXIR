# Hướng dẫn chạy thu thập và parse dữ liệu

Runbook cho hai máy dùng chung Google Drive, cùng một plan và parser. Chạy từ thư mục gốc repo. Crawler tự parse mỗi trang ngay sau khi tải; raw được giữ để có thể sửa parser và parse lại.

## Phạm vi và nguyên tắc

- `doc_id` là ID gốc trong `dataset/links_corpus.parquet`; phân shard theo `doc_id % 2`.
- Plan full hiện tại có **4.381.984 URL**. Nó loại **12.734 ID đã được thử/hoàn tất trong các đợt trước**. Một ID đã thử không có nghĩa là đã lấy được bài. Khi đánh giá độ phủ cuối cùng, phải đối chiếu cả pilot, 10.000 mẫu, 1.000 mẫu kiểm chứng, 40 URL mở lại và hai shard full theo `doc_id`.
- Crawler ghi nhận lỗi và cảnh báo riêng. Challenge, 404, redirect về trang chủ, body rỗng, lỗi parser, template chưa biết và kết quả thiếu text đều không được tính là nội dung thành công.
- Hai máy chạy độc lập, không có bộ điều phối tốc độ trung tâm. Cấu hình dưới đây giới hạn 1 request đồng thời mỗi nhóm nguồn trên mỗi máy; các website vẫn có thể chặn hoặc CAPTCHA.

## 1. Chuẩn bị máy A và máy B

Trên **cả hai máy**, lấy cùng commit code và cài thư viện:

```bash
git pull
uv sync
```

Cần Python 3.12 trở lên, `uv`, `rclone` và quyền ghi vào cùng thư mục Google Drive. Cấu hình rclone trên từng máy, đặt tên remote là `gdrive`, rồi đăng nhập tài khoản được chia sẻ quyền với thư mục MedXIR:

```bash
rclone config
rclone lsd gdrive:
```

Repo có `storage.json` chứa remote và ID thư mục dùng chung, không chứa credential. Giữ nguyên file này. Không tạo một thư mục MedXIR riêng trên máy B. Xác nhận kết nối và chuẩn bị thư mục cloud cho hai shard chỉ một lần, từ máy A:

```bash
uv run python scripts/storage.py init --remote gdrive:MedXIR
uv run python scripts/storage.py prepare \
  --plan artifacts/crawl_full/plan.jsonl --shard-count 2
```

`init` sẽ kiểm tra `storage.json` đã có khớp với remote. Lệnh `prepare` cần plan hiện tại và tạo các thư mục cloud mang SHA-256 của plan. Nếu chạy từ repo sạch mà chưa có plan, tạo theo lệnh ở mục 2 trước.

## 2. Chia sẻ cùng một plan cho hai máy

Plan nằm trong `artifacts/` nên không được Git theo dõi. Máy A đã có plan tại `artifacts/crawl_full/plan.jsonl`. Đưa plan lên thư mục coordination trong Drive. ID thư mục gốc đã được ghim trong `storage.json`; dùng cùng ID khi truy cập từ remote rclone ở cả hai máy:

```bash
rclone mkdir 'gdrive,root_folder_id=1tfvL8OJIRejT_c-4wfaQnTZecT67qJld:/coordination'
rclone copyto artifacts/crawl_full/plan.jsonl \
  'gdrive,root_folder_id=1tfvL8OJIRejT_c-4wfaQnTZecT67qJld:/coordination/crawl_full_plan.jsonl'
sha256sum artifacts/crawl_full/plan.jsonl
```

Trên máy B, từ thư mục gốc repo:

```bash
mkdir -p artifacts/crawl_full
rclone copyto \
  'gdrive,root_folder_id=1tfvL8OJIRejT_c-4wfaQnTZecT67qJld:/coordination/crawl_full_plan.jsonl' \
  artifacts/crawl_full/plan.jsonl
sha256sum artifacts/crawl_full/plan.jsonl
```

So sánh SHA-256 máy A/B; phải trùng nhau. Không tự tạo lại plan trên máy B. Nếu plan được tạo mới, upload và phân phối plan đó trước; chạy lại `storage.py prepare` với plan mới.

## 3. Chạy hai shard

Mỗi máy có thư mục checkpoint riêng. Các lệnh chạy foreground để theo dõi tiến độ; `tee` vừa hiện log trong terminal vừa lưu log vào file. `set -o pipefail` giữ mã lỗi của crawler khi output được pipe qua `tee`.

**Máy A — shard 0:**

```bash
mkdir -p artifacts/crawl_full/logs
set -o pipefail
uv run python scripts/crawl.py \
  --plan artifacts/crawl_full/plan.jsonl \
  --output artifacts/crawl_full \
  --shard-count 2 --shard-id 0 \
  --workers 16 --host-workers 1 --delay 2 \
  --storage-config storage.json --prune-local \
  --batch-docs 10000 --batch-mb 256 --cache-limit-gb 4 \
  --progress-every 100 \
  2>&1 | tee -a artifacts/crawl_full/logs/shard-0.log
```

**Máy B — shard 1:**

```bash
mkdir -p artifacts/crawl_full/logs
set -o pipefail
uv run python scripts/crawl.py \
  --plan artifacts/crawl_full/plan.jsonl \
  --output artifacts/crawl_full \
  --shard-count 2 --shard-id 1 \
  --workers 16 --host-workers 1 --delay 2 \
  --storage-config storage.json --prune-local \
  --batch-docs 10000 --batch-mb 256 --cache-limit-gb 4 \
  --progress-every 100 \
  2>&1 | tee -a artifacts/crawl_full/logs/shard-1.log
```

Nếu máy yếu, đổi `--workers 16` thành `--workers 8`; không tăng `--host-workers` khi chưa quan sát ổn định. Do hai shard tự giảm tốc theo cấu hình shard, đây không phải bộ giới hạn tổng giữa hai máy. Không chạy hai crawler cùng `shard-id`.

Crawler parse ngay sau mỗi response. Batch được đóng khi đạt 10.000 document hoặc 256 MiB raw nén. `--prune-local` chỉ xóa raw và batch local **sau khi upload và xác minh SHA-256 thành công**; SQLite checkpoint vẫn giữ URL, trạng thái, cảnh báo và locator trên Drive. Nếu upload lỗi, dữ liệu local được giữ. Mốc cache 4 GiB khiến crawler dừng nếu batch chưa được upload kịp.

## 4. Theo dõi, dừng và resume

Xem tiến độ shard 0 (trên máy A):

```bash
uv run python scripts/crawl.py --status \
  --plan artifacts/crawl_full/plan.jsonl --output artifacts/crawl_full \
  --shard-count 2 --shard-id 0
```

Máy B đổi `--shard-id 0` thành `--shard-id 1`. `--status` chỉ đọc SQLite và có thể chạy trong khi crawler hoạt động. Dừng bằng `Ctrl+C`; chạy lại đúng lệnh crawl và thêm `--resume` để tiếp tục. Plan, shard count và shard ID phải giữ nguyên.

Nếu host bị pause, xem lý do trong `--status` và log. Sau khi đã xác minh nguồn hồi phục, resume các URL **đang pending**:

```bash
uv run python scripts/crawl.py --resume --retry-paused \
  --plan artifacts/crawl_full/plan.jsonl --output artifacts/crawl_full \
  --shard-count 2 --shard-id 0 \
  --workers 16 --host-workers 1 --delay 2 \
  --storage-config storage.json --prune-local --progress-every 100
```

Máy B thay shard ID bằng 1. `--retry-paused` không tải lại URL đã ghi kết quả hoàn tất, kể cả kết quả HTTP lỗi/challenge. Nếu crawler kết thúc với exit code 2, còn URL pending do host/parser bị pause; đọc status/log trước khi quyết định retry.

## 5. Log riêng các URL chưa thành công hoặc cần duyệt

SQLite là checkpoint có thẩm quyền; log terminal giúp xem sự kiện theo thời gian. Sau khi dừng mỗi crawler, xuất danh sách URL cần xử lý thành CSV. Lệnh sau đọc checkpoint shard 0; chạy tương tự trên máy B với `SHARD_ID=1`:

```bash
SHARD_ID=0 uv run python - <<'PY'
import csv
import json
import os
import sqlite3
from pathlib import Path

shard_id = int(os.environ['SHARD_ID'])
root = Path(f'artifacts/crawl_full/shard-{shard_id:03d}-of-002')
output = root / 'needs_review.csv'
with sqlite3.connect(root / 'state.sqlite') as db, output.open('w', newline='', encoding='utf-8') as file:
    writer = csv.DictWriter(file, fieldnames=[
        'doc_id', 'url', 'host', 'fetch_state', 'http_status',
        'parse_status', 'has_text', 'warnings', 'error'
    ])
    writer.writeheader()
    for doc_id, url, host, page_json, document_json in db.execute(
        'SELECT doc_id,url,host,page,document FROM jobs ORDER BY doc_id'
    ):
        if page_json is None:
            writer.writerow(dict(doc_id=doc_id, url=url, host=host,
                                 fetch_state='pending', http_status='',
                                 parse_status='', has_text='', warnings='',
                                 error='not_crawled'))
            continue
        page = json.loads(page_json)
        document = json.loads(document_json) if document_json else {}
        has_text = document.get('has_text', bool(document.get('text', '').strip()))
        warnings = document.get('warnings', [])
        if (page.get('state') != 'html_response' or not has_text
                or document.get('status') in {'unknown_template', 'encoded_content',
                                               'parser_error', 'empty_content', 'source_error'}
                or warnings):
            writer.writerow(dict(doc_id=doc_id, url=url, host=host,
                fetch_state=page.get('state'), http_status=page.get('http_status'),
                parse_status=document.get('status'), has_text=has_text,
                warnings=' | '.join(warnings), error=page.get('error', '')))
print(f'Wrote {output}')
PY
```

File CSV gồm URL chưa crawl, HTTP error/challenge, body không parse thành text, parser status lỗi và document có cảnh báo (ví dụ phân trang/media/thiếu đáp án). Khi `--prune-local` đã dọn dữ liệu, `has_text`, status và warnings vẫn có trong checkpoint compact; raw có thể đọc lại từ Drive để parse sau.

Chạy thống kê gọn bất kỳ lúc nào bằng `--status`; file CSV chi tiết nên xuất sau khi crawler dừng để có snapshot ổn định. Giữ CSV và log cùng plan SHA để biết chúng thuộc lần chạy nào.

## 6. Parse lại khi sửa parser

Crawl và parse ban đầu đã diễn ra trong cùng lệnh. Nếu phát hiện template mới, cập nhật parser trên cả hai máy, dừng crawler rồi chạy reparse riêng trên từng shard. Reparse đọc raw local hoặc tải raw cần thiết từ Drive; không gửi request website:

```bash
uv run python scripts/crawl.py --reparse --parse-workers 4 \
  --plan artifacts/crawl_full/plan.jsonl --output artifacts/crawl_full \
  --shard-count 2 --shard-id 0 \
  --storage-config storage.json --progress-every 100
```

Máy B đổi shard ID thành 1. Mặc định chỉ parse document chưa dùng parser version hiện tại. Nếu vừa sửa rule nhưng chưa bump version, thêm `--force-reparse`. Sau reparse, upload kết quả mới; muốn dọn batch local mới sau khi xác minh, dùng `--prune-local`:

```bash
uv run python scripts/crawl.py --reparse --force-reparse --parse-workers 4 \
  --plan artifacts/crawl_full/plan.jsonl --output artifacts/crawl_full \
  --shard-count 2 --shard-id 0 \
  --storage-config storage.json --prune-local --progress-every 100
```

Xuất lại `needs_review.csv` sau mỗi lần sửa parser. Những parse status như `needs_review` hoặc cảnh báo pagination/media cần duyệt theo nguyên nhân; tự reparse cùng raw không làm nội dung nguồn xuất hiện.

## 7. Tiêu chí kết thúc và giới hạn hiện tại

Không dùng riêng số `completed` làm tiêu chí đầy đủ. Đối chiếu kết quả hai shard và các đợt pilot/kiểm chứng theo `doc_id`; phân riêng:

1. URL có nội dung HTML, parse được, không còn cảnh báo cần xử lý.
2. URL có text nhưng có cảnh báo, phân trang, nội dung media hoặc QA thiếu câu trả lời.
3. URL chưa crawl, lỗi mạng/HTTP, challenge, redirect về trang chủ hoặc response rỗng.
4. URL có raw nhưng template/parser chưa trích được nội dung.

Một lượt crawl hoàn tất chỉ có nghĩa là mọi URL trong plan đã có kết quả được ghi. Plan full hiện loại các ID đã thử trước đây; danh sách lỗi lịch sử phải được nhập vào báo cáo độ phủ và đưa vào một kế hoạch retry riêng sau khi nguồn/chiến lược truy cập được xác minh. Crawler hiện chưa có cờ retry chọn lọc những URL đã hoàn tất với trạng thái lỗi. Không retry 403/CAPTCHA liên tục; nguồn bị chặn cần điều tra riêng.

Danh mục host/template và kết quả kiểm chứng: [HOST_TEMPLATES.md](HOST_TEMPLATES.md). Trạng thái triển khai và số liệu gần nhất: [PROGRESS.md](../PROGRESS.md). CLI đầy đủ: `uv run python scripts/crawl.py --help` và `uv run python scripts/storage.py --help`.
