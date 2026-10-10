# Overview

## Bối cảnh bài toán

Tri thức y sinh hiện nay được phân bố không đồng đều giữa các ngôn ngữ. Mặc dù nguồn dữ liệu tiếng Việt ngày càng được mở rộng, nguồn này vẫn còn hạn chế về chiều sâu và chuyên môn. Trong khi đó, tiếng Anh và tiếng Trung sở hữu kho tài nguyên y sinh đồ sộ. Sự chênh lệch này tạo ra một “khoảng cách thông tin y tế” đáng kể, khiến người dùng sử dụng tiếng Việt gặp nhiều trở ngại trong việc tiếp cận các bằng chứng y học được công bố bằng ngôn ngữ khác.

Xuất phát từ thực trạng đó, cuộc thi hướng tới xây dựng các hệ thống AI có khả năng truy hồi thông tin y sinh đa ngôn ngữ từ các nguồn tiếng Việt, tiếng Anh và tiếng Trung. Với mỗi truy vấn bằng tiếng Việt, hệ thống cần xác định các tài liệu và nội dung liên quan, không phụ thuộc vào ngôn ngữ của nguồn. Ban Tổ chức cung cấp các nguồn dữ liệu để các đội chủ động thu thập, xử lý và xây dựng cơ sở tri thức phục vụ truy hồi. Kết quả được đánh giá ở hai mức độ: tài liệu (document) và đoạn nội dung (chunk), qua đó phản ánh cả khả năng xác định đúng tài liệu liên quan và khả năng định vị chính xác phần nội dung chứa thông tin cần tìm.

Nhiệm vụ có thể được hình thức hoá như sau: Cho tập truy vấn Q = {q₁, q₂, …, qₙ} bằng tiếng Việt và tập các nguồn dữ liệu y sinh đa ngôn ngữ do Ban Tổ chức cung cấp hoặc chỉ định. Các đội tự thiết kế quy trình xây dựng cơ sở tri thức, bao gồm thu thập dữ liệu, tiền xử lý, phân đoạn, lập chỉ mục và truy xuất. Với mỗi truy vấn qᵢ, hệ thống cần tìm và xếp hạng các tài liệu hoặc nội dung liên quan từ các nguồn tiếng Việt, tiếng Anh và tiếng Trung.

## Mục tiêu cuộc thi

Các đội thi cần xây dựng hệ thống AI có khả năng:

#### 1. Truy hồi chính xác ở cấp tài liệu

- Xác định đúng các tài liệu liên quan đến truy vấn từ các nguồn dữ liệu y sinh đa ngôn ngữ.
- Thực hiện truy hồi xuyên ngôn ngữ giữa tiếng Việt, tiếng Anh và tiếng Trung thay vì chỉ dựa trên so khớp từ khoá.

#### 2. Truy hồi chính xác ở cấp đoạn nội dung

- Xác định được các đoạn nội dung thực sự chứa thông tin y khoa liên quan đến truy vấn.
- Cách phân đoạn tài liệu và tổ chức các đơn vị truy hồi do từng đội tự thiết kế.

#### 3. Hiểu truy vấn y khoa bằng tiếng Việt

- Hiểu ngôn ngữ tự nhiên tiếng Việt về các thuật ngữ, khái niệm y khoa và lâm sàng.
- Xử lý được truy vấn chứa nhiều thông tin khác nhau trong cùng một câu hỏi.

## Các mốc thời gian quan trọng

- 01 tháng 10, 2026: Phát hành tập kiểm thử công khai (public test)
- 31 tháng 10, 2026: Hạn chót nộp bài cho tập kiểm thử công khai
- 01 tháng 11, 2026: Giai đoạn kiểm thử riêng (private test)
- 04 tháng 11, 2026: Hạn chót nộp hệ thống
- 11 tháng 11, 2026: Công bố kết quả chung cuộc

**Lưu ý: Tất cả các hạn chót đều là 23:59 theo giờ Việt Nam (UTC+07:00).**

## Quy định về dữ liệu bên ngoài và mô hình ngôn ngữ huấn luyện trước (PLMs)

Để đảm bảo sự công bằng trong cuộc thi, người tham gia phải cung cấp nguồn dữ liệu. Người tham gia được phép sử dụng dữ liệu từ các nguồn bên ngoài, tuy nhiên phải trích dẫn rõ ràng và cung cấp đầy đủ thông tin về nguồn gốc dữ liệu để Ban tổ chức có thể kiểm tra, xác minh khi cần thiết.

Người tham gia có thể sử dụng các mô hình ngôn ngữ huấn luyện trước và các LLM có dữ liệu huấn luyện và/hoặc mô hình được công khai (ví dụ: Hugging Face hoặc các trang tương tự), nhưng không được sử dụng các LLM có mô hình đóng (ví dụ: GPT-4o, Gemini, ...). Ngoài ra, người tham gia chỉ được sử dụng các mô hình được phát hành trước ngày 1 tháng 8 năm 2026 (giờ Việt Nam) có kích thước nhỏ hơn hoặc bằng 15B.
