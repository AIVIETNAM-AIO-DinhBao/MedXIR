# Quy ước nối dữ liệu retrieval

**Chốt ngày 23/10/2026:** baseline đầu dùng dense retrieval với Qwen3-Embedding-4B. Hoãn BM25 tới khi thiết kế và đánh giá tokenizer riêng cho tiếng Anh, tiếng Việt và tiếng Trung.

## Artifact đã rà soát

- Manifest chunk corpus v1: `artifacts/kaggle/chunk-corpus-v1/chunk_manifest.json`.
- Bảng chunk chuẩn: `artifacts/kaggle/chunk-corpus-v1/chunks/<lang>/part-NNN.parquet`.
- Ánh xạ nội dung trùng: `artifacts/kaggle/chunk-corpus-v1/hash_groups.parquet`.
- Vector 4B và bảng ID ghép cặp: `artifacts/kaggle/vectors-qwen3-4b-s0..s2/vectors/`.
- Ba manifest 4B báo đủ chín part, 2.560 chiều, nguồn float16 và 0 hàng không hữu hạn. Tổng cộng 2.621.553 chunk, khớp manifest chunk.
- Đã kiểm tra shape/dtype của cả chín `.npy`; tổng 2.621.553 `chunk_id` duy nhất. Sau khi tải output chunk, đối chiếu từng hàng xác nhận bảng ID khớp chính xác thứ tự với chunk parquet. Norm vector lấy mẫu xấp xỉ 1.
- Cả 1.023.783 hash group có prefix 16 ký tự duy nhất; mọi vector ID đều khớp một prefix, định dạng hợp lệ và thứ tự chunk trong mỗi group liên tục từ 0.

## Khóa nối và thứ tự hàng

Với mỗi part `P = <lang>-part-NNN`:

1. `vectors/P.npy[row]` ghép với `vectors/P-ids.parquet.chunk_id[row]`.
2. Nối `chunk_id` đó với `chunks/<lang>/part-NNN.parquet.chunk_id` để lấy `text_sha256`, `rep_doc_id`, `lang`, `order` và `chunk_text` gốc. Thứ tự ID phải khớp chính xác; dừng build nếu thiếu, trùng hoặc sai thứ tự.
3. Xác nhận `chunk_id == text_sha256[:16] + "-" + str(order)` và cặp `(text_sha256, rep_doc_id)` tồn tại trong `hash_groups.parquet`.
4. Khi truy hồi: số hàng FAISS → `chunk_id` trong row-map → chunk gốc để lấy text và tài liệu đại diện. Dùng `text_sha256` để tra hash group. Chỉ mở rộng document ID khi `fanout_safe=true`; nếu không thì trả representative ID.

Không nối riêng hai mảng vector/ID rồi sắp xếp từng bên độc lập; không suy ra document ID từ hash bị cắt 16 ký tự. Khóa nối chuẩn là `chunk_id` và `text_sha256` đầy đủ.

## Cách lưu index 4B

- Dùng `IndexFlatIP` chính xác trên vector đã chuẩn hóa L2. Nguồn `.npy` là float16; khi build chuyển từng batch sang float32 và chuẩn hóa lại để khử sai lệch norm nhỏ do lưu float16.
- Lưu một index cho mỗi part và một row-map parquet cạnh index. `IndexFlatIP` trả về số hàng tuần tự, vì vậy row-map là bắt buộc.
- Tìm cùng top-k ở từng part, sau đó gộp và sắp xếp theo inner product. Vì từng part tìm chính xác, hợp các top-k cục bộ sẽ chứa top-k chính xác của toàn corpus.
- Payload vector chiếm khoảng 26,84 GB (25,0 GiB) ở float32 với 2.621.553 × 2.560 giá trị; các file nguồn float16 khoảng 13,42 GB (12,5 GiB). Tách theo part giúp build trong giới hạn RAM và không cần một index đơn khối.
- Baseline đầu dùng `IndexFlatIP` float32. Cách này không khôi phục phần chính xác đã mất khi vector được lưu float16; nó giữ phép tích lũy điểm và baseline Flat ở float32. FAISS ghi nhận GPU float16 thường ít ảnh hưởng Recall@N nhưng có thể đổi khoảng cách; nếu đổi sau này cần so độ trùng candidate trên query thật.

> Tài liệu FAISS: [Flat index và ID tuần tự](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes), [đánh đổi GPU float16](https://github.com/facebookresearch/faiss/wiki/Faiss-on-the-GPU), [khác biệt kết quả GPU/CPU](https://github.com/facebookresearch/faiss/wiki/Comparing-GPU-vs-CPU).

## Phạm vi hiện tại

Đã build và kiểm tra index dense 4B; bước tiếp là encode 1.200 query bằng cùng model, truy hồi và xuất baseline. BM25 hoãn lại vì cần lựa chọn tokenization phù hợp cho EN/VI/ZH.
