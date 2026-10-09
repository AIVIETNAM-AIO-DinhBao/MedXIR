# Evaluation

## Phương pháp đánh giá

Hiệu suất hệ thống trên nhiệm vụ truy hồi được đánh giá ở hai cấp độ: tài liệu và đoạn nội dung, bằng các chỉ số Độ chính xác (Precision), Độ bao phủ (Recall) và F2. Các chỉ số được tính riêng cho từng truy vấn, sau đó lấy trung bình trên toàn bộ tập câu hỏi (macro-average).

### 3.1 Truy hồi tài liệu (cấp document)

Với mỗi truy vấn, gọi **D** là tập tài liệu được truy hồi và **G** là tập tài liệu liên quan theo nhãn tham chiếu.

| Precision = | \|D ∩ G\| |
| ----------- | :-------: |
| \|D\|       |           |

| Recall = | \|D ∩ G\| |
| -------- | :-------: |
| \|G\|    |           |

### 3.2. Quy tắc xác định một đoạn văn bản được truy hồi đúng

Mỗi chunk đội thi nộp được đối chiếu với các đoạn tham chiếu thuộc cùng câu hỏi và cùng `doc_id`.

Trước khi đối chiếu, văn bản hai phía được chuẩn hóa theo các bước sau:

- Chuẩn hóa Unicode theo NFKC.
- Giải mã HTML entity, chuyển văn bản về chữ thường, gộp các khoảng trắng liên tiếp và bỏ khoảng trắng ở đầu, cuối đoạn.

Gọi **c** là chunk đội thi nộp và **g** là chunk tham chiếu. Sau bước chuẩn hóa văn bản, độ trùng khớp được tính như sau:

| overlap(c,g) = | LCStokens(c,g) |
| -------------- | :------------: |
| \|g\|          |                |

Trong đó, **LCStokens(c,g)** là độ dài của dãy token con chung dài nhất giữa *c* và *g*, trong đó thứ tự token được giữ nguyên nhưng các token không bắt buộc phải liên tiếp.

Một chunk được tính là đúng nếu overlap với ít nhất một chunk tham chiếu đạt **từ 40% trở lên**.

### 3.3. Đánh giá ở cấp đoạn nội dung

| Precision =               | Số chunk được tính là đúng |
| ------------------------- | :------------------------: |
| Tổng số chunk đội thi nộp |                            |

| Recall =                 | Số chunk tham chiếu đã được tìm thấy |
| ------------------------ | :----------------------------------: |
| Tổng số chunk tham chiếu |                                      |

### 3.4. Điểm F2

Ở mỗi cấp đánh giá, với từng câu hỏi, F2 được tính từ Precision và Recall:

| F2 =                   | 5 × Precision × Recall |
| ---------------------- | :--------------------: |
| 4 × Precision + Recall |                        |

Precision, Recall và F2 cuối cùng ở mỗi cấp đánh giá được tính bằng trung bình cộng giá trị tương ứng trên toàn bộ câu hỏi (macro-average).
