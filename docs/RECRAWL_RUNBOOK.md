# Crawl lại corpus hiện hành — 15/10/2026

Đây là hướng dẫn cho lần crawl mới sau khi kho cũ được dọn. Runbook cũ mô tả corpus 4,4 triệu URL chỉ còn giá trị lịch sử. Crawler tải và parse ngay, upload raw và document theo batch; tiến độ/thất bại được lưu theo ID chính thức.

## Phạm vi đã chốt

Corpus `dataset/links_corpus_vi_en_zh.parquet`, SHA-256 `87e44a792d7a99fb0fde5dff7f09f1a38a0a1fdf02ddd1ac93a005736e00b0a7`, gồm 1.220.598 ID duy nhất. Mapping host/ngôn ngữ đã lưu tại `configs/host_languages.json`; đây là nhãn theo nguồn, chưa phải nhận diện ngôn ngữ từng bài.

| Ngôn ngữ | Tổng ID | Được mở crawl ban đầu | Hoãn |
| --- | ---: | ---: | ---: |
| VI | 740.598 | 703.983 | 36.615 |
| EN | 240.000 | 0 | 240.000 |
| ZH | 240.000 | 107.633 | 132.367 |
| Tổng | 1.220.598 | 811.616 | 408.982 |

Không loại bỏ ID hoãn: chúng có trong plan, checkpoint và báo cáo follow-up. Policy nằm tại `configs/recrawl_policy.json`. Nguồn bị chặn trong pilot và PubMed chưa có parser được hoãn; không thử lại liên tục. Tỷ lệ có text của pilot không phải cam kết >85% URL toàn corpus sẽ đầy đủ.

`parser-v2.3` thêm cảnh báo text quá ngắn và bài nhiều ảnh nhưng ít text. Trang có text/cảnh báo vẫn giữ nguyên raw, không coi là đã duyệt đủ nội dung.

## Chuẩn bị một lần trên máy điều phối

```bash
uv sync
uv run python scripts/prepare_recrawl.py --prepare-cloud
```

Script kiểm hash corpus, ID/URL trùng, mapping host chưa biết; tạo đủ ba plan, không loại ID theo pilot/checkpoint cũ. Plan đã tồn tại mà khác nội dung bị từ chối. Kết quả tại `artifacts/recrawl_20261015/`:

- `manifest.json`: hash plan, tổng ID, số shard, số hoãn.
- `vi/plan.jsonl`, `en/plan.jsonl`, `zh/plan.jsonl`.
- `storage.vi.json`, `storage.en.json`, `storage.zh.json`: ghim folder ID con thực tế, không có token.

`--prepare-cloud` dùng root `storage.json`, yêu cầu mỗi thư mục ngôn ngữ chỉ có một bản. Chuẩn bị trước `runs/<plan_sha>/shard-.../batches` cho cả hai shard, rồi upload plan/config/manifest vào `MedXIR/coordination/recrawl_20261015/`. Chỉ một máy thực hiện để tránh Drive tạo folder trùng tên.

## Chạy hai máy

Mỗi máy cần cùng phiên bản code, Python 3.12+, uv, rclone remote `gdrive` đăng nhập tài khoản có quyền ghi cùng folder. Không chia sẻ rclone token. Gói source đã chụp cho lần chạy có thể được lưu trong folder coordination; đọc `source.json` để lấy tên và SHA-256.

Máy A phụ trách shard 0, máy B phụ trách shard 1. Mỗi ngôn ngữ chia `doc_id % 2`; plan giữ nguyên sau khi bắt đầu. Không chạy hai máy cùng shard ID.

Đợt kiểm tra đầu (đã dùng trên máy A):

```bash
uv run python scripts/run_recrawl.py --shard-id 0 \
  --max-new 300 --batch-docs 200 --keep-local
```

Lệnh này xử lý tối đa 300 URL cho mỗi ngôn ngữ đang mở, upload và xác minh batch; giữ raw local để khôi phục thử. EN chỉ xuất báo cáo hoãn. Exit 2 là còn pending, đúng với giới hạn thử.

Sau khi xác minh upload/restore, chạy dài trên máy A:

```bash
uv run python scripts/run_recrawl.py --shard-id 0
```

Máy B tải cùng plan/config đã chia sẻ, kiểm SHA-256 và chạy shard 1:

```bash
uv run python scripts/run_recrawl.py --shard-id 1 --download
```

Runner tự resume nếu checkpoint đã có. Mặc định tổng ngân sách 16 HTTP worker/máy, chia 8 cho VI và 8 cho ZH; EN hoãn không dùng worker HTTP. Mỗi nhóm nguồn 1 request đồng thời/máy, delay 2 giây được nhân với 2 shard thành 4 giây/máy. Nhóm 39.net, familydoctor.com.cn, iiyi.com, NCBI dùng rate limit chung giữa subdomain trên cùng máy. Chưa có limiter tập trung giữa hai máy.

Tăng worker chỉ sau khi quan sát timeout, chặn và CPU. Parser chạy trong thread khi crawl; `--parse-workers` chỉ điều khiển process khi reparse riêng.

Khi số URL còn lại dồn vào ít rate group, `--workers` tổng không làm nhanh hơn. Có thể truyền
`--rate-limits CONFIG.json` để chỉ tăng song song cho một group đã thử nghiệm. Ví dụ thử ZH tại
`configs/zh_rate_limits_trial.json` giữ toàn bộ `39.net` ở một request nhưng cho
`www.cnkang.com` hai response đồng thời; delay khởi tạo request vẫn là 0,5 giây.

## Upload và giữ dữ liệu

Cấu trúc cho mỗi ngôn ngữ:

```text
MedXIR/vi/runs/<plan_sha>/shard-000-of-002/
  batches/<batch_uuid>/
    raw.tar
    pages.jsonl.gz
    documents.jsonl.gz
    manifest.json
  followup/
    documents.csv.gz
    retry_fetch.jsonl.gz
    reparse.jsonl.gz
    summary.json
```

Mặc định đóng batch khi đạt 2.000 kết quả hoặc 128 MiB raw gzip. Uploader chạy cùng crawler, đọc lại toàn bộ payload để kiểm SHA-256, rồi upload manifest hoàn tất. `--prune-local` được runner bật mặc định: chỉ dọn raw/batch đã xác minh, giữ SQLite và locator. `--keep-local` tắt việc dọn.

Cache raw/batch có mốc dừng 4 GiB; có thể vượt tạm thời và không bao gồm SQLite/report/log. Upload lỗi giữ dữ liệu local, runner báo lỗi; không tự lặp vô hạn. Giữ nguyên `state.sqlite`, WAL liên quan, plan và storage config để resume. Checkpoint SQLite hiện giữ local, chưa có backup định kỳ lên Drive; follow-up và batch cloud giúp kiểm kê/khôi phục dữ liệu nhưng không thay thế một checkpoint resume đầy đủ.

### Bàn giao một shard sang máy khác

Chỉ một máy được chạy một shard tại một thời điểm. Sau khi máy cũ dừng crawler hoàn toàn, lệnh
`checkpoint_handoff.py publish` đóng/upload/xác minh mọi batch chưa đóng, tạo SQLite backup độc
lập với WAL, rồi đưa snapshot và manifest SHA-256 vào Drive. Ví dụ bàn giao ZH shard 1:

```bash
uv run python scripts/checkpoint_handoff.py publish \
  --root artifacts/recrawl_20261015/zh/shard-001-of-002 \
  --config artifacts/recrawl_20261015/storage.zh.json \
  --plan artifacts/recrawl_20261015/zh/plan.jsonl \
  --shard-count 2 --shard-id 1 --prune-local
```

Máy nhận tải plan/config nhưng chưa chạy crawler, rồi tải checkpoint. Lệnh fetch từ chối ghi đè
checkpoint local, xác minh checksum của manifest/state và kiểm identity của plan/shard trước khi
đặt `state.sqlite`:

```bash
uv run python scripts/run_recrawl.py --shard-id 1 --download --download-only
uv run python scripts/checkpoint_handoff.py fetch \
  --root artifacts/recrawl_20261015/zh/shard-001-of-002 \
  --config artifacts/recrawl_20261015/storage.zh.json \
  --plan artifacts/recrawl_20261015/zh/plan.jsonl \
  --shard-count 2 --shard-id 1
```

Lặp lại theo từng ngôn ngữ đang chạy, rồi resume bằng cùng plan/shard/config. Không dùng
`--download` của runner để crawl trước khi fetch checkpoint; máy mới sẽ tạo checkpoint trống và
fetch sẽ từ chối ghi đè nó.

## Danh sách cần quay lại xử lý

Mỗi shard có `followup/documents.csv.gz`, cập nhật khoảng 5 phút trong lúc crawl và lúc kết thúc, đồng thời upload lên đúng folder shard. Có thể khởi tạo báo cáo cả shard chưa chạy mà không gửi HTTP:

```bash
uv run python scripts/run_recrawl.py --shard-id 1 --initialize-only
```

CSV có `doc_id`, URL, host, category, action, reason, fetch state, HTTP status, parse status, has_text, warnings, error, raw_path, archive, parser version, fetched_at. Không cần đọc/tải lại toàn bộ text từ Drive để xuất báo cáo.

| Category | Ý nghĩa | Việc tiếp theo |
| --- | --- | --- |
| pending | Chưa thử | Crawler tiếp tục từ checkpoint |
| deferred | Hoãn theo policy | Sửa truy cập/parser rồi mở nguồn |
| paused | Crawler tự pause | Xem lỗi trước khi resume nguồn |
| crawl_failed | Đã thử nhưng tải không thành công hoặc trang lỗi nguồn | Dùng plan retry sau khi xác minh nguồn |
| parse_failed | Có raw nhưng parser chưa lấy được đúng text | Sửa parser và reparse chọn lọc |
| incomplete | Không có text hoặc cần parse tài nguyên non-HTML | Kiểm raw, PDF/ảnh/media hoặc fetch bổ sung |
| needs_review | Có text nhưng có cảnh báo | Duyệt/mở rộng nội dung trước khi chấp nhận |

`extracted_unreviewed` có trong thống kê tổng nhưng không ghi vào CSV công việc tồn đọng; tên này không khẳng định đã kiểm duyệt đầy đủ. Khi sửa thành công, ID tự rời hàng đợi ở snapshot sau. Tổng các category trong summary luôn bằng tổng ID trong checkpoint.

`summary.json` có thời gian, plan/shard identity, counts theo host và SHA-256 của ba payload báo cáo. Khi tải trong lúc crawler đang cập nhật, kiểm checksum; nếu lệch thì tải lại snapshot mới. Snapshot báo cáo thay thế bản trước, còn batch dữ liệu là immutable.

Hai file JSONL gzip:

- `retry_fetch.jsonl.gz`: chỉ các ID đã kết thúc với lỗi tải; pending/hoãn vẫn thuộc plan gốc. Không tự retry tất cả 403/404/challenge.
- `reparse.jsonl.gz`: chỉ các ID parse lỗi. Các trường hợp needs_review/incomplete vẫn có trong CSV để quyết định cần reparse hay fetch/OCR bổ sung.

Xuất báo cáo thủ công, kể cả khi crawler đang chạy:

```bash
uv run python scripts/followup.py \
  --root artifacts/recrawl_20261015/vi/shard-000-of-002 \
  --storage-config artifacts/recrawl_20261015/storage.vi.json
```

Reparse đúng ID có lỗi sau khi sửa parser (dừng crawler shard/ngôn ngữ đó trước):

```bash
uv run python scripts/crawl.py --reparse --force-reparse --parse-workers 4 \
  --reparse-plan artifacts/recrawl_20261015/vi/shard-000-of-002/followup/reparse.jsonl.gz \
  --plan artifacts/recrawl_20261015/vi/plan.jsonl \
  --output artifacts/recrawl_20261015/vi --shard-count 2 --shard-id 0 \
  --storage-config artifacts/recrawl_20261015/storage.vi.json --prune-local
```

Có thể truyền một JSONL[.gz] ID/URL tự chọn thay cho reparse.jsonl.gz. ID thuộc shard khác bị bỏ qua, URL không khớp checkpoint bị từ chối. Reparse đọc raw từ Drive nếu local đã dọn và upload batch parser mới.

Để tải lại các ID lỗi, giải nén retry_fetch.jsonl.gz thành **plan mới**, chuẩn bị cloud với hash plan mới và dùng output mới. Không xóa kết quả lỗi trong SQLite cũ để ép retry. Muốn thử lại pending ở host đã pause, chỉ mở khi đã xác minh nguyên nhân; `--retry-paused` hiện mở mọi pause của shard và không nên dùng hàng loạt khi còn PubMed chưa có parser.

## Tiến độ và dừng

Log: `artifacts/recrawl_20261015/logs/{vi,en,zh}-shard-{0,1}.log`. Runner ghi PID vào `processes-shard-<id>.json`.

```bash
uv run python scripts/crawl.py --status \
  --output artifacts/recrawl_20261015/vi --shard-count 2 --shard-id 0
```

Dừng runner bằng Ctrl+C nếu chạy foreground, hoặc gửi SIGTERM tới supervisor PID. Runner gửi SIGTERM cho các child, giữ checkpoint và xuất báo cáo. Chạy lại đúng lệnh runner để resume. Exit 2 nghĩa là còn pending/hoãn; exit 1 cần xem log/upload/cache, không được gọi là đã xong.
