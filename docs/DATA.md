# Data

## Dữ liệu cuộc thi

**Nguồn dữ liệu chính thức của cuộc thi:**  
Bạn có thể truy cập và tải toàn bộ Dataset trên HuggingFace tại đây:  
[**Truy cập HuggingFace Dataset (ViBioMIR)**](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR)

**Ban Tổ chức cung cấp:**

- **Danh sách nguồn dữ liệu:** Các liên kết trong `links_corpus.parquet` để các đội chủ động thu thập, xử lý và xây dựng cơ sở tri thức phục vụ truy hồi.
- **Bộ dữ liệu kiểm thử (test set):** Tập câu hỏi bằng tiếng Việt, được sử dụng làm căn cứ chấm điểm và đánh giá hệ thống của các đội thi.

Ban Tổ chức không cung cấp quy trình embedding/indexing sẵn có. Các đội thi được toàn quyền chủ động trong việc:

- Xây dựng hoặc lựa chọn mô hình embedding đa ngôn ngữ phù hợp với dữ liệu y học.
- Thiết kế chiến lược lập chỉ mục và tìm kiếm.
- Xây dựng cơ chế reranking, hậu xử lý.
- Các tập dữ liệu mở khác về y học mà đội thi có thể hợp pháp sử dụng để huấn luyện/đánh giá bổ trợ.

### Tập câu hỏi truy vấn

Tập câu hỏi truy vấn được cung cấp trong tệp `query.parquet`, gồm:

- **id:** Mã định danh của truy vấn, kiểu số nguyên (integer).
- **query:** Nội dung truy vấn y khoa bằng tiếng Việt, kiểu chuỗi (string).

```bash
{
  "id": <integer>,
  "query": "<string>"
}
```

**Ví dụ:**

```json
{
  "id": 1,
  "query": "Cần làm gì đối với tình trạng tắc nghẽn đường tiết niệu do sỏi thận?"
}
```

### Nguồn dữ liệu được cung cấp

Ban Tổ chức cung cấp danh sách URL của các bài viết y khoa trong tệp `links_corpus.parquet`, gồm:

- **id:** Mã định danh của nguồn, kiểu số nguyên (integer).
- **url:** URL dẫn tới bài viết gốc, kiểu chuỗi (string).

```bash
{
  "id": <integer>,
  "url": "<string>"
}
```

**Ví dụ:**

```json
{
  "id": 1,
  "url": "https://example.com/article"
}
```

Các đội tự thực hiện việc thu thập nội dung từ các URL được cung cấp, tiền xử lý, phân đoạn và xây dựng cơ sở tri thức phục vụ truy hồi.

Trong phiên bản đánh giá này, mọi nhãn tài liệu đều sử dụng trường `id` kiểu số nguyên có trong `links_corpus.parquet`. Tài liệu nằm ngoài danh sách này không được tính là nhãn dương.
