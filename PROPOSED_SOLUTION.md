# Giải pháp ViBioMIR — Version 2

**Phiên bản:** 2.0  
**Ngày:** 12/10/2026 (UTC+07:00)  
**Trạng thái:** Đề xuất thiết kế; chưa triển khai hoặc benchmark pipeline này.

## 1. Hướng triển khai đã chốt

Pipeline dự kiến:

```text
Crawl → Clean bằng rule theo host/template → Bổ sung metadata
      → Gắn nhóm ICD-10 → Chunk → BM25 + Qwen 4B retrieval
      → Chọn tài liệu và đoạn nguồn → JSON/ZIP bài nộp
```

Xây dựng toàn bộ pipeline trên tiếng Việt trước và xuất được bài nộp hợp lệ. Sau đó bổ sung tiếng Anh, đánh giá mức tăng chất lượng, rồi mới bổ sung tiếng Trung. Mỗi vòng giữ được một baseline hoàn chỉnh để đối chiếu và quay lại khi thử nghiệm mới chưa tốt hơn.

**Ưu tiên triển khai hiện tại — 23/10/2026:** nộp dense baseline sớm bằng Qwen3-Embedding-4B trên vectors đã tạo cho đủ ba ngôn ngữ. BM25 tạm hoãn cho tới khi chốt và đánh giá tokenizer EN/VI/ZH; chưa để nhánh này chặn submission đầu tiên.

Trong tài liệu này, “Qwen 4B” được hiểu là **Qwen3-Embedding-4B** cho nhánh dense retrieval. Quyết định ngày 17/10/2026: embed toàn bộ corpus (không chỉ ứng viên BM25); chạy pilot để so Qwen3-Embedding-4B với Qwen3-Embedding-0.6B và model khác trước khi chốt; giữ số chiều vector 2560. Model reranker là lựa chọn riêng, chưa chốt. Model nhỏ dùng gắn ICD-10 cũng được chọn riêng theo chi phí và chất lượng.

## 2. Corpus mới và phạm vi từng vòng

Theo thông báo cập nhật của BTC ngày 12/10/2026, corpus mới gồm:

| Ngôn ngữ | Số URL | Thứ tự xử lý |
| --- | ---: | --- |
| Tiếng Việt | 740.598 | Vòng 1 |
| Tiếng Anh | 240.000 | Vòng 2 |
| Tiếng Trung | 240.000 | Vòng 3 |
| **Tổng** | **1.220.598** | |

Số liệu trên lấy từ file hiện hành (phân nhóm theo nguồn, chưa phải nhận diện ngôn ngữ từng bài). Thông báo ngày 12/10 ghi 1.194.469 URL; file nhiều hơn 26.129 dòng, xem [AUDIT_2026-10-15.md](docs/AUDIT_2026-10-15.md).

Nguồn: [AIGuruTinix/ViBioMIR](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR), file `links_corpus_vi_en_zh.parquet`, được lưu cục bộ tại `dataset/links_corpus_vi_en_zh.parquet`.

File cục bộ đã được kiểm tra: 1.220.598 dòng, 1.220.598 ID duy nhất, 94 host, có hai cột `id`, `url`. SHA-256 là `87e44a792d7a99fb0fde5dff7f09f1a38a0a1fdf02ddd1ac93a005736e00b0a7` (theo [RECRAWL_RUNBOOK.md](docs/RECRAWL_RUNBOOK.md)).

Nhóm quyết định crawl lại toàn bộ corpus mới. ID chính thức phải được giữ nguyên; không đánh số lại theo dòng, shard hoặc ngôn ngữ. Plan mới không dùng checkpoint hay danh sách loại trừ của lần crawl trước.

Parquet không có cột ngôn ngữ. Cần lập manifest ngôn ngữ từ thông tin nguồn của BTC nếu có; nếu không, dùng mapping host làm nhãn dự kiến và kiểm tra bằng nội dung thực tế sau crawl. Trang đa ngôn ngữ hoặc không chắc chắn phải được ghi rõ. Không suy ra ngôn ngữ chỉ từ miền `.com` hay `.vn`.

### Ba vòng phát triển

| Vòng | Corpus dùng truy hồi | Đầu ra bắt buộc |
| --- | --- | --- |
| VI | Tiếng Việt | Baseline và bài nộp cho đủ 1.200 query |
| VI+EN | Giữ VI, thêm tiếng Anh | Bài nộp mới và báo cáo thay đổi so với VI |
| VI+EN+ZH | Giữ VI+EN, thêm tiếng Trung | Bài nộp mới và báo cáo thay đổi so với VI+EN |

Không chờ xử lý cả ba ngôn ngữ mới tạo bài nộp đầu tiên. Khi thêm một ngôn ngữ, chỉ xử lý và embedding phần mới nếu phiên bản clean, chunking và model không đổi.

## 3. Crawl và lưu dữ liệu nguồn

Lưu HTML hoặc response gốc để sửa rule clean mà không phải tải lại. Mỗi URL cần có ID, URL gốc, URL sau redirect, thời điểm tải, mã HTTP, trạng thái và lỗi. Crawl cần checkpoint, resume và cơ chế tránh gọi lại URL đã hoàn thành trong lần crawl mới.

Dữ liệu trên Drive dự kiến tổ chức dưới ba thư mục `vi/`, `en/`, `zh/`. Sau khi crawl xong cả hai shard, dữ liệu được upload lên Kaggle dataset (một hoặc hai dataset) để notebook dùng. Bên trong mỗi ngôn ngữ phân biệt dữ liệu nguồn, văn bản clean và artifact phục vụ retrieval; lưu theo shard/batch để xử lý tiếp từng phần.

**Trạng thái hiện tại:** ba thư mục ngôn ngữ đã được tạo; crawler gốc chưa được cấu hình để upload theo cấu trúc này. Phần chia crawl giữa các máy phải bảo đảm mỗi ID chỉ có một máy phụ trách.

Báo cáo độ phủ theo ngôn ngữ và host: tổng URL, tải thành công, có nội dung chính, lỗi, pending và trang cần kiểm tra. Số URL đã xử lý không đồng nghĩa với số tài liệu có thể dùng để retrieval.

## 4. Clean bằng rule: AI phân tích mẫu, code xử lý hàng loạt

### 4.1. Khảo sát host và template

Lấy mẫu HTML theo **host và dạng trang**, không chỉ một trang trên mỗi host. Phân nhóm ban đầu bằng đường dẫn URL, sau đó kiểm tra cấu trúc DOM để xác định template thực tế. Một host có thể có bài viết, hỏi đáp, trang danh mục hoặc template cũ/mới; đường dẫn giống nhau chưa chắc có cùng layout.

Cho AI phân tích mẫu và đề xuất:

- Vùng nội dung chính, tiêu đề và heading.
- Cấu trúc câu hỏi, câu trả lời và phần trả lời bổ sung.
- Selector và quy tắc bỏ menu, quảng cáo, bài liên quan, footer hoặc widget.
- Dấu hiệu trang lỗi, redirect về trang chủ hoặc nội dung không đầy đủ.
- Cách giữ danh sách, bảng, chú thích và thứ tự khối văn bản.

AI hỗ trợ phân tích và viết rule. Việc clean toàn corpus chạy bằng code có thể kiểm tra, tái lập và đánh phiên bản. Không dùng AI viết lại văn bản nguồn làm `chunk_text`.

Model hỗ trợ phát triển/parser cũng cần được đối chiếu với quy định sử dụng model trong [OVERVIEW.md](OVERVIEW.md); nếu vai trò công cụ hỗ trợ phát triển chưa rõ, cần xác nhận với BTC.

### 4.2. Kiểm chứng và áp dụng rule

1. Tạo mẫu đại diện cho từng template và giữ một phần mẫu để kiểm tra độc lập.
2. Viết rule, so sánh HTML với nội dung sau clean, kiểm tra mất đoạn và nhiễu còn sót.
3. Chạy trên một batch, thống kê nội dung rỗng, độ dài bất thường và lỗi template.
4. Mở rộng xử lý khi batch đạt chất lượng; trang chưa nhận diện được giữ trạng thái cần review.
5. Khi rule đổi, chạy lại clean từ HTML đã lưu và chỉ cập nhật artifact chịu ảnh hưởng.

Giữ nguyên phủ định, tên thuốc, liều lượng, đơn vị xét nghiệm, tuổi và diễn tiến thời gian. Tránh rule xóa mạnh làm mất câu trả lời hoặc bảng y khoa. Nội dung không đạt chất lượng được đánh dấu để kiểm tra, không âm thầm coi là tài liệu đã clean thành công.

## 5. Metadata: tối thiểu trước, mở rộng sau

Metadata bổ sung như tác giả, ngày xuất bản, chuyên khoa và thực thể y khoa **chưa chốt**, sẽ thiết kế sau khi có corpus clean và baseline.

Ngay từ đầu vẫn giữ các trường cần để quản lý dữ liệu và truy ngược nguồn:

| Nhóm | Trường dự kiến |
| --- | --- |
| Định danh | `doc_id`, URL gốc, URL cuối, host |
| Nội dung | tiêu đề nếu lấy được, ngôn ngữ dự kiến/thực tế, văn bản nguồn |
| Truy nguyên | locator HTML, thời điểm crawl, phiên bản rule, hash nội dung |
| Chất lượng | trạng thái crawl/clean, cảnh báo, template nhận diện |
| Chunk | `chunk_id`, `doc_id`, thứ tự, section, vị trí trong văn bản nguồn |

Các field không lấy được để trống và ghi rõ nguồn gốc; không tự suy diễn thành metadata chắc chắn. Khi deduplicate, giữ mapping về tất cả ID chính thức và đánh giá cách chọn ID khi xuất kết quả.

## 6. Gắn nhóm ICD-10

Mục tiêu là tạo nhãn chủ đề hỗ trợ tổ chức corpus, phân tích lỗi và thử cải thiện retrieval. Nhãn này không thay thế nhãn liên quan của cuộc thi và không phải chẩn đoán y khoa.

Hướng thử nghiệm ban đầu:

1. Chọn phiên bản ICD-10 và cấp nhóm phù hợp; xây bảng mã, tên nhóm, mô tả và thuật ngữ tương ứng. Ghi nguồn và phiên bản bảng nhãn.
2. Dùng BM25 tìm nhóm phù hợp với tiêu đề và nội dung tài liệu.
3. Dùng model embedding nhỏ so sánh văn bản với mô tả nhóm, rồi hợp nhất hoặc chọn lại ứng viên nhãn.
4. Lưu nhiều nhãn nếu tài liệu đề cập nhiều nhóm; giữ điểm từng nhánh, phiên bản model và phương pháp gắn nhãn. Điểm similarity chưa được coi là xác suất.
5. Kiểm tra thủ công một mẫu, đặc biệt trang về thuốc, xét nghiệm và tài liệu nhiều chủ đề. Cho phép `unknown` khi chưa đủ bằng chứng.

Tên/mô tả nhãn cần phù hợp ngôn ngữ tài liệu và truy vấn. Không cộng trực tiếp điểm BM25 và cosine nếu chưa hiệu chỉnh thang điểm.

Ban đầu ICD-10 được dùng làm metadata và tín hiệu hỗ trợ. Khi thử truy hồi theo nhóm, luôn giữ nhánh tìm trên toàn corpus. So sánh cấu hình có/không dùng tag; lọc cứng sai nhóm có thể làm mất tài liệu liên quan. Bước tag không nên chặn việc xuất baseline tiếng Việt đầu tiên khi còn đang thử nghiệm.

## 7. Chunking và BM25 + Qwen 4B retrieval

### 7.1. Chuẩn bị chunk

Giữ nguyên bài hỏi đáp ngắn khi phù hợp giới hạn đầu vào; chia bài dài theo heading và đoạn, rồi mới chia theo câu nếu cần. Chốt độ dài và overlap sau khi đo phân bố văn bản và thử retrieval trên mẫu.

Mỗi chunk có hai biểu diễn:

- `retrieval_text`: có thể thêm tiêu đề và ngữ cảnh section để lập index.
- `source_text`: văn bản trích từ tài liệu, kèm vị trí và thứ tự để dựng chunk nộp.

Chunk nộp lấy từ nguồn, không lấy từ phần mô tả ICD-10, bản dịch hay văn bản sinh. Giữ liên kết với đoạn liền kề để thử mở rộng cửa sổ nguồn khi cần.

### 7.2. Hai nhánh tìm kiếm

**BM25 — giai đoạn sau:** lập index từ văn bản clean/chunk, dùng tokenization phù hợp từng ngôn ngữ. Tạm hoãn đến khi có lựa chọn tokenizer riêng cho EN/VI/ZH và có thể đánh giá chúng; khi triển khai, đo BM25 với query tiếng Việt gốc và với bản dịch query.

**Dense retrieval:** dùng [Qwen3-Embedding-4B](https://huggingface.co/Qwen/Qwen3-Embedding-4B), model embedding đa ngôn ngữ, cho vectors corpus và query. Full corpus đã embed; không chạy lại chunk embedding. Tạo exact FAISS `IndexFlatIP` riêng từng part để giới hạn RAM khi build, với row-map Parquet nối số hàng FAISS tới `chunk_id` và metadata chunk. Nguồn vector vẫn float16; index đầu tiên float32 và chuẩn hóa lại sau khi nạp để khử norm drift do lưu float16. Query phải dùng cùng model/config và được chuẩn hóa. Merge top-k từ từng part theo inner product. Chi tiết khóa nối ở [RETRIEVAL_DATA_CONTRACT.md](docs/RETRIEVAL_DATA_CONTRACT.md).

Hợp nhất thứ hạng bằng RRF là cấu hình xuất phát. Chốt số ứng viên, trọng số và ngưỡng chọn bằng thực nghiệm. Khi một nhánh có nhiều bản dịch query, kiểm soát tổng trọng số của nhánh đó.

Với các index ngôn ngữ riêng, thử tìm ứng viên trong từng index rồi hợp nhất để đo phần ngôn ngữ mới. Giữ cùng cấu hình embedding cho các index được so sánh. Theo dõi số document khác nhau, tránh để nhiều chunk của một bài chiếm hết tập ứng viên.

### 7.3. Chọn kết quả

Tổng hợp điểm chunk thành điểm document; thử điểm chunk tốt nhất và tổng hợp một số đoạn bằng chứng. Chọn document và chunk theo cấu hình đã đánh giá. Reranker hoặc dịch query được thêm trong thử nghiệm riêng nếu lỗi thực tế cho thấy cần thiết.

Theo [EVALUATION.md](docs/EVALUATION.md), Precision, Recall và F2 được tính macro riêng cho document và chunk. F2 ưu tiên recall nhưng vẫn giảm khi thêm quá nhiều kết quả sai. Chunk được đối chiếu trong cùng `doc_id`, dùng overlap LCS token đạt ít nhất 40% sau chuẩn hóa.

## 8. Đo đóng góp khi thêm ngôn ngữ

### 8.1. Giữ phép so sánh có thể diễn giải

Lưu baseline VI trước khi thêm EN, rồi lưu baseline VI+EN trước khi thêm ZH. Mỗi lần thay corpus, giữ cùng tập query, bộ nhãn đánh giá, model và cấu hình chọn kết quả để có phép so sánh ban đầu. Việc tune lại cấu hình là thử nghiệm tiếp theo, ghi riêng.

| Thực nghiệm | Mục đích |
| --- | --- |
| VI: BM25, Qwen 4B, hybrid | Đo đóng góp từng nhánh trên baseline đầu tiên |
| VI+EN so với VI | Đo lợi ích bổ sung của tài liệu tiếng Anh |
| VI+EN+ZH so với VI+EN | Đo lợi ích bổ sung của tài liệu tiếng Trung |
| Có/không ICD-10 trên cùng corpus | Đo tác dụng của tag và nguy cơ mất recall |
| Query gốc/có bản dịch trên cùng corpus | Kiểm tra lexical match và retrieval xuyên ngôn ngữ |

Báo cáo document/chunk Precision, Recall và F2; recall ứng viên trước chọn kết quả; tài liệu đúng mới theo ngôn ngữ; tài liệu đúng cũ bị mất; độ phủ crawl/clean; dung lượng index và thời gian xử lý.

Dùng cùng tập tham chiếu đa ngôn ngữ để so recall giữa các vòng. Nếu chỉ có nhãn thủ công trong pool ứng viên, gọi rõ là **recall trên pool đã đánh giá**. Nếu dashboard chỉ trả điểm tổng, không suy ra recall riêng từ mức tăng/giảm điểm đó. Không đổi bộ nhãn hoặc chỉ giữ nhãn thuộc ngôn ngữ hiện tại rồi gọi đó là phép so sánh recall giữa các vòng.

### 8.2. Diễn giải khi recall không tăng

Mục tiêu là kiểm tra tài liệu EN/ZH có giúp tìm thêm kết quả liên quan cho query tiếng Việt hay không. **Recall không tăng là tín hiệu cần phân tích, chưa đủ kết luận model xử lý kém ngôn ngữ mới.**

Các khả năng cần phân biệt:

- Tài liệu liên quan ở phần mới chưa crawl được hoặc bị clean mất nội dung.
- Corpus mới không bổ sung nhiều nhãn liên quan cho các query đang đánh giá.
- Chunking hoặc tokenization làm mất bằng chứng cần tìm.
- BM25 thiếu lexical match; dense model chưa nối được query VI với tài liệu EN/ZH.
- Ứng viên đúng đã được tìm nhưng mất ở bước fusion, tag ICD-10, reranking hoặc cắt top-K.
- Tài liệu mới cạnh tranh với ứng viên đúng cũ khi ngân sách kết quả giữ nguyên.
- Nhãn đánh giá chưa đủ để nhận biết các tài liệu mới có liên quan.

Để kiểm tra giả thuyết về model, chọn các tài liệu EN/ZH đã được xác nhận liên quan và clean đúng, rồi đo khả năng tìm chúng ở từng candidate. So query VI gốc với bản dịch đúng, so BM25 với dense, và kiểm tra kết quả trước/sau fusion. Chỉ quy lỗi cho khả năng ngôn ngữ khi đã tách được các yếu tố này.

Nếu phiên bản mở rộng chưa tốt hơn, giữ bài nộp baseline tốt nhất trong khi sửa nguyên nhân; vẫn lưu dữ liệu mới để tiếp tục thử nghiệm.

## 9. Artifact và bài nộp

Mỗi vòng lưu manifest ID, dữ liệu clean, chunk, embedding/index, kết quả retrieval và cấu hình. Mỗi index part là file FAISS (`.faiss`) kèm `row_map.parquet`; map này trỏ tới chunk parquet gốc bằng `chunk_id`. Giữ `hash_groups.parquet` làm bảng ánh xạ document và fan-out. Cache theo corpus/chunk version và model revision; tiếp tục từng part sau gián đoạn.

Tên bài nộp dự kiến:

- `submission_vi.json` / `.zip`.
- `submission_vi_en.json` / `.zip`.
- `submission_vi_en_zh.json` / `.zip`.

Theo [SUBMISSION.md](docs/SUBMISSION.md), mỗi JSON phải có đủ 1.200 query và các trường `id`, `relevant_docs`, `relevant_chunks`; mục không có kết quả vẫn có danh sách rỗng. Document ID phải thuộc corpus mới; kiểu ID cần tuân theo đặc tả/scorer chính thức vì ví dụ chuỗi và mô tả số nguyên trong tài liệu hiện chưa nhất quán.

`chunk_text` phải trích từ tài liệu gốc. Kiểm tra ID, schema, nguồn chunk, query bị thiếu và bản ghi trống trước khi ZIP. Mỗi ZIP chứa duy nhất một JSON ở thư mục gốc.

## 10. Thứ tự công việc

1. Corpus v1 đã chunk và embed đầy đủ VI/EN/ZH bằng Qwen3-Embedding-4B.
2. Tạo exact `IndexFlatIP` theo từng part, xác minh row-map và lưu index manifest.
3. Encode query bằng đúng 4B, truy hồi dense trên các part, hợp nhất candidate và xuất JSON đủ 1.200 query.
4. Kiểm tra submission theo schema, ID và nội dung chunk nguồn; lưu baseline trước khi tinh chỉnh.
5. So sánh Qwen3-Embedding-0.6B với 4B trên cùng tập query/corpus khi baseline chạy được.
6. Thiết kế tokenizer riêng EN/VI/ZH, sau đó mới triển khai BM25 và đánh giá hybrid.
7. Dùng lỗi retrieval quan sát được để quyết định có cần query translation, ICD-10 hay reranker.

Các cỡ batch, dimension, chunk size, top-K, model nhỏ gắn tag và thời gian embedding chưa chốt. Đo trên mẫu đại diện trước khi chạy quy mô lớn. Theo [OVERVIEW.md](OVERVIEW.md), model phải đáp ứng quy định công khai, tối đa 15B và phát hành trước 01/08/2026; lưu revision và bằng chứng ngày phát hành khi chọn checkpoint.

**Tiêu chí tiến độ:** mỗi ngôn ngữ thêm vào phải có dữ liệu có thể truy nguyên, index có thể dùng lại, bài nộp hợp lệ và báo cáo đóng góp so với baseline trước đó.
