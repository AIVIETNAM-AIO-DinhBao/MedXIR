# Tiến trình MedXIR

**Cập nhật:** 10/10/2026 (UTC+07:00).  
**Giai đoạn hiện tại:** khảo sát nguồn và kiểm chứng parser trên pilot; chưa crawl toàn bộ corpus.

Tài liệu này ghi kết quả đã thu được. Các bước thiết kế chưa triển khai được đánh dấu riêng; hướng giải quyết tổng thể nằm trong [PROPOSED_SOLUTION.md](PROPOSED_SOLUTION.md).

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

Đã thống nhất nguyên tắc lưu raw cùng ID/URL/trạng thái trước khi parse để có thể sửa parser mà không tải lại. Hướng chia việc nhiều máy là chia shard theo ID chính thức, chẳng hạn `doc_id % 128`; đây mới là thiết kế, chưa có crawler phân tán. Tốc độ theo host cần được quản lý tổng thể khi nhiều máy cùng chạy.

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

## 7. Bước tiếp theo — chưa thực hiện

1. Duyệt mẫu đại diện từng template, ưu tiên cảnh báo và vùng ngoài selector; ghi rõ template nào đủ tin cậy để crawl.
2. Viết crawler theo đợt: giữ ID chính thức, lưu raw/trạng thái, resume, retry có giới hạn, chia shard và kiểm soát tốc độ theo host.
3. Crawl một đợt mở rộng để phát hiện template mới và đo throughput, dung lượng, tỷ lệ tải/parse trước khi mở rộng toàn bộ nguồn truy cập được.
4. Xử lý ngoại lệ theo độ phủ và chi phí; ưu tiên nội dung text/QA có thể lấy ổn định.
5. Thiết kế chunking từ các block sạch, sau đó triển khai sparse + dense baseline và đầu ra theo đặc tả nộp bài.

**Chưa hoàn thành:** crawl toàn bộ, chunking, indexing, retrieval, reranking, benchmark và bài nộp.
