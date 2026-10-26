# Tiến trình MedXIR

**Cập nhật:** 23/10/2026 (UTC+07:00).

**Giai đoạn hiện tại:** corpus v1 và vectors 0.6B/4B đã hoàn tất. Đã xác minh phép nối 4B từ hàng vector qua `chunk_id` tới chunk gốc và `hash_groups`. Retrieval 4B chạy tìm kiếm chính xác trực tiếp trên vector đã lưu, không dùng FAISS (xem mục 16). Đang chạy lần retrieval đầu tiên trên Kaggle. BM25 được hoãn tới khi chốt tokenizer riêng cho EN/VI/ZH.

Tài liệu này ghi kết quả đã thu được. Các bước thiết kế chưa triển khai được đánh dấu riêng; hướng giải quyết tổng thể nằm trong [PROPOSED_SOLUTION.md](PROPOSED_SOLUTION.md).

## Rà soát bổ sung — 15/10/2026

Đã kiểm tra rclone trực tiếp: kho MedXIR chỉ còn ba thư mục rỗng `vi/`, `en/`, `zh/`; người dùng xác nhận teammate đã xóa dữ liệu để crawl lại. Các batch verified và kết quả crawl dưới đây là lịch sử, không phản ánh dữ liệu còn trên Drive. Local full shard 0 đã xử lý 321.391 URL ngày 12/10, nên mô tả “chưa chạy full” ở phần lịch sử đã lỗi thời.

Corpus hiện hành có 1.220.598 ID / 94 host, hash khớp Hugging Face, khác số 1.194.469 trong thông báo. Pilot mới: 266/275 URL đã xử lý, 240 có text, 9 pending do pause; chưa đủ căn cứ kết luận mọi nội dung đã đầy đủ. Đã sửa nhận diện challenge/blocked NCBI trong crawler, 8 test offline đạt. Chưa triển khai parser PubMed, chưa crawl full lại hoặc upload.

Báo cáo, danh sách host, giới hạn kiểm thử và đề xuất triển khai: [AUDIT_2026-10-15.md](docs/AUDIT_2026-10-15.md).

## Triển khai crawl lại và hàng đợi sửa lỗi — 15/10/2026

- `prepare_recrawl.py` tạo đủ ba plan, không loại ID theo lịch sử; hợp bằng corpus, ID/URL duy nhất, hai shard modulo 2 không trùng. Mapping host/ngôn ngữ và policy được lưu trong `configs/`.
- Tổng VI/EN/ZH: 740.598 / 240.000 / 240.000. Ban đầu mở 703.983 VI + 107.633 ZH = **811.616 ID**; **408.982 ID hoãn**, vẫn nằm trong plan và follow-up.
- Đã ghim ba storage config vào folder ID con hiện có, chuẩn bị cloud cho cả hai shard và chia sẻ manifest/plan/config trong `MedXIR/coordination/recrawl_20261015/`.
- `followup.py` xuất snapshot SQLite nhất quán: CSV tất cả ID cần xử lý, plan retry tải và plan ID reparse; có lý do, action, raw locator, checksum, thống kê theo host. Cập nhật mỗi 300 giây và khi kết thúc, upload rồi đọc lại xác minh. Có báo cáo ban đầu cho cả shard 1 dù chưa crawl.
- Parser v2.3 bổ sung review bài text ngắn và bài ít text/nhiều ảnh. Reparse chọn lọc ba ID 64406/89705/89714 từ raw cũ đã giữ nguyên text và chuyển đúng sang needs_review.
- `--reparse-plan` chọn chính xác ID/URL để reparse, không tải lại website; `--max-new` giới hạn đợt kiểm chứng; runner chia tổng 16 worker thành 8 VI + 8 ZH/máy.
- 12 kiểm tra offline đạt; đã thử thực tế prepare, upload/checksum và reparse chọn lọc. Đợt đầu: 600 URL đã xử lý, 593 có text (447 extracted_unreviewed, 146 needs_review), 7 không có text. Bốn batch đã upload/đọc lại khớp SHA-256. Đã khôi phục raw từ một batch mỗi ngôn ngữ VI/ZH, kiểm hash nguồn và parse lại khớp text đã upload; bằng chứng ở `artifacts/recrawl_20261015/verification.json`.
- Shard 0 chạy dài với 8 worker VI + 8 worker ZH, 1/nhóm nguồn, delay hiệu dụng 4 giây/máy; batch 2.000 kết quả hoặc 128 MiB raw gzip, prune chỉ sau xác minh. Shard 1 đã khởi tạo báo cáo nhưng chưa crawl, dành cho teammate. PID/log hiện hành ở `artifacts/recrawl_20261015/active_run.json` và `processes-shard-0.json`.

Lệnh hiện hành và chi tiết xử lý lỗi: [RECRAWL_RUNBOOK.md](docs/RECRAWL_RUNBOOK.md). Checkpoint SQLite vẫn giữ local; giữ nguyên plan/shard/config khi resume. Độ đầy đủ nội dung và retrieval chưa được benchmark; extracted_unreviewed không đồng nghĩa đã duyệt sạch.

## 1. Đọc đề bài và khảo sát dữ liệu — đã thực hiện

Đã đọc tài liệu về nhiệm vụ, dữ liệu, đánh giá và định dạng nộp bài. Nhiệm vụ trả về tài liệu và chunk liên quan cho câu hỏi tiếng Việt; đánh giá riêng hai cấp bằng Precision, Recall và F2 theo macro-average. Chưa có nhãn liên quan cục bộ để benchmark retrieval.

Kết quả khảo sát hai file Parquet:

| Hạng mục | Kết quả |
|---|---:|
| Truy vấn | 1.200 |
| Bản ghi corpus / URL không trùng | 4.394.718 |
| Host | 97 |
| ID tài liệu nhỏ nhất / lớn nhất | 583 / 4.420.561 |

ID tài liệu có khoảng trống. Số bản ghi thực tế khác con số 4.420.561 trong tài liệu dữ liệu; pipeline phải dựa vào các ID có trong Parquet.

| Host lớn nhất | Số URL |
|---|---:|
| `www.cnkang.com` | 963.438 |
| `www.120ask.com` | 918.479 |
| `www.familydoctor.com.cn` | 447.453 |
| `ask.39.net` | 337.375 |

Bốn host chiếm khoảng 60,68% corpus. Vì các host còn lại vẫn chiếm gần 40%, pilot được mở rộng cho toàn bộ danh sách host.

## 2. Chốt hướng giải pháp V1 — đã viết thiết kế

Đã ghi giải pháp trong `PROPOSED_SOLUTION.md`: ưu tiên thu thập, làm sạch và chunking; baseline ban đầu dùng một sparse retriever và một dense retriever. Chưa chọn checkpoint cuối cùng, chưa triển khai hoặc đo chất lượng retrieval.

Đã thống nhất nguyên tắc lưu raw cùng ID/URL/trạng thái trước khi parse để có thể sửa parser mà không tải lại. Crawler hiện đã hỗ trợ chia theo `doc_id % shard_count` từ cùng kế hoạch. Chưa có bộ điều phối tốc độ tập trung giữa các máy; mỗi máy tự giảm tốc độ theo số shard.

## 3. Audit host và template — đã chạy pilot

Notebook `notebooks/host_template_audit.ipynb` lấy mẫu theo host/đường dẫn, với mục tiêu khoảng 20–50 URL tùy nguồn và cấu hình. Không phải host nào cũng có đủ mẫu hoặc truy cập được.

Manifest hiện tại có **1.820 bản ghi, bao phủ danh sách 97 host**:

| Trạng thái nguồn | Số bản ghi |
|---|---:|
| HTML response | 1.581 |
| Bỏ qua host (`skipped_host`) | 126 |
| HTTP error | 73 |
| Access challenge | 20 |
| Chuyển về trang chủ | 14 |
| Lỗi tải | 3 |
| Response rỗng | 2 |
| Không phải HTML | 1 |
| **Tổng** | **1.820** |

Các phát hiện chính đã được ghi trong [docs/HOST_TEMPLATES.md](docs/HOST_TEMPLATES.md):

- Có bài kiến thức, hỏi–đáp một/nhiều/không có answer, hồ sơ bệnh, chương sách, danh sách, trang ảnh/media/PDF và quiz.
- `www.familydoctor.com.cn` gặp HTTP 403 trong pilot. `www.familydoctor.cn` là host khác, chưa có cơ sở dùng làm nguồn thay thế.
- HTTP 200 có thể chỉ chứa challenge hoặc body rỗng; redirect có thể đưa về trang chủ.
- Một số trang dùng GBK/GB2312; có payload mã hóa, section trả phí hoặc nội dung chỉ xuất hiện qua tab/link khác.
- Box answer vẫn có quảng cáo, thông tin bác sĩ và UI. Nội dung hữu ích cũng có thể nằm ngoài đoạn tổng quan, trong bảng, list, caption hoặc text trực tiếp.

## 4. Dọn cấu trúc repo — đã thực hiện

Đã bỏ tiền tố số ở tên notebook, bỏ `.python-version` và quản lý thư viện bằng uv với `package = false`. Code audit nằm trong notebook. Artifacts audit được rút còn manifest, tổng hợp host và raw.

Parser hiện nằm ở `scripts/content_parser.py` để notebook review và crawler tương lai dùng chung. Notebook review tập trung chạy, kiểm tra và đối chiếu parser. Không cần một bản `content_parser.py` ở thư mục gốc.

## 5. Parser và review — đã triển khai, đã chạy

Đã triển khai parser theo selector của các host khảo sát, xử lý encoding và giữ cấu trúc nội dung. Parser lưu `doc_id`, URL gốc/cuối, dấu vết raw, title, block theo thứ tự, câu hỏi/answer, metadata, tài nguyên và phần bị loại kèm lý do.

Lần chạy notebook hiện tại dùng raw đã tải, không gửi request mới. Kết quả trong `artifacts/parser_review/documents.jsonl` và `review.html`:

| Kết quả | Số bản ghi |
|---|---:|
| `extracted_unreviewed` | 1.393 |
| `needs_review` | 168 |
| `encoded_content` | 19 |
| Nhận diện trang chủ riêng | 1 |
| Các trạng thái nguồn còn lại | 239 |
| **Tổng** | **1.820** |
| Có text trích xuất | **1.559, thuộc 83 host** |
| Lỗi parser / template chưa nhận diện trong pilot | **0 / 0** |
| Cảnh báo mất/lặp chữ khi đọc DOM vùng đã chọn | **0** |

Số mẫu có text khác tổng hai trạng thái trích xuất vì một số trang có media hoặc placeholder nhưng không có text hữu ích.

### Những trường hợp đã kiểm chứng

- Hỏi–đáp: giữ đúng question/answer, bỏ box quảng cáo và chuyên môn bác sĩ khỏi body answer; câu hỏi thiếu answer được ghi nhận riêng.
- Câu hỏi bổ sung: chỉ bỏ đoạn lặp hoàn toàn; giữ tuổi, thời gian và diễn tiến nếu có thêm thông tin.
- Bách khoa: giữ triệu chứng, xét nghiệm, biến chứng, section và tài liệu tham khảo.
- Chương sách: giữ nội dung thật, loại điều hướng và lời mời tải sách.
- Bài báo: loại tin liên quan; giữ bảng dữ liệu và caption.
- Media: giữ URL tài nguyên, bỏ text fallback trình duyệt, không khẳng định đã có transcript.
- Section trả phí: giữ phần truy cập được và ghi phần thiếu, không đưa thông báo trả phí vào text retrieval.

Các kiểm tra hồi quy trong notebook đã qua. Một số ID đối chiếu: `1298551`, `2291691` (Q&A), `1029368` (audio), `2516803` (JBK), `3160915` (Baidu), `893827` (chương sách), `764166` (YouMed), `2547549` (trả phí).

### Ý nghĩa và giới hạn

`extracted_unreviewed` chỉ nghĩa là chưa có cảnh báo tự động, không phải đã duyệt sạch. `needs_review` có thể là nội dung trích đúng nhưng chưa đầy đủ, không nhất thiết parser sai.

Kiểm tra bảo toàn chữ chỉ xác nhận các vùng đã chọn sau khi cắt UI được đọc đúng; không chứng minh mọi nội dung hữu ích ngoài selector đã được lấy. Báo cáo hiển thị heading và đoạn dài ngoài vùng chọn, notebook cho xem text toàn trang để hỗ trợ đối chiếu.

Kết quả pilot chưa thể suy rộng thành tỷ lệ thành công cho 4,4 triệu URL. Không có số đo Precision/Recall/F2 của retrieval ở thời điểm này.

## 6. Các nhóm còn tồn đọng

| Nhóm | Số mẫu | Phần cần xử lý |
|---|---:|---|
| Wiki | 40 | Duyệt phần đuôi theo loại trang, kiểm tra nhiễu trong vùng nội dung |
| JB39 | 20 | Section hiện tại là bản xem trước; chưa tải phần chi tiết |
| JBK | 17 | Chưa tải tab/link chi tiết |
| Section trả phí | 19 | Thiếu phần không truy cập được |
| Payload mã hóa | 19 | Chưa có decoder tạo được text bài |
| Quiz | 5 | Chưa có câu hỏi trong HTML server |
| Media chưa xác minh transcript | 20 | Text có thể chưa phản ánh nội dung audio/video |
| Ảnh chưa chuyển thành text | 9 | Có thể thiếu thông tin trong ảnh |
| PDF chưa parse | 5 | Chưa lấy text tài liệu đính kèm |
| Nội dung nhúng chưa parse | 10 | Cần kiểm tra tài nguyên nhúng |

Các cảnh báo có thể chồng lấp trên cùng bản ghi. Ngoài bảng còn có trang danh sách, chỉ mục chương, placeholder, câu hỏi không có answer và marker chưa render; chi tiết nằm trong báo cáo.

## 7. Crawler và đợt mở rộng — đã triển khai, đã hoàn tất

Đã thêm `scripts/crawl.py`, dùng lại parser hiện có, không thêm thư viện ngoài `pyproject.toml`.

- Lập kế hoạch chung trước khi chia shard; giữ nguyên ID và loại URL đã có trong pilot.
- Lưu response nén gzip trước khi parse; checkpoint từng URL bằng SQLite.
- Chạy tiếp các URL chưa hoàn thành; khóa shard để tránh hai tiến trình cục bộ cùng ghi.
- Tối đa 12 request đồng thời, tuần tự theo hostname với khoảng cách mặc định 1,5 giây. Redirect/retry cũng qua giới hạn.
- Timeout, giới hạn kích thước response, retry có giới hạn, xử lý `Retry-After` và tạm dừng host sau ba lỗi truy cập liên tiếp.
- Xuất manifest, tài liệu, summary và báo cáo review mẫu; có chế độ chỉ export hoặc parse lại raw mà không tải mới.
- Giảm tốc độ mỗi máy theo số shard; chưa bảo đảm giới hạn tổng nghiêm ngặt giữa các máy.

Đã tạo **10.000 URL mới trên 80 host** tại `artifacts/crawl/plan.jsonl`. Ba host `fk.99.com.cn`, `mega.vietnamplus.vn`, `special.vietnamplus.vn` có HTML pilot nhưng không còn URL mới trong corpus. Các host không có HTML pilot chưa được đưa vào đợt này; danh sách loại được in khi lập kế hoạch.

Lấy tối đa 20 URL ban đầu mỗi host, phần còn lại phân bổ theo căn bậc hai số URL, xen kẽ nhóm thành phần đầu đường dẫn. Phân bổ này phục vụ kiểm chứng độ đa dạng, không đại diện tỷ trọng corpus và chưa xác nhận mọi template.

Snapshot đã xuất trước khi chạy tiếp:

| Kết quả đợt mở rộng | Số bản ghi |
|---|---:|
| Đã lưu checkpoint | 1.187 |
| Có text | 1.152 |
| `extracted_unreviewed` | 1.019 |
| `needs_review` | 133 |
| `encoded_content` | 21 |
| `unknown_template` | 3 |
| HTTP error | 4 |
| Chuyển về trang chủ | 5 |
| Không phải HTML | 2 |
| Chưa hoàn thành tại snapshot | 8.813 |

Chưa ghi nhận `parser_error` trong snapshot. Ba trang chưa khớp template là `713530` (infographic VOV2), `856747` (People Health), `2052399` (chỉ mục y án ZYDCD). Được giữ nguyên raw và trạng thái để duyệt; chưa kết luận nguyên nhân hoặc độ đầy đủ.

Đã xuất kết quả checkpoint rồi khởi chạy tiếp cùng kế hoạch. Sau đó đã dừng tiến trình nền theo yêu cầu người dùng, xuất đầy đủ kết quả đã lưu. Người dùng tiếp tục chạy trực tiếp và hoàn tất 10.000 URL. Các file JSONL/review là snapshot lần xuất gần nhất, không cập nhật theo từng URL; summary được cập nhật theo `--progress-every` (mặc định 25 URL) và khi kết thúc.

Checkpoint khi dừng: **6.891/10.000 URL (68,9%)**, còn **3.109 URL**; **6.757 bản ghi có text**, gồm 5.871 `extracted_unreviewed`, 887 `needs_review`; có 24 `unknown_template` và không ghi nhận `parser_error`. Raw, SQLite, JSONL và báo cáo mẫu đã được giữ. Không còn tiến trình crawler nền do agent khởi chạy.

Đã bổ sung `--resume` (yêu cầu checkpoint có sẵn), `--status` (đọc tiến độ trực tiếp, không tải), `--progress-every` (tần suất in tiến độ). Chế độ shard dùng `--shard-count`/`--shard-id`; `--status` và `--resume` áp dụng theo shard được chọn. Đợt hiện tại giữ cấu hình một shard để tiếp tục đúng checkpoint.

## 8. Kiểm tra chất lượng đợt 10.000 — đã thực hiện

Kết quả cuối cùng được xuất lúc **21:49 ngày 10/10/2026 (UTC+07:00)**. Nguồn kiểm tra: `artifacts/crawl/shard-000-of-001/{pages.jsonl,documents.jsonl,state.sqlite,raw/}`. Không tải thêm URL và không sửa/parse lại toàn bộ kết quả trong vòng review này.

### Kết quả định lượng

| Kết quả | Số bản ghi | Tỷ lệ trên 10.000 |
|---|---:|---:|
| Đã hoàn thành checkpoint | 10.000 | 100% |
| HTML response | 9.840 | 98,40% |
| Có text | 9.737 | 97,37% |
| `extracted_unreviewed` | 8.316 | 83,16% |
| `needs_review` | 1.423 | 14,23% |
| `unknown_template` | 55 | 0,55% |
| `encoded_content` | 46 | 0,46% |
| HTTP error | 65 | 0,65% |
| Chuyển về trang chủ | 55 | 0,55% |
| Response rỗng | 32 | 0,32% |
| Không phải HTML | 5 | 0,05% |
| Access challenge | 2 | 0,02% |
| Fetch error | 1 | 0,01% |
| `parser_error` | 0 | 0% |

Hai bản ghi có media nhưng không có text khiến tổng hai trạng thái trích xuất là 9.739, khác 9.737 bản ghi có text. Các nhóm cảnh báo có thể chồng lấp.

### Phạm vi kiểm tra và kết quả đạt được

- Đối chiếu toàn bộ 10.000 ID/URL/host với plan, và ID/URL với Parquet chính thức: không có sai lệch; manifest và tài liệu đều đủ 10.000 bản ghi.
- Giải nén và kiểm tra SHA-256, độ dài của toàn bộ **9.999 raw response** có đường dẫn: không có lỗi file hoặc sai hash. Một fetch error không có raw.
- Đường dẫn raw và hash nguồn trong tài liệu đều khớp manifest.
- Đọc thống kê/cảnh báo trên toàn bộ tài liệu; parse lại **160 mẫu, hai mẫu mỗi host**, từ raw với parser hiện tại: title, text, blocks, answers, warnings và status khớp kết quả lưu. Điều này xác nhận khả năng tái lập, không chứng minh mọi mẫu sạch.
- Đối chiếu raw các nhóm bất thường: title rỗng, unknown template, bảng bị lệch chữ, phân trang, HTML còn trong text và khối gợi ý/quảng cáo.
- Cập nhật file `artifacts/crawl/shard-000-of-001/review.html` hiện có với tập mẫu theo host và các dấu hiệu lỗi: tất cả unknown/title rỗng/phân trang đã phát hiện, ca đã xác nhận và hai mẫu mỗi host. Chỉ cập nhật báo cáo hiển thị, giữ nguyên raw, checkpoint và `documents.jsonl`.
- Các phần `answers[].text` đều khớp các block tương ứng. Có 1.286 trang Q&A; 53 mẫu không có answer được cảnh báo riêng. Nội dung ngắn không tự động bị loại.
- Có 366 tài liệu với text dưới 120 ký tự, nhiều mẫu là câu trả lời/chương sách/ngắn hoặc caption hợp lệ; không coi toàn bộ là lỗi.

### Vấn đề đã xác nhận

| Vấn đề | Bằng chứng / phạm vi | Ý nghĩa |
|---|---|---|
| Title rỗng nhưng nguồn có title | **86 mẫu**: 61 Baidu, 25 People Health; cả 86 đều có title hoặc heading không rỗng trong raw | `title_of` dừng ở node rỗng, bỏ qua fallback. Các mẫu hiện không được cảnh báo lỗi này. |
| Bảng bị đọc lặp | **1 mẫu**, SKĐS `493463`; khi đối chiếu vùng DOM đã làm sạch có thêm 416 ký tự sau khi bỏ khoảng trắng/separator | Lỗi đọc cấu trúc bảng lồng/malformed; cảnh báo `block_text_mismatch` đã bắt được. Đây là lặp chữ, không phải mất 416 ký tự. |
| Bài phân trang chưa lấy đủ | **32 mẫu** có text “下一页” và link trang tiếp theo trong raw: Wujue 26, CNKang 3, ba host 39.net mỗi host 1 | Crawler chỉ tải URL được cấp, chưa nối các trang tiếp theo. Text còn pager, chưa có cảnh báo thiếu phân trang. |
| Nhiễu publisher lặp lại | **1.563 tài liệu** còn câu bản quyền 39.net trong text | Rule hiện chưa phủ các vị trí DOM chứa boilerplate; cần tách khỏi text retrieval, giữ provenance. |
| Gợi ý bài còn trong body | `911500` (wei.39.net: “相关推荐”), `834086` (fk.39.net: “编辑推荐”) | Cả hai đang là `extracted_unreviewed`; wrapper đúng vẫn có thể chứa khối gợi ý. |
| Khối quảng bá còn trong bài | `174333`, `174369` (MEDLATEC Store); `746491` (Vinmec hotline); `760445` (YouMed chat/app) | Cần rule theo cấu trúc/section. Không xóa toàn bộ đoạn chỉ vì có từ app/hotline. |
| HTML còn ở dạng text | **11 ứng viên** có dấu hiệu tag trong text: 9 Q&A 120ask, 1 Woman39, 1 Bệnh viện Nhi; đã xác nhận ví dụ `1212271`, `923749` | Có escaped markup từ nguồn chưa được xử lý; ảnh trong markup có thể chưa thành resource. Cần phân biệt với văn bản thật. |
| Wiki còn điều hướng | **402 tài liệu** chứa nhãn điều hướng wiki; toàn bộ 681 tài liệu wiki đã có `wiki_tail_requires_review` | Khối đuôi chưa sạch, cần phân loại cả trang file/attachment và sách/bệnh/thuốc. |

Quét từ khóa còn phát hiện 62 ứng viên có câu đặt lịch/hotline (44 ở MEDLATEC), nhưng đây chỉ là danh sách cần đối chiếu, không phải 62 lỗi đã xác nhận. Mẫu `1313` của Bạch Mai thực sự hướng dẫn dùng ứng dụng khám bệnh; loại theo từ khóa sẽ làm mất nội dung hữu ích. Tương tự, “Đăng nhập” trong `760515` là chữ nằm ở phần trích nguồn, không phải menu đăng nhập.

### Phân biệt template mới với vấn đề của nguồn

55 `unknown_template` thuộc 14 host; CNKang chiếm 30. Đối chiếu raw xác nhận ít nhất **10 mẫu** là lỗi/thiếu nội dung nguồn, không đơn thuần thiếu selector:

- Năm CNKang trả `Fatal error` dù HTTP 200: `1728056`, `1728067`, `1728069`, `3485054`, `3485056`.
- Một 39.net chuyển sang `/web/404.html`: `812601`.
- Một Vinmec báo không tìm thấy dữ liệu: `742721`.
- Ba trang có vùng thân bài rỗng: Quảng Ninh `82817`, PMPHAI `2541307`, `2541601`.

**45 mẫu còn lại cần phân loại/template bổ sung**, có cả bài cũ, longform, media, infographic và trang danh sách; không khẳng định cả 45 là bài text lấy được đầy đủ. Ví dụ Dân trí có `#desktop-cate-in-article`, VietNamNet longform có `.inner-contetn`; ZYDCD `2052399` là chỉ mục y án, không phải một y án đơn.

CNKang có 59 HTTP 404 và 32 response rỗng trong 813 URL mẫu. Toàn bộ 65 HTTP error của đợt đều là 404, không phải 403/429. Vì vậy không nên tăng retry cho nhóm này để mong cải thiện độ phủ.

### Nội dung chưa đầy đủ đã được cảnh báo

- Wiki: 681; JB39 preview: 90; JBK chưa tải tab chi tiết: 132.
- Section trả phí: 101; nội dung mã hóa: 46; quiz chưa có câu hỏi trong HTML: 3.
- Ảnh chưa chuyển thành text: 63; media chưa xác minh transcript: 83; PDF chưa parse: 17; nội dung nhúng chưa parse: 120.

52 ứng viên có đoạn dài ngoài selector đều thuộc ask.39.net, food.39.net, gc.39.net và wei.39.net. Nội dung quan sát được là hồ sơ bệnh/triệu chứng hoặc chuyên môn bác sĩ ở sidebar; đây là bằng chứng việc bỏ ngoài selector có thể đúng, không nên gom lại chỉ vì đoạn dài. Heuristic này không bao phủ mọi cách bỏ sót nội dung.

Có ba nhóm hai ID có text trùng hoàn toàn, gồm alias thuốc hoặc hai URL của cùng nội dung. Chưa loại ID nào; deduplicate nội dung cần giữ mapping tới toàn bộ ID chính thức. Hai mẫu có ký tự replacement trong text cũng cần đối chiếu dữ liệu nguồn, dù decoder không ghi cảnh báo fallback/replacement.

### Kết luận chất lượng và thứ tự sửa theo ROI

Lớp tải/lưu dữ liệu hoạt động ổn trên tập đã chọn; raw đủ để sửa parser mà không crawl lại. **Chưa đủ cơ sở gọi 9.737 bản ghi có text là corpus sạch**, và `extracted_unreviewed` vẫn có false negative của hệ thống cảnh báo.

1. Sửa fallback title, đọc bảng, boilerplate và khối gợi ý/quảng bá đã xác nhận; thêm cảnh báo title rỗng, escaped markup và phân trang. Chi phí thấp, có bằng chứng cụ thể và phạm vi ảnh hưởng lớn.
2. Phân loại 55 unknown; bổ sung template cho các nhóm có nội dung, tách lỗi nguồn/empty body khỏi unknown. Giữ raw, không cần tải lại để sửa selector.
3. Thiết kế theo link phân trang có quan hệ rõ với cùng bài, lưu phần bổ sung gắn `doc_id` gốc; chưa triển khai trong vòng review này.
4. Parse lại raw sau khi sửa, duyệt lại mẫu lỗi và mẫu bình thường; sau đó mới quyết định mở rộng crawl/chunk.

Chưa sửa parser, chưa thay đổi kết quả crawl và chưa chạy thêm crawl trong vòng kiểm tra chất lượng này.

## 9. Hạ tầng lưu trữ V1 — thiết kế ban đầu OneDrive, 11/10/2026

Đã cài rclone v1.75.2 tại `~/.local/bin/rclone` trên máy hiện tại, thêm `scripts/storage.py` và tích hợp upload batch vào crawler. Teammate cần tự cài rclone; Python vẫn dùng các dependency hiện có.

### Cơ chế đã cài đặt

- Cấu hình chung `storage.json` chứa remote/thư mục và `drive_id`. Mỗi máy đăng nhập rclone riêng; drive sai bị từ chối trước upload. Chưa tạo file này vì chưa đăng nhập OneDrive.
- Đường dẫn cloud theo SHA-256 plan, shard và UUID batch; không ghi đè batch giữa các máy. Phân công vẫn dùng ID chính thức modulo số shard.
- Đóng batch theo 10.000 document hoặc 256 MiB raw gzip. Giữ nguyên `.gz` trong `raw.tar`; lưu đủ pages/document/audit dưới JSONL gzip và manifest checksum.
- Upload payload, đọc lại byte để kiểm SHA-256, rồi xuất manifest làm dấu hoàn tất. Batch chưa xác minh được giữ local và thử lại khi resume.
- `--prune-local` chỉ dọn batch đã xác minh; SQLite giữ trạng thái/locator và document rút gọn. SQLite tái sử dụng phần trống; muốn giảm kích thước file ngay cần VACUUM khi đã dừng crawler. Các JSONL xuất cũ vẫn giữ nguyên.
- `--cache-limit-gb` đặt mốc dừng cho raw/batch local; có thể vượt tạm thời trong khi đóng batch/request đang chạy. Không áp dụng cho SQLite/file export và không phải hạn ngạch đĩa cứng.
- Có lệnh đọc document theo shard, khôi phục một raw và xuất document của tất cả shard từ OneDrive mà không cần SQLite trên máy indexing. Xuất remote chọn batch mới nhất cho từng ID, cần đồng bộ đồng hồ các máy.
- Crawler thêm `--host-workers`, cho phép tải nhiều response trong cùng hostname nhưng vẫn giới hạn khoảng cách bắt đầu request, retry và redirect. Mặc định cũ 12 tổng/1 host/delay 1,5 giây được giữ.

### Kết quả thao tác thực tế

Đã chạy đóng batch trên checkpoint 10.000 mẫu. Trước khi đóng, kiểm SHA-256 raw giải nén với digest nguồn; không có lỗi khiến thao tác dừng.

| Thành phần | Dung lượng byte |
|---|---:|
| `raw.tar` | 265.584.640 |
| `pages.jsonl.gz` | 891.887 |
| `documents.jsonl.gz` | 24.127.288 |
| Tổng payload | 290.603.815 (~277,1 MiB) |

Batch `d41e657764064425a66a2cbc5fed88cf`, plan SHA-256 `f3db74ca32b0e3e32c059d70accaedca5ccabb01b223024ad17e6692d3fa1464`, shard `000-of-001`. Trạng thái: **1 batch pending, 0 document chưa đóng**. Checkpoint vẫn đủ 10.000 kết quả; raw gốc chưa xóa, chưa compact SQLite và chưa đổi nội dung parser.

Đã kiểm tra cú pháp Python và đọc trạng thái checkpoint/batch. **Chưa xác minh upload/download OneDrive thực tế**, vì cấu hình rclone hiện chưa có remote; cần người dùng đăng nhập Microsoft. Chưa đo tốc độ crawler sau thay đổi song song; chưa chạy thêm crawl hoặc khởi động crawler nền.

Cấu hình khởi điểm đề xuất: 32 worker tổng, 2/host, delay 1,5 giây. Tăng tiếp 48–64 tổng/2–4 host tùy timeout, 429/5xx và tài nguyên máy; chưa có số worker tối đa đã đo. Giữ nguyên delay 1,5 giây thì riêng 963.438 URL CNKang có cận dưới khoảng 16,7 ngày, bất kể worker tổng. Các host bị chặn vẫn cần xử lý riêng.

Hướng dẫn thao tác trong [README.md](README.md#google-drive-lưu-cuốn-chiếu-và-dùng-nhiều-máy). Chưa tự backup/khôi phục SQLite/plan từ cloud; giữ các file checkpoint local.

## 10. Chuyển sang Google Drive — 11/10/2026

Người dùng đổi đích lưu trữ sang Google Drive và đã đăng nhập rclone remote `gdrive` (backend `drive`). Đã cập nhật `scripts/storage.py`, CLI crawler và README để dùng đích này; dữ liệu/batch đã đóng được tái sử dụng.

- Đã tạo thư mục `MedXIR`, ID `1tfvL8OJIRejT_c-4wfaQnTZecT67qJld`, và `storage.json` chứa remote, backend, folder ID và Shared Drive ID nếu có. File không chứa token/mật khẩu; có thể chia sẻ nguyên file cho các máy.
- Mọi thao tác cloud cố định `root_folder_id` theo ID thư mục trên. Hai tài khoản có thư mục trùng tên không dẫn upload sang hai nơi. Tài khoản teammate khác phải có quyền sửa vào cùng thư mục.
- Thêm `storage.py prepare --plan PLAN --shard-count N` để một máy tạo các thư mục plan/shard chung trước khi các máy bắt đầu. Google Drive cho phép tên thư mục trùng; bước chuẩn bị tuần tự tránh việc hai máy tạo các thư mục cha trùng nhau. Crawler yêu cầu thư mục shard đã được chuẩn bị.
- Đã chuẩn bị layout cho plan hiện tại, một shard; chưa thay đổi số shard/checkpoint và chưa khởi động crawl mới.

### Upload thực tế đã hoàn tất

Batch `d41e657764064425a66a2cbc5fed88cf` chứa đủ 10.000 bản ghi đã upload vào:

```text
MedXIR/runs/f3db74ca32b0e3e32c059d70accaedca5ccabb01b223024ad17e6692d3fa1464/
  shard-000-of-001/batches/d41e657764064425a66a2cbc5fed88cf/
```

Ba payload tổng 290.603.815 byte (~277,1 MiB) và manifest đều được đọc lại qua rclone và **khớp SHA-256** với bản local. Checkpoint ghi batch **verified**. Lần này chạy `upload` không có `--prune-local`: raw, batch local, full document trong SQLite và các bản xuất cũ vẫn giữ nguyên. Chưa chạy VACUUM.

Remote báo quota **429.496.729.600 byte (400 GiB)** và **398.653.978.817 byte (~371,3 GiB) trống** trước upload; đây là quota Google Drive thực tế, không phải 5 TB OneDrive đã đề cập trước đó. Kích thước toàn corpus cần tiếp tục đo khi phân bố host thay đổi.

Đã kiểm cú pháp Python và đọc trạng thái local. Xác minh lần này là upload/download byte thực tế trong cơ chế lưu trữ; chưa đo crawler với số worker mới, chưa chạy crawl nền và chưa sửa parser. Hướng dẫn hiện hành ở [README.md](README.md#google-drive-lưu-cuốn-chiếu-và-dùng-nhiều-máy); thư mục cloud [MedXIR](https://drive.google.com/drive/folders/1tfvL8OJIRejT_c-4wfaQnTZecT67qJld).

## 11. Parser v2.1 và hướng dẫn CLI — 11/10/2026

Đã sửa `scripts/content_parser.py` dựa trên các trường hợp trong raw của đợt 10.000 URL, giữ ID và URL chính thức. Không tải thêm URL nguồn.

### Các thay đổi đã cài đặt

- Bổ sung fallback cho Dân trí, VietNamNet longform, CNKang bài cũ/recipe/listing, VOV/VOV2, Care39, chuyên đề Man39, ZYDCD và video SKĐS. Giữ sapo/heading/body theo thứ tự DOM.
- Title fallback tiếp tục tìm khi node hoặc meta rỗng, thay vì kết thúc với title rỗng.
- Đọc bảng theo DOM để tránh lặp text ở hàng/cell lồng hoặc thiếu thẻ đóng; giữ cấu trúc hàng cho bảng dữ liệu. Vẫn đối chiếu text vùng đã chọn với các block và cảnh báo nếu không khớp.
- Cleanup thông báo publisher/editor, các khối link gợi ý, store card MEDLATEC, booking CTA Vinmec và app promotion YouMed theo cấu trúc đã xác nhận. Không áp dụng xóa hàng loạt bằng từ khóa; hướng dẫn ứng dụng trong bài Bach Mai được giữ. `removed` lưu text/link/lý do của phần loại.
- Chuyển escaped HTML có tag được nhận diện thành DOM, giữ caption/heading/tài nguyên; lưu bản trước chuyển trong `normalizations` và cảnh báo.
- Tách `source_error` cho fatal error, soft 404 và Vinmec báo không tìm thấy dữ liệu; `empty_content` cho vùng bài thật sự rỗng; `unknown_template` dành cho trang chưa tìm được vùng nội dung.
- Ghi `pagination_links` và cảnh báo phần tiếp theo chưa tải. Loại nút điều hướng khỏi text; không nhận link chuyển chương sách/tab chi tiết khác ID là phân trang cùng bài. Chưa tải trang tiếp theo.
- Giữ URL video từ widget/data attribute, URL ảnh/audio/PDF cùng caption nếu có. Gallery/infographic được ghi thiếu text trong ảnh, không coi mô tả ngắn là đã lấy đủ nội dung.

### Thao tác và tài liệu

Crawler có `--parse-workers` để reparse bằng process CPU, `--force-reparse` để áp dụng lại cùng version, và tiến độ reparse trực tiếp. Mặc định reparse bỏ qua document đã cùng version để tiếp tục sau khi dừng. `--status` và summary ghi phân bố parser version. Worker reparse tách riêng `--workers`/`--host-workers` dùng cho HTTP.

README đã ghi lệnh parser độc lập, crawl, prepare, resume, status, reparse thường/ép, export, chia shard, thay plan/output, cloud và toàn bộ tùy chọn crawler/parser. Danh mục template được cập nhật các selector/cleanup đã cài đặt.

### Kết quả parse lại toàn bộ 10.000 raw

Lượt cuối dùng `--reparse --force-reparse --parse-workers 4`, hoàn tất khoảng **300,8 giây** trên máy hiện tại. Sau đó xuất lại JSONL/summary và báo cáo mẫu 250; số mẫu HTML thực tế bị giới hạn bởi các nhóm host/trạng thái. Toàn bộ 10.000 document có `parser_version = parser-v2.1`, không gửi request mới tới website nguồn. Đã kiểm cú pháp Python; không thêm thư viện hoặc bộ test mới.

| Chỉ tiêu | Trước sửa (`pilot-v1`) | Sau sửa (`parser-v2.1`) |
|---|---:|---:|
| Bản ghi có text | 9.737 | **9.775** |
| `extracted_unreviewed` | 8.316 | 8.173 |
| `needs_review` | 1.423 | 1.611 |
| `unknown_template` | 55 | **0** |
| `source_error` | Chưa tách | 7 |
| `empty_content` | Chưa tách | 3 |
| `encoded_content` | 46 | 46 |
| `parser_error` | 0 | 0 |
| `block_text_mismatch` | 1 | **0** |
| Title rỗng ở bản trích/needs_review | 86 | **0** |

55 unknown cũ được phân thành **45 bản trích** (31 không có cảnh báo, 14 cần duyệt), **7 lỗi nguồn** và **3 vùng nội dung rỗng**. Có thêm 38 bản ghi chứa text; phần còn lại gồm trang media chỉ có tài nguyên. Không tính trang lỗi thành tài liệu đã lấy nội dung.

- Bảng SKĐS `493463` giữ 6 hàng, không còn lặp chữ; 6 recipe CNKang giữ hàng/cell và không có mismatch. Hai bài CNKang chỉ có text/br `3506847`, `3507150` được khôi phục.
- Mẫu Dân trí, VietNamNet longform, Care39, Man39, VOV/VOV2 và ZYDCD mới đã có vùng trích phù hợp. Infographic `713530` chỉ có mô tả và ảnh nên vẫn `needs_review`; gallery `923749` giữ caption/ảnh và dấu vết chuyển escaped markup.
- 1.563 tài liệu trước đó chứa cụm publisher “未经书面授权”; còn **1** (`855409`) do chung đoạn với lời nhắc phương thuốc lấy từ mạng và cần thận trọng. Rule xóa nguyên thông báo không xóa đoạn có thêm thông tin này. `834086`, `911500` đã loại khối related/publisher; `174333`, `174369`, `746491`, `760445` đã xử lý các CTA/store/app đã xác nhận.
- 5 title còn thiếu thuộc các trang PHP fatal error, không thuộc bản trích nội dung.

`needs_review` tăng vì nhận diện thêm media/gallery/infographic, pagination và chuẩn hóa escaped HTML, cùng các template mới. Số bản ghi không cảnh báo giảm không đồng nghĩa mất dữ liệu; số liệu này cũng không chứng minh mọi nội dung đã sạch.

**Còn thiếu:** 31 tài liệu có phân trang chưa lấy phần tiếp; 46 payload mã hóa; cảnh báo 73 trang có text trong ảnh chưa chuyển, 195 trang media chưa xác minh transcript, 17 PDF và 148 nội dung nhúng chưa parse. Ngoài ra còn 101 trang trả phí, 90 preview JB39, 132 tổng quan JBK thiếu tab chi tiết, 681 wiki cần duyệt đuôi và 53 QA không có answer trong HTML. Các nhóm chồng lấp. Không còn unknown trong 10.000 mẫu không bảo đảm mọi template của 4,4 triệu URL đã được bao phủ.

Kết quả hiện tại ở `artifacts/crawl/shard-000-of-001/documents.jsonl`, `summary.json` và `review.html`. Pilot/báo cáo cũ vẫn là kết quả lịch sử cho tới khi tự chạy lại notebook; raw gốc được giữ để xử lý tiếp.

### Xuất bản kết quả mới lên Google Drive

Đã chạy `storage.py upload` sau reparse, tạo batch **`4ea9a3ff320e4132bb26600c88640726`** chứa 10.000 bản ghi v2.1, cùng plan/shard và thư mục Drive ở mục 10. Ba payload và manifest đã được đọc lại, khớp SHA-256; checkpoint ghi `verified = 1` và mọi document trỏ tới batch mới.

| Thành phần | Dung lượng byte |
|---|---:|
| `raw.tar` | 265.584.640 |
| `pages.jsonl.gz` | 891.887 |
| `documents.jsonl.gz` | 25.430.030 |
| Tổng payload | 291.906.557 (~278,4 MiB) |

Batch v1 cũ vẫn giữ trên cloud/local để đối chiếu, cùng các ID với batch mới; không phải 10.000 tài liệu mới. `export-remote` chọn bản trong batch mới hơn. Raw vẫn được đóng lại trong batch mới theo cơ chế V1, nên cloud tăng thêm khoảng 278,4 MiB; chưa tách riêng kho raw để dùng chung qua nhiều phiên bản parser. Không dùng `--prune-local`, chưa compact document hoặc VACUUM, không khởi động crawler tải URL nền.

## 12. Parser v2.2 và khả năng chạy full — 11/10/2026

### Parser và nguồn mới

- Giải mã payload CMS Phú Thọ/Thanh Hóa theo JavaScript công khai của CMS (alphabet/rotate/Base64); không chạy JavaScript tùy ý. Kiểm tra cấu trúc HTML sau decode, giữ payload gốc và đường dẫn nguồn. **46/46 mẫu cũ và 19/19 mẫu pilot đã giải mã**; không coi chuỗi mã hóa là text.
- Long Châu đã truy cập lại được: lấy trường đúng bài từ `__NEXT_DATA__`, kiểm tra slug, giữ mô tả, triệu chứng, nguyên nhân, nguy cơ, chẩn đoán/điều trị, sinh hoạt/phòng ngừa, FAQ và tham khảo. Loại danh sách sản phẩm/bài/bệnh gợi ý. Sáu hồ sơ bệnh có phần hữu ích nằm ngoài nội dung HTML ban đầu; đã giữ đủ các trường hiện có và 5 FAQ mỗi hồ sơ.
- Việt Đức: hai bố cục `.news-primary-content .edittor-content` và `.news-primary-content`; giữ nội dung và tác giả trong bài, loại chia sẻ/tag/gợi ý. Selector đầu tiên quá hẹp gây 3 unknown, gate đã tạm dừng đúng; bổ sung fallback rồi parse lại và hoàn tất phần còn lại.
- Reparse toàn bộ pilot và cập nhật báo cáo HTML local bằng v2.2. Không còn unknown, encoded hay mismatch trong pilot; số có text là 1.578/1.820 (gồm trạng thái fetch/skip cũ).

### Kết quả đo được

| Đợt | Hoàn thành | Có text | Unknown / encoded / parser error / mismatch |
|---|---:|---:|---:|
| 10.000 mẫu cũ, reparse raw | 10.000 | 9.821 | 0 / 0 / 0 / 0 |
| 1.000 URL mới, không trùng mẫu cũ | 1.000 | 964 | 0 / 0 / 0 / 0 |
| Long Châu + Việt Đức, 20 URL mỗi host | 40 | 40 | 0 / 0 / 0 / 0 |

Reparse 10.000 mẫu mất khoảng 226,3 giây với 4 process. 1.611 bản cần duyệt; không đồng nghĩa 9.821 bản đều đủ nội dung hoặc hoàn toàn sạch.

**179 mẫu cũ không có text:** 65 HTTP 404, 55 redirect về trang chủ, 32 response rỗng, 7 lỗi nội dung nguồn, 5 non-HTML, 3 vùng body rỗng, 2 challenge, 1 lỗi fetch và 9 trang media chỉ có tài nguyên. Không có trường hợp còn mất text do decoder trong 46 mẫu đã sửa.

**36 mẫu mới không có text:** 28 CAPTCHA/challenge, 3 HTTP 404, 3 redirect trang chủ, 1 non-HTML và 1 media. Các challenge đã hoàn thành với trạng thái lỗi được giữ lại; không báo thành công hoặc tự retry chúng. Sau cooldown, 5 URL pending thuộc nhóm nguồn tạm dừng đã tải được với giới hạn mới.

### Crawler và lưu trữ

- Thêm giới hạn chung theo nhóm subdomain 39.net/familydoctor/iiyi, áp dụng cả retry/redirect. Chờ semaphore/delay không bị tính là timeout HTTP.
- Pause nguồn khi lỗi truy cập liên tiếp hoặc parser unknown/error/encoded/mismatch vượt gate; giữ pause trong SQLite qua resume. `--retry-paused` chỉ mở lại URL pending; còn pending thì kết thúc exit code 2.
- Thêm `--all-hosts`, `--include-host`, `--exclude-completed`; skipped pilot chưa fetch không bị loại khỏi full plan. Checkpoint nhập theo batch và cờ `plan_loaded` tránh nhập lại hàng triệu dòng khi resume.
- Cache checksum/tar theo batch khi đọc raw cloud; kiểm tra digest raw local. Chỉ dọn batch đã upload và xác minh.
- **7 kiểm tra offline đã đạt**, gồm shard ID không liên tục, loại ID đã làm, pause/resume, giới hạn subdomain, hàng đợi/timeout, bảng lỗi HTML, payload CMS sai, trường ẩn Long Châu và pack/verify/prune/restore raw.
- Batch v2.2 **`eabafa6fc7d347649b547038bd5e1420`** của 10.000 ID đã upload và đọc lại xác minh SHA-256; giữ local. Payload gồm raw.tar 265.584.640 byte, pages.jsonl.gz 891.887 byte và documents.jsonl.gz 25.573.468 byte (tổng 292.049.995 byte). Ba batch cloud là ba phiên bản của cùng ID, không cộng thành 30.000 tài liệu. Hai đợt 1.000/40 mẫu mới còn local.

### Phạm vi full và kết luận

Reprobe 14 nguồn cũ: Long Châu/Việt Đức mở lại, 12 nguồn còn lỗi/chặn. **85 host với 3.632.818 URL** đã có bằng chứng HTML nội dung; **12 host với 761.900 URL** chưa truy cập được. Nhóm 39.net vẫn có CAPTCHA theo thời điểm.

Đã tạo `artifacts/crawl_full/plan.jsonl`, **4.381.984 URL**, loại hợp 12.734 ID đã thử/hoàn thành từ các đợt trước. Plan bao gồm mọi host còn URL (90 host); toàn corpus có 97 host. Đã đối chiếu plan: 4.381.984 ID duy nhất, mọi ID/URL khớp corpus; không thiếu ID ngoài hợp loại trừ và không trùng ID đã loại. Chia modulo 2: shard 0 có 2.190.903 URL, shard 1 có 2.191.081 URL. Khởi tạo checkpoint shard 0 mất 15,69 giây, peak RSS 71.932 KiB; mở lại/export không nhập lại plan mất 0,99 giây. Đã chuẩn bị thư mục Drive cho hai shard. Không khởi chạy full crawl. Lệnh chính thức cho hai máy, resume/status, storage và giới hạn nguồn nằm trong [README.md](README.md#chạy-toàn-bộ-corpus).

**Kết luận:** đủ khả năng bắt đầu full crawl có kiểm soát và giữ nguồn để sửa parser sau. Chưa thể xác nhận sẽ lấy đủ nội dung 4,4 triệu URL. Cảnh báo phân trang (31 mẫu cũ), media/PDF/ảnh, QA thiếu đáp án và vùng preview/paywall còn tồn tại; không tự biến chúng thành bản hoàn chỉnh cho indexing. Theo delay 2 giây, riêng CNKang có cận dưới khoảng 22,3 ngày; đây không phải cam kết thời gian hoàn thành.

## 13. Kế hoạch tại thời điểm 11/10/2026

1. Chạy full plan trực tiếp theo shard; theo dõi batch đầu, nguồn pause, tỷ lệ text/cảnh báo và dung lượng theo host.
2. Xử lý host bị chặn khi có phương án truy cập thực sự; mở lại pending sau khi xác minh, tránh retry liên tục.
3. Duyệt cảnh báo thiếu nội dung, bổ sung phân trang/media theo ROI trước khi chunk các nhóm đó.
4. Hợp kết quả các đợt theo doc_id, thiết kế chunking từ block/QA và triển khai sparse + dense baseline.

**Chưa hoàn thành:** crawl toàn bộ, chunking, indexing, retrieval, reranking, benchmark và bài nộp.

## 14. Chunking, embedding và đóng gói vector — 23/10/2026

### Chunking corpus trên Kaggle

Kernel `boinhbo/medxir-chunk-corpus` hoàn tất. [Manifest đã tải về](artifacts/kaggle/chunk-corpus-v1/chunk_manifest.json) ghi corpus v1, `clean-v1`, tham số chunk `350/50/40`, 118 ID bị loại theo cấu hình và 1.023.783 hash group.

| Ngôn ngữ | Chunk |
|---|---:|
| EN | 250.487 |
| VI | 2.128.601 |
| ZH | 242.465 |
| **Tổng** | **2.621.553** |

### Embedding toàn bộ corpus

Hai model đã chạy trên cùng 2.621.553 chunk / 987.297.945 token, gồm đủ 9 part: `en-part-000`, `vi-part-000..006`, `zh-part-000`.

| Model | Trạng thái | Kích thước vector | NaN | Artifact |
|---|---|---:|---:|---|
| Qwen3-Embedding-0.6B | Hoàn tất 9/9 part; người dùng chạy interactive session và upload thủ công | 1.024 chiều | 0 | [Dataset Kaggle](https://www.kaggle.com/datasets/boinhbo/medxir-vectors-qwen3-0-6b), gồm manifest, 9 `.npy` và 9 parquet `chunk_id` |
| Qwen3-Embedding-4B | Ba shard Kaggle `s0/s1/s2` đều hoàn tất 3/3 part | 2.560 chiều | 0 | Ba dataset và archive liệt kê bên dưới |

Các embedding được tính với BF16; vector `.npy` lưu dạng float16. Tổng chunk và token trên manifest hai model khớp nhau. Các shard 4B đã kiểm shape, dtype và số hàng ID parquet so với manifest trước khi upload.

| Shard | Part | Dataset | Archive cục bộ |
|---|---|---|---|
| s0 | EN-000, VI-002, VI-005 | [medxir-vectors-qwen3-4b-s0](https://www.kaggle.com/datasets/boinhbo/medxir-vectors-qwen3-4b-s0) | `artifacts/kaggle/medxir-vectors-qwen3-4b-s0.zip` |
| s1 | VI-000, VI-003, VI-006 | [medxir-vectors-qwen3-4b-s1](https://www.kaggle.com/datasets/boinhbo/medxir-vectors-qwen3-4b-s1) | `artifacts/kaggle/medxir-vectors-qwen3-4b-s1.zip` |
| s2 | VI-001, VI-004, ZH-000 | [medxir-vectors-qwen3-4b-s2](https://www.kaggle.com/datasets/boinhbo/medxir-vectors-qwen3-4b-s2) | `artifacts/kaggle/medxir-vectors-qwen3-4b-s2.zip` |

Mỗi dataset 4B có `embed_manifest.json` và ba cặp `vectors/<part>.npy` / `vectors/<part>-ids.parquet`, tổng cộng 7 file theo Kaggle file listing. Các dataset là private của `boinhbo`.

### Bước tiếp theo

1. Đợi lần chạy retrieval 4B trên Kaggle (`boinhbo/medxir-retrieve-qwen3-4b`, version 4) hoàn tất; kiểm tra `retrieval_manifest.json` và bài nộp 1.200 query.
2. Nộp baseline 4B lên leaderboard, rồi ghi điểm vào mục này.
3. Sau baseline, so sánh 0.6B nếu thời gian cho phép. BM25 vẫn hoãn tới khi chốt và đánh giá tokenizer riêng EN/VI/ZH.

Query retrieval, benchmark chất lượng và submission chưa hoàn tất. [Lịch Road to AI 2026](https://r2ai.aiguru.com.vn/) ghi hạn Vòng 1 là 30/10/2026; kế hoạch embedding cũ đặt mốc nội bộ khoảng 26/10.

## 15. Nối dữ liệu và FAISS Qwen3-Embedding-4B — 23/10/2026

### Đã rà soát và chốt data contract

Tải đủ chunk manifest, `hash_groups.parquet` và cả chín chunk parquet từ output của kernel Kaggle `boinhbo/medxir-chunk-corpus` vào `artifacts/kaggle/chunk-corpus-v1/` để đối chiếu thực tế.

- Với từng part, `vectors/<part>.npy[row]`, `<part>-ids.parquet.chunk_id[row]` và `chunks/<lang>/part-NNN.parquet.chunk_id[row]` khớp chính xác theo thứ tự hàng.
- Đã kiểm tra đủ 2.621.553 hàng, 2.621.553 `chunk_id` duy nhất; không có ID vector thiếu hoặc lặp giữa các part. ID khớp công thức `text_sha256[:16] + "-" + order`; đủ 1.023.783 prefix hash, không collision và mọi thứ tự chunk liên tục.
- Mỗi `(text_sha256, rep_doc_id)` khớp `hash_groups.parquet`. Lúc truy hồi: hàng vector → `<part>-ids.parquet` → `chunk_id` → chunk gốc để lấy text; `text_sha256` nối hash group. Chỉ fan-out sang nhiều doc ID khi `fanout_safe=true`; nếu không, dùng representative ID. (Mục 16 thay FAISS row-map bằng cách này.)
- Hợp đồng đầy đủ và quy tắc nối nằm trong [docs/RETRIEVAL_DATA_CONTRACT.md](docs/RETRIEVAL_DATA_CONTRACT.md).

### Index 4B đã tạo

Thêm [scripts/build_faiss_flat_index.py](scripts/build_faiss_flat_index.py) và dependency tùy chọn `retrieval` (`faiss-cpu`). Script kiểm tra manifest, dtype/shape/vector hữu hạn, so sánh row order vector-ID-chunk, xác minh hash group rồi ghi index cùng row-map cho từng part.

- Đã build đủ 9 `IndexFlatIP`, 2.621.553 hàng, dimension 2.560, metric inner product; FAISS 1.15.1. Mỗi index part độc lập, có thể search top-k riêng rồi merge chính xác thành top-k toàn corpus.
- Index float32 chiếm 26.844.703.125 byte (~25,0 GiB) tổng. Build theo part để vừa RAM; mỗi file khoảng 2,1–3,4 GB.
- Nguồn `.npy` giữ float16. Khi build, từng batch được nạp thành float32 và chuẩn hóa lại; index lưu float32 để làm baseline chính xác. Chưa thử so sánh ranking thực tế với GPU float16 trên query cuộc thi.
- Index và row-map ở `artifacts/faiss/qwen3-embedding-4b/` (nằm trong `.gitignore`). Đã upload lên Kaggle dataset `boinhbo/medxir-faiss-qwen3-4b-fp32` ngày 23/10 (khoảng 25 GB). Từ 24/10 retrieval không còn dùng index này; sẽ xóa dataset sau khi lần chạy retrieval 24/10 hoàn tất.

Kiểm tra đầu ra đã xác nhận 9/9 index đọc được, dimension/metric/count đúng, row-map khớp file ID và truy vấn self-search trên vectors nguồn trả similarity 1,0. Chưa tạo query embedding, chưa chạy retrieval trên 1.200 query, chưa sinh submission hoặc benchmark chất lượng.

## 16. Retrieval Qwen3-Embedding-4B trên Kaggle — 24/10/2026

**Trạng thái:** lần chạy đầu tiên của `boinhbo/medxir-retrieve-qwen3-4b` (version 4, RTX Pro 6000, Internet tắt) đang chạy. Chưa có điểm và chưa có bài nộp đã kiểm tra.

**Lỗi trước đó:** version 1 đến 3 dừng ở ô cuối với `ValueError`. Nguyên nhân: `doc_ids` đọc từ parquet là mảng numpy, còn ô ghép bài nộp dùng `if not doc_ids:`, trên mảng nhiều phần tử thì Python báo lỗi. Đã đổi sang list Python và kiểm tra logic bằng pandas trên máy local. Log trên Kaggle rỗng trong các lần đó nên không đọc được lỗi từ phía Kaggle; lỗi chỉ thấy khi chạy interactive.

**Thiết kế hiện tại:** không dùng FAISS nữa.
- Mã hóa 1.200 query bằng Qwen3-Embedding-4B, theo đúng tiền tố `Instruct: … Query:` và pooling token cuối của model card, chuẩn hóa L2.
- Nạp từng part vector (`vectors/<part>.npy`, float16) từ ba dataset `medxir-vectors-qwen3-4b-s0..s2` lên GPU, tính tích vô hướng bằng float32, lấy top 20 mỗi part, rồi gộp thành top 20 toàn corpus. Đây là tìm kiếm chính xác, tương đương `IndexFlatIP`.
- Map hàng vector → `chunk_id` qua `<part>-ids.parquet`, rồi lấy chunk gốc từ `chunks/<lang>/part-NNN.parquet` và nhóm trùng từ `hash_groups.parquet`. Chỉ fan-out doc ID khi `fanout_safe=true`.
- `TOP_CHUNKS = 20` là giá trị đặt tạm, chưa tune.

**Lý do đổi khỏi FAISS:** index FAISS chỉ là bản sao của cùng vector float16. Runtime FAISS không có sẵn trên image RTX của Kaggle và gây lỗi import trong quá trình debug. Tìm bằng PyTorch cho cùng phép tính.

**Dọn dẹp sau khi lần chạy xong:** xóa `boinhbo/medxir-faiss-qwen3-4b-fp32` và `boinhbo/medxir-retrieval-runtime` trên Kaggle. Bản local `artifacts/faiss/` đã xóa ngày 25/10 để giải phóng 26 GB; dựng lại bằng `scripts/build_faiss_flat_index.py` từ `artifacts/kaggle/vectors-qwen3-4b-s*`.

**Cập nhật 25/10:** lần chạy encode trên Kaggle (`medxir-encode-queries-4b`) hoàn tất ở 495 giây, RSS đỉnh 5,7 GiB. Kernel tìm kiếm trên Kaggle (`medxir-retrieve-qwen3-4b`) chết ngay khi đọc file vector, không có log; probe `medxir-vector-read-probe` đọc đầy đủ một file vector cũng chết. Kết luận tạm: đọc file vector từ dataset mount trên Kaggle là điểm lỗi. Tìm kiếm chuyển sang máy local bằng `scripts/retrieve_local_4b.py` (CPU, float32, 141 giây). Bài nộp `artifacts/submission/medxir-qwen3-4b-submission.json` đủ 1.200 query, mỗi query 20 chunk, mọi `chunk_text` là nguyên văn. Đã nộp lên leaderboard, kết quả baseline 4B (chưa tune, `TOP_CHUNKS = 20`):

| Chỉ số | Giá trị |
|---|---:|
| FINAL_SCORE | **0,0818** |
| Document F2 (macro) | 0,1214 |
| Document Precision / Recall | 0,3238 / 0,1174 |
| Chunk F2 (macro) | 0,0422 |
| Chunk Precision / Recall | 0,0978 / 0,0436 |

So với bài #1 (BM25, FINAL_SCORE 0,019): khoảng gấp 4 lần. Recall document chỉ 0,12 nên phần lớn tài liệu đúng chưa được tìm thấy; precision chunk thấp hơn precision document nên nhiều chunk nộp không khớp đoạn tham chiếu.

### Tách danh sách doc khỏi danh sách chunk, thêm BM25 — 25/10/2026

`relevant_docs` và `relevant_chunks` được chấm độc lập, nên `scripts/retrieve_local_4b.py` giờ lưu top 500 chunk mỗi query (`artifacts/submission/candidates-4b-k500.npz`), xếp doc theo điểm chunk tốt nhất, rồi xuất N doc và C chunk tùy chọn (`--doc-counts`, `--chunk-counts`). Mọi bản bên dưới dùng cùng vector 4B; FINAL = trung bình Doc F2 và Chunk F2.

| Bản nộp | Doc P / R | Chunk P / R | Doc F2 | Chunk F2 | FINAL |
|---|---|---|---:|---:|---:|
| Baseline (≈17 doc, 20 chunk) | 0,324 / 0,117 | 0,098 / 0,044 | 0,121 | 0,0422 | 0,0818 |
| d50, 20 chunk | 0,240 / 0,242 | 0,098 / 0,044 | 0,207 | 0,0422 | 0,1245 |
| d100, 20 chunk | 0,186 / 0,339 | 0,098 / 0,044 | 0,248 | 0,0422 | 0,1453 |
| d200, 20 chunk | 0,134 / 0,445 | 0,098 / 0,044 | 0,261 | 0,0422 | 0,1517 |
| d300, 20 chunk | 0,108 / 0,508 | 0,098 / 0,044 | 0,254 | 0,0422 | 0,1480 |
| **d200, 100 chunk** | 0,134 / 0,445 | 0,074 / 0,134 | 0,261 | 0,0954 | **0,1783** |
| RRF dense+BM25 (1:1) d100 / d200 | 0,174 / 0,325; 0,125 / 0,430 | 0,089 / 0,042 | 0,235; 0,247 | 0,0401 | 0,1377; 0,1433 |
| Chỉ BM25 tiếng Việt, d200 | 0,086 / 0,299 | 0,055 / 0,025 | 0,172 | 0,0248 | 0,0982 |

Nhận xét: số doc tối ưu quanh 200; tăng số chunk làm Chunk F2 tăng mạnh (precision chỉ giảm 0,098 → 0,074 khi chunk tăng 5 lần, recall tăng 3 lần), nên còn dư địa với 200–300 chunk. RRF 1:1 với BM25 làm điểm giảm; BM25 đứng riêng đạt 0,098 (chỉ phủ chunk tiếng Việt). Số chunk tham chiếu mỗi query ước tính khoảng 50, suy từ recall/precision.

BM25 (`scripts/bm25_vi.py`, chạy trên Kaggle CPU bằng `configs/kaggle/bm25_vi.yaml`): âm tiết và cặp âm tiết liền kề, chuẩn hóa NFKC và vị trí dấu thanh, bỏ stopword và câu xã giao ở phía query, IDF/độ dài toàn corpus. Chạy BM25 trên máy local bị hết RAM hai lần; việc nặng chạy trên Kaggle (AGENT.md).

**Mã nguồn:** [scripts/build_retrieval_notebook.py](scripts/build_retrieval_notebook.py), [src/notebooks/kaggle_retrieve_qwen3_4b.ipynb](src/notebooks/kaggle_retrieve_qwen3_4b.ipynb), [configs/kaggle/retrieve_qwen3_4b_rtx6000.yaml](configs/kaggle/retrieve_qwen3_4b_rtx6000.yaml).

## Dọn dẹp artifacts và kế hoạch corpus v1.1 — 25/10/2026

**Đã làm:**
- Xóa `artifacts/crawl_full/` (plan 4,38 triệu URL cũ, không còn trong corpus).
- Xóa `artifacts/crawl/` (pilot 11/10). Trước khi xóa, đối chiếu với `dataset/links_corpus_vi_en_zh.parquet`: 10.000 tài liệu gồm 5.344 ID không có trong parquet, 4.372 ID đã có trong `corpus_v1` (4.353 giống hệt văn bản), 284 ID có trong parquet nhưng chưa có trong v1. Trong 284 ID này, 76 thuộc 120ask (nguồn bị chặn, loại), còn 208 dùng được, trong đó 140 có văn bản. 140 tài liệu đó được trích vào [artifacts/corpus_v1_patch/crawl_gap_v1_1.jsonl](artifacts/corpus_v1_patch/crawl_gap_v1_1.jsonl) (đã kiểm tra: id khớp parquet, url khớp 100%). Trạng thái của cả 284 ID nằm ở [crawl_gap_status.csv](artifacts/corpus_v1_patch/crawl_gap_status.csv).
- Đã xóa `.work/` (debug/smoke) và `artifacts/readiness_check/`.

**Quyết định (25/10):**
- Không upload `cc_v1` và `pharmacity_v1` lên Drive: ước tính hơn 10 giờ với tốc độ ~7,8 MB/phút, và chưa có việc cần dùng ngay. Đã xóa bản `cc_v1` upload dở trên Drive.
- `wayback_v1` (9 MB) đã upload lên `gdrive:coordination/recrawl_20261015/local_only/wayback_v1` và kiểm tra khớp (435 file, 0 khác biệt).
- Giữ bản local của `cc_v1` (4,7 GB) và `pharmacity_v1` (772 MB). Đây là bản duy nhất của raw; ổ đĩa còn trống 922 GB nên không cần xóa.
- Không reparse ZH và không làm v1.1 bây giờ. Corpus v1 giữ nguyên làm nền cho thí nghiệm reranker. Tác động của ZH parser v2.3 chưa đo được; làm sau khi có kết quả reranker.
- 140 tài liệu trong patch chưa nhập vào corpus, vì nhập sẽ kéo theo chunk và embed mới. File patch giữ lại để làm sau.

**Kế hoạch corpus v1.1 (hoãn, chỉ làm nếu còn thời gian sau reranker):**
1. Thêm 140 tài liệu trong `crawl_gap_v1_1.jsonl`.
2. Thêm tài liệu VI từ 7 batch upload sau khi build v1 (4.753 ID chưa có trong v1, 4.733 có văn bản). Cần loại nguồn bị chặn trước khi nhập.
3. Reparse ZH từ parser v2.3 lên v2.4 trở lên (khoảng 72k tài liệu, raw ZH có trên Drive). Cần đo số văn bản thực sự đổi trước.
4. Chunk và embed phần đổi: chunk ID là `text_sha256[:16]-order`, nên văn bản không đổi thì giữ vector cũ.
5. Đăng ký v1.1 là một version mới của dataset Kaggle, giữ v1 để tái lập.
Pending 26.179 ID (suckhoedoisong, longchau theo quota) không đưa vào v1.1 vì cần crawl lại và đang bị chặn.

### Giới hạn kích thước bài nộp — 25/10/2026

Dashboard từ chối file trên 100 MB. Bản d200 với 200 chunk/query nặng 121 MB (đã nén mức 9, JSON gọn), nên không nộp được. Chunk text phải nguyên văn nên không rút gọn được nội dung. Chọn 150 chunk/query: 90 MB, nằm trong giới hạn.

Ghi chú: file `medxir-qwen3-4b-d200-c100-submission.zip` được tạo lại bằng nén mức 9 nên khác byte với bản đã nộp (59,7 MB thay vì 60,2 MB). Nội dung chunk và doc giống hệt.

| Bản | Kích thước | Ghi chú |
|---|---:|---|
| d200-c100 | 59,7 MB | Đã nộp, FINAL 0,1783 |
| **d200-c150** | 90,0 MB | **Đã nộp, FINAL 0,1847 (tốt nhất hiện tại)** |
| d200-c200 | 121 MB | Vượt giới hạn, không nộp được |

**Kết quả d200-c150 (đã nộp):** FINAL 0,1847; Doc F2 0,2612; Chunk F2 0,1081 (P 0,0665, R 0,1696).

Xu hướng Chunk F2 theo số chunk/query (cùng 200 doc): 20 → 0,042; 100 → 0,095; 150 → 0,108. Mỗi 50 chunk thêm tăng ít dần (+0,013 ở 100→150), trong khi precision giảm. Chunk tiếp theo bị giới hạn 100 MB chặn lại. Các cải thiện tiếp theo cần đến từ thứ tự xếp hạng (reranker, hybrid), không chỉ từ số lượng.

### Benchmark reranker đa ngôn ngữ — 26/10/2026 (chưa hoàn tất)

Gói model (`boinhbo/medxir-package-rerankers`, exp_0002, 12,8 GB): Qwen3-Reranker-0.6B (Apache-2.0, 2025-05-29), Qwen3-Reranker-4B (Apache-2.0, 2025-06-03), bge-reranker-v2-m3 (Apache-2.0, 2024-03-15), jina-reranker-v3.5 (CC-BY-NC-4.0, 2026-07-14, không thương mại; cần xác nhận quy định cuộc thi).

Chạy benchmark (exp_0004, 100 query = 15.000 cặp, top-150 dense):
- Phần cứng thực tế là **Tesla T4**, không phải RTX Pro 6000. Config yêu cầu `NvidiaRtxPro6000` nhưng CLI không áp dụng được; notebook không kiểm tra GPU nên không báo lỗi.
- Qwen3-Reranker-0.6B: chạy được, nhưng 2,1 cặp/giây (bf16 không có trên T4). Với 180.000 cặp của 1.200 query là khoảng 24 giờ.
- bge-reranker-v2-m3: chạy được, 4,4 cặp/giây, VRAM 3,5 GB.
- Qwen3-Reranker-4B: hết VRAM trên T4 (14,5 GB).
- jina-reranker-v3.5 (listwise, 150 tài liệu một ngữ cảnh): hết VRAM, cần kiến trúc chia nhỏ hoặc GPU lớn hơn.

Đo đồng thuận (không phải chất lượng, vì chưa có nhãn): hai model cho xếp hạng tương quan Spearman trung bình 0,51 và trùng 8,6/20 chunk đầu.

Cần làm: chạy lại trên RTX Pro 6000 chọn trong giao diện Kaggle (không qua CLI); dùng fp16 khi GPU không hỗ trợ bf16; thêm kiểm tra tên GPU trong notebook; chỉ dùng được Qwen3-4B và bge trên GPU lớn hơn.

### Rerank toàn bộ 1.200 query trên RTX Pro 6000 — 27/10/2026

Chạy `exp_0006` (NVIDIA RTX PRO 6000 Blackwell, bf16, Internet OFF, có cuộc thi ARC AGI 3 gắn vào). 180.000 cặp (1.200 query × top-150 dense) cho mỗi model. Kết quả lưu ở `artifacts/rerank/results/full/`.

| Model | Thời gian | Cặp/giây | VRAM đỉnh |
|---|---:|---:|---:|
| bge-reranker-v2-m3 | 339 s | 532 | 1,5 GB |
| Qwen3-Reranker-0.6B | 1.140 s | 158 | 13,1 GB |
| Qwen3-Reranker-4B | 4.750 s | 38 | 20,6 GB |

Jina v3.5 không chạy (CC-BY-NC-4.0, chờ BTC xác nhận).

Bài nộp (dựng bằng `scripts/build_rerank_submission.py`, 150 chunk đã sắp lại theo điểm rerank, 200 doc). Danh sách doc lấy thêm các ứng viên dense hạng 150 trở đi (không rerank) để đủ 200 doc như các bản d200 trước:
- `medxir-rr4b-d200-c150-submission.zip` (89 MB): Qwen3-4B, chỉ rerank.
- `medxir-rr06b-d200-c150-submission.zip` (89 MB): Qwen3-0.6B.
- `medxir-rrbge-d200-c150-submission.zip` (88 MB): bge-m3.
- `medxir-rr4b-f05-d200-c150-submission.zip` (89 MB): 4B + dense, trọng số dense 0,5.
- `medxir-rr4b-f10-d200-c150-submission.zip` (89 MB): 4B + dense, trọng số dense 1,0.

Thứ tự nộp đề xuất: rr4b → rr06b → rrbge → rr4b-f05 tùy kết quả trước đó. Mốc hiện tại: d200-c150 dense, FINAL 0,1847.
