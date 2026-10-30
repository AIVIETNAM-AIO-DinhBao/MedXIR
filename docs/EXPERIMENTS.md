# Nhật ký thử nghiệm retrieval — 27/10 đến 29/10/2026

Tài liệu ghi lại **mọi bài nộp và mọi thử nghiệm** từ khi có điểm 0,1847 (Qwen3-Embedding-4B thuần) đến 0,2410. Điểm tốt nhất hiện tại: **0,2410** (`ens4-b1-d200-c70-x1`).

Phần 1 là bảng tra cứu nhanh; các phần sau giải thích cách làm, kết quả từng nhóm thử nghiệm, bài học, sự cố và việc còn dở. Số liệu nộp lấy từ dashboard của BTC. Số liệu "đo local" (độ trùng giữa các danh sách, kích thước file, ...) do script tính, được nêu rõ nguồn.

## 1. Bảng tổng hợp mọi bài nộp có điểm

FINAL = trung bình (Doc F2, Chunk F2). Mọi bài đều `d200` (200 doc mỗi query) và, trừ khi ghi khác, 150 chunk đơn mỗi query. "Trọng số" là trọng số điểm min-max theo từng query (mục 3.2).

### 1.1 Các mốc trước 27/10 (4B, chỉ để tham chiếu; từ PROGRESS.md)

| Bài | Doc P / R | Chunk P / R | Doc F2 | Chunk F2 | FINAL |
|---|---|---|---:|---:|---:|
| Baseline (≈17 doc, 20 chunk) | 0,324 / 0,117 | 0,098 / 0,044 | 0,121 | 0,0422 | 0,0818 |
| d50, 20 chunk | 0,240 / 0,242 | 0,098 / 0,044 | 0,207 | 0,0422 | 0,1245 |
| d100, 20 chunk | 0,186 / 0,339 | 0,098 / 0,044 | 0,248 | 0,0422 | 0,1453 |
| d200, 20 chunk | 0,134 / 0,445 | 0,098 / 0,044 | 0,261 | 0,0422 | 0,1517 |
| d300, 20 chunk | 0,108 / 0,508 | 0,098 / 0,044 | 0,254 | 0,0422 | 0,1480 |
| d200, 100 chunk | 0,134 / 0,445 | 0,074 / 0,134 | 0,261 | 0,0954 | 0,1783 |
| **d200, 150 chunk (4B thuần)** | 0,134 / 0,445 | 0,0665 / 0,1696 | 0,2612 | 0,1081 | **0,1847** |
| RRF dense + BM25 1:1, d100 / d200 | 0,174 / 0,325; 0,125 / 0,430 | 0,089 / 0,042 | 0,235; 0,247 | 0,0401 | 0,1377; 0,1433 |
| Chỉ BM25 tiếng Việt, d200 | 0,086 / 0,299 | 0,055 / 0,025 | 0,172 | 0,0248 | 0,0982 |

Năm bài rerank (`rr4b`, `rr06b`, `rrbge`, `rr4b-f05`, `rr4b-f10`) **không được nộp**: chúng chỉ sắp lại 150 chunk đã có nên tập chunk và tập doc không đổi, điểm không đổi (kiểm tra 1.200/1.200 query). Rerank chỉ có tác dụng khi chọn từ pool lớn hơn top-150.

### 1.2 Bài nộp từ 27/10

| # | Bài | Mô tả | FINAL | Doc F2 | Chunk F2 | Doc P | Doc R | Chunk P | Chunk R |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `sc03u` | 4B 0,7 + BM25 0,3, pool hợp | 0,1855 | 0,2629 | 0,1080 | 0,1342 | 0,4517 | 0,0660 | 0,1691 |
| 2 | `sc03u-m1` | mỗi doc tối đa 1 chunk | 0,1800 | 0,2629 | 0,0972 | 0,1342 | 0,4517 | 0,0591 | 0,1497 |
| 3 | `sc03u-m1-s2` | m1, mỗi chunk cắt đôi (300 đoạn) | 0,1486 | 0,2629 | 0,0343 | 0,1342 | 0,4517 | 0,0177 | 0,0635 |
| 4 | `sc03u-m1-b1` | m1 + doc-beta 1,0 | 0,1811 | 0,2650 | 0,0972 | 0,1353 | 0,4545 | 0,0591 | 0,1497 |
| 5 | `sc03u-sb02-b1` | cộng điểm chunk anh em γ=0,2 + doc-beta 1,0 | 0,1858 | 0,2652 | 0,1063 | 0,1355 | 0,4549 | 0,0656 | 0,1666 |
| 6 | `sc03u-en10-b1` | bù +0,10 cosine cho top-100 chunk tiếng Anh + doc-beta 1,0 | 0,1766 | 0,2519 | 0,1014 | 0,1288 | 0,4333 | 0,0621 | 0,1592 |
| 7 | `sc03u-e06-b1` | 4B 0,4 + 0.6B 0,3 + BM25 0,3 + doc-beta 1,0 | 0,1914 | 0,2706 | 0,1121 | 0,1386 | 0,4648 | 0,0692 | 0,1768 |
| 8 | `pure-octen` | Octen-8B thuần | 0,1849 | 0,2631 | 0,1068 | 0,1350 | 0,4489 | 0,0660 | 0,1691 |
| 9 | `oct-bm25-b1` | Octen 0,7 + BM25 0,3 + doc-beta 1,0 | 0,1868 | 0,2650 | 0,1086 | 0,1354 | 0,4556 | 0,0668 | 0,1721 |
| 10 | `ens4-b1` | **4B 0,25 + Octen 0,35 + 0.6B 0,15 + BM25 0,25 + doc-beta 1,0** | 0,1975 | 0,2788 | 0,1163 | 0,1428 | 0,4789 | 0,0719 | 0,1820 |
| 11 | `ens4-icd02-b1` | ens4-b1 + 0,2 × độ khớp ICD-10 | 0,1966 | 0,2778 | 0,1154 | 0,1423 | 0,4778 | 0,0712 | 0,1813 |
| 12 | `pure-qwen8b` | Qwen3-Embedding-8B thuần | 0,1878 | 0,2660 | 0,1096 | 0,1359 | 0,4573 | 0,0676 | 0,1727 |
| 13 | `e06-b1-d200-c70-x1` | bài 7 + cửa sổ 3 chunk, 70 cửa sổ | 0,2323 | 0,2706 | 0,1940 | 0,1386 | 0,4648 | 0,1954 | 0,2329 |
| 14 | **`ens4-b1-d200-c70-x1`** | **bài 10 + cửa sổ 3 chunk, 70 cửa sổ** | **0,2410** | 0,2788 | **0,2033** | 0,1428 | 0,4789 | **0,2053** | **0,2426** |

Ghi chú:
- Bài 1 được chấm lại sau khi BTC đổi luật chunk (27/10): vẫn 0,1855. Chunk nguyên vẹn không bị ảnh hưởng.
- Pure 4B chưa nộp lại; điểm 0,1847 là của bài `d200-c150` nộp trước 27/10. Script `fuse_scores.py --w-bm25 0 --pool dense --doc-beta 0` tạo ra đúng file đó (kiểm tra từng query).
- Bài 3: Doc F2 và Doc P/R đều giữ nguyên vì danh sách doc chỉ phụ thuộc thứ hạng chunk, không phụ thuộc cách cắt.
- Đã tạo nhưng **chưa nộp**: `ens4-icd02-b1-d200-c70-x1`. Qwen3-8B chưa vào bài ensemble nào.

### 1.3 So sánh từng model đứng riêng (cùng cấu hình: d200-c150, chunk đơn, không BM25, không doc-beta)

| Model | Kích thước | Chiều | FINAL | Doc F2 | Chunk F2 |
|---|---|---:|---:|---:|---:|
| Qwen3-Embedding-4B | 4B | 2560 | 0,1847 | 0,2612 | 0,1081 |
| Octen-Embedding-8B | 7,6B | 4096 | 0,1849 | 0,2631 | 0,1068 |
| **Qwen3-Embedding-8B** | 8B | 4096 | **0,1878** | **0,2660** | 0,1096 |

Đổi model (cùng chunk, cùng cách nộp) chỉ thay đổi điểm ±0,003. Ensemble nhiều model mới cho mức tăng đáng kể.

### 1.4 Đóng góp cộng dồn

| Bước | FINAL | Tăng |
|---|---:|---:|
| 4B thuần | 0,1847 | |
| + BM25, chọn chunk theo điểm kết hợp | 0,1855 | +0,0008 |
| + Qwen3-0.6B | 0,1914 | +0,0059 |
| + Octen-8B (và doc-beta) | 0,1975 | +0,0061 |
| + cửa sổ 3 chunk | 0,2410 | **+0,0435** |

## 2. Luật chấm và phát hiện then chốt

- Doc: Precision, Recall, F2 (macro theo query). Chunk: một chunk nộp được tính đúng nếu LCS (dãy token con chung dài nhất) so với chunk đáp án ≥ 40%.
- **Từ 27/10:** BTC thêm điều kiện thứ hai: LCS so với **chính chunk ta nộp** cũng phải ≥ 40%. Nghĩa là chunk nộp quá dài so với đáp án thì trượt.
- FINAL = trung bình (Doc F2, Chunk F2). `relevant_docs` và `relevant_chunks` được chấm độc lập. Bài nộp là một tập, nên chỉ đổi thứ tự trong cùng tập không đổi điểm.
- Giới hạn file nộp: 100 MB.

**Phát hiện lớn nhất:** chunk đáp án dài hơn chunk của ta (350 từ).
- Cắt đôi chunk (bài 3) làm precision chunk giảm 3,3 lần (0,0591 → 0,0177; 3,7 lần so với sc03u). Suy luận: điều này chỉ xảy ra nếu chunk đáp án dài hơn khoảng 440 từ, vì nửa chunk (175 từ) không đủ phủ 40% đáp án. Đây là suy luận từ điểm số, chưa đo độ dài chunk đáp án.
- Nối mỗi chunk với chunk trước và sau (bài 14) làm precision chunk tăng 2,9 lần (0,0719 → 0,2053) dù chỉ nộp 70 cửa sổ thay vì 150 chunk.
- Giải pháp thực hiện ở bước nộp, không cần chunk lại và embed lại.

## 3. Phương pháp

### 3.1 Nguồn ứng viên (mỗi nguồn: top-500 chunk cho mỗi một trong 1.200 query)

| Nguồn | File (`artifacts/submission/`) | Cách tạo |
|---|---|---|
| Qwen3-Embedding-4B | `candidates-4b-k500.npz` | `retrieve_local_4b.py`, cosine chính xác trên 2,62 triệu chunk |
| BM25 tiếng Việt | `artifacts/bm25/vi-candidates-k500.npz` | Kaggle CPU, chỉ phủ chunk tiếng Việt |
| Qwen3-Embedding-0.6B | `candidates-06b-k500.npz` | `retrieve_local_06b.py` |
| Octen-Embedding-8B | `candidates-octen8b-k500.npz` | `retrieve_local_8b.py --model octen8b` |
| Qwen3-Embedding-8B | `candidates-qwen8b-k500.npz` | `retrieve_local_8b.py --model qwen8b` |
| Top-100 chunk tiếng Anh (4B) | `candidates-4b-en-part-000-k100.npz` | `retrieve_lang_4b.py`, chỉ dùng cho thử nghiệm EN |

Embed: tài liệu không có instruction (Octen thêm một dấu cách làm tiền tố, theo cấu hình chính thức); query có instruction "Given a web search query, retrieve relevant passages that answer the query"; last-token pooling; chuẩn hóa L2; bf16 trên RTX Pro 6000, lưu float16. Chunk: 350 từ, chồng lấn 50, tối thiểu 40 (`chunk_params 350/50/40`), tổng 2.621.553 chunk.

Kích thước pool (số chunk duy nhất trong top-500 của 1.200 query): 4B 373.607; + BM25 579.638; + 0.6B 668.638 (25,5% corpus).

### 3.2 Kết hợp điểm ([scripts/fuse_scores.py](../scripts/fuse_scores.py))

1. Mỗi danh sách được min-max về [0, 1] **trong từng query**; chunk không có trong một danh sách nhận 0 ở danh sách đó.
2. Điểm kết hợp = Σ trọng số × điểm đã chuẩn hóa. Pool là hợp các danh sách (`--pool union`) hoặc chỉ danh sách đầu (`--pool dense`). Trọng số 4B = 1 − tổng các trọng số còn lại.
3. **Doc:** gom chunk theo văn bản (hash), mỗi văn bản được điểm = chunk tốt nhất + β × (chunk tốt thứ 2 + thứ 3) (`--doc-beta`); lấy 200 doc đầu. Mỗi văn bản mở rộng ra mọi `doc_id` cùng hash (bản URL khác nhau).
4. **Chunk:** lấy chunk theo điểm kết hợp.
5. **Cửa sổ** (`--context K`): mỗi chunk được chọn nối với K chunk liền trước và liền sau cùng văn bản, ghép nguyên văn theo vị trí ký tự `start/end` trong parquet (bỏ phần chồng lấn; khoảng cách 1–2 ký tự giữa hai chunk thành xuống dòng). Chunk nằm trong cửa sổ của chunk xếp cao hơn cùng văn bản thì bỏ qua để không tốn byte cho đoạn lặp. Kiểm tra: 8.924 cặp chunk liền kề trong `vi-part-000`, không cặp nào lệch.

Kích thước file theo số cửa sổ mỗi query (ens4-b1, K=1): 50 → 66,1 MB; 60 → 79,2 MB; **70 → 92,4 MB**; mỗi cửa sổ thêm ≈ 1,3 MB, nên 75 cửa sổ sẽ sát 100 MB. Cửa sổ trung vị 680 từ (p10 261, p90 999), trải trung bình 61 doc mỗi query.

## 4. Kết quả từng nhóm thử nghiệm

### 4.1 Chọn chunk kết hợp dense + BM25 (bài 1)

Thay RRF bằng điểm min-max kết hợp theo trọng số. Chunk thay so với dense thuần (trong 150): w=0,1 → 3,7; 0,2 → 8,1; 0,3 (pool dense) → 13,4; 0,3 (pool hợp) → 17,9. Chọn w=0,3 pool hợp. Kết quả: 0,1855, chỉ +0,0008 so với 4B thuần. Chunk F2 giảm nhẹ (0,1081 → 0,1080); doc F2 tăng 0,2612 → 0,2629.

### 4.2 Đa dạng chunk (bài 2) — **không có ích**

Giới hạn mỗi văn bản 1 chunk để 150 chunk phủ 150 doc khác nhau (trước đó chỉ 109–110 doc). Chunk F2 giảm 0,1080 → 0,0972; doc không đổi. Kết luận: nhiều chunk từ cùng một doc mạnh có ích, vì doc đúng thường có nhiều chunk đáp án.

### 4.3 Cắt chunk ngắn (bài 3) — **không có ích, nhưng cho manh mối**

Mỗi chunk cắt đôi (khoảng 175 từ), 300 đoạn mỗi query. Chunk precision 0,0591 → 0,0177; chunk F2 0,0972 → 0,0343. Đây là manh mối đầu tiên rằng chunk đáp án dài, dẫn đến thử nghiệm cửa sổ.

### 4.4 Doc-beta (bài 4)

Trên nền bài 2: Doc F2 0,2629 → 0,2650 (+0,0021), recall 0,4517 → 0,4545. Chỉ ảnh hưởng danh sách doc, không đổi chunk. Giữ lại cho mọi bài sau.

### 4.5 Cộng điểm chunk anh em (bài 5) — **không có ích**

`điểm' = điểm + γ × (tổng 3 điểm chunk tốt nhất của cùng văn bản)`, γ=0,2; thay ≈ 27 chunk, số doc trong 150 chunk giảm từ 109 xuống 92. Kết quả 0,1858: phần tăng so với bài 4 đến từ doc-beta, chunk F2 giảm (0,1080 → 0,1063).

### 4.6 Ưu tiên tiếng Anh (bài 6) — **làm giảm điểm**

Phát hiện: top-500 dense chỉ có 2,8% chunk tiếng Anh (corpus có 9,6%); điểm cosine tốt nhất trung bình của EN là 0,59 so với VI 0,73 và ZH 0,65; 359/1.200 query không có chunk EN nào trong top-500. Thử bù +0,10 cho top-100 chunk EN (tìm riêng); so với dense 4B thuần, danh sách doc đổi khoảng 48/200. Kết quả: Doc recall 0,4549 → 0,4333, FINAL 0,1766 (giảm 0,009). Nhãn thật không ưu tiên doc tiếng Anh, đã bỏ hướng này (không thử ZH).

### 4.7 Ensemble nhiều model (bài 7, 9, 10, 8, 12)

| Bài | Thành phần (trọng số) | FINAL |
|---|---|---:|
| 7 | 4B 0,4; 0.6B 0,3; BM25 0,3 | 0,1914 |
| 9 | Octen 0,7; BM25 0,3 | 0,1868 |
| 10 | 4B 0,25; Octen 0,35; 0.6B 0,15; BM25 0,25 | 0,1975 |

Độ trùng giữa các danh sách (đo local): top-20 của Octen trùng 4B 9,3/20 (top-150: 81,3/150); 0.6B trùng 4B 7,6/20 (66,2/150). Bài 10 trùng với dense 4B 104/150 chunk và 141/200 doc. Qwen3-8B trùng 4B 94/150 chunk và 133/200 doc (cùng họ Qwen3). Mỗi model mới khác họ hoặc khác dung lượng đóng góp khoảng +0,006. Trọng số chưa được tối ưu.

### 4.8 Nhãn ICD-10 (bài 11) — **không có ích**

- 22 chương ICD-10, mỗi chương viết một đoạn mô tả bằng Việt, Anh và Trung (từ khóa, bệnh, triệu chứng; [configs/icd10_chapters.json](../configs/icd10_chapters.json)). Vector chương = trung bình vector 3 ngôn ngữ, chuẩn hóa L2.
- Gán nhãn: cosine chunk × 22 chương, z-score mỗi chương (toàn corpus cho chunk, 1.200 query cho query); nhãn = softmax(z/0,5). Query giữ các chương theo top-p 0,8. Độ khớp = Σ p_query × p_chunk. Script: `build_icd_notebook.py`, `icd_labels.py`, `icd_feature.py`; kết quả trong `artifacts/icd/`.
- Kiểm tra nhãn: 11/12 query mẫu được chương đầu hợp lý. Trong danh sách ens4, độ khớp trung bình giảm từ 0,43 (hạng 0–10) xuống 0,35 (hạng 300–500) (ngẫu nhiên: 0,045): pool đã đúng chủ đề.
- λ=0,2 thay 14,5/150 chunk. Điểm 0,1966 so với 0,1975 của ens4-b1 (thấp hơn 0,0009). Embedding đã mã hóa chủ đề bệnh, nhãn chương không thêm tín hiệu.

### 4.9 Cửa sổ 3 chunk (bài 13, 14) — **mức tăng lớn nhất**

- `ens4-b1-d200-c70-x1`: Chunk P 0,0719 → 0,2053; Chunk R 0,1820 → 0,2426; Chunk F2 0,1163 → 0,2033; FINAL 0,1975 → **0,2410**.
- `e06-b1-d200-c70-x1`: FINAL 0,1914 → 0,2323 (+0,041); Chunk F2 0,1121 → 0,1940. Mức tăng tương đương ở cả hai nền, nên hiệu ứng nhất quán.
- Doc F2 giữ nguyên trong cả hai bài vì danh sách doc không đổi.
- Chưa thử: K=2 hoặc 3 (ít cửa sổ hơn nhưng rộng hơn), cửa sổ bất đối xứng, chọn tâm cửa sổ theo độ phủ điểm.

## 5. Review đáp án bài tốt nhất (ens4-b1, không có nhãn; chỉ là quan sát)

Đọc 10 query ngẫu nhiên và 4 query khó, thống kê trên 1.200 query. Chi tiết nằm trong memory `answer-review-28-10.md`; tóm tắt:

- Query cụ thể (ferritin, GPT, creatinin, gout, huyết áp 100/60, ALA-PDT, thoái hóa cơ tủy) trả về đúng chủ đề ở cả 4 chunk đầu.
- Lỗi khớp từ vựng: Q678 ("nữ 17 tuổi đi ngoài ra máu") lấy chunk "17 tuổi quan hệ được chưa?"; Q692 (lác mắt tái phát) lấy "ung thư tuyến giáp tái phát" (2/10 query mẫu).
- **Lệch nguồn:** Long Châu (nhathuoclongchau + tiemchunglongchau) chỉ 8,3% corpus nhưng chiếm **39,1%** doc top-20 (4,0 lần và 8,4 lần); medlatec 2,4% → 15,1% (6,3 lần); vinmec 2,1% → 8,6% (4,1 lần); suckhoecongdongonline 15,0% → 2,1% (0,1 lần). Chưa biết nhãn thật có cùng thiên lệch không.
- **Trang mục lục:** 112 trang chỉ mục tiếng Trung (chủ yếu `a-hospital.com`, như 默克家庭诊疗手册 và 疾病条目索引) chiếm 0,57% số slot doc, rank trung bình 98,6/200; 187 query có ít nhất một trang như vậy trong top-50.
- Loại query yếu nhất: kể chuyện dài (>40 từ, 24% query; độ trùng 4B–Octen 0,35 so với 0,40 chung) và query mơ hồ về triệu chứng (32% theo phân bố intent của BTC). 80 query có độ trùng 4B–Octen top-20 ≤ 15%.
- Độ chính xác biên của doc theo hạng (suy từ điểm d50/d100/d200/d300 của 4B): hạng 1–50: 24%; 51–100: 13%; 101–200: 8%; 201–300: 5,6%.

## 6. Hạ tầng và thời gian chạy

### 6.1 Các lần chạy Kaggle (giờ UTC; thêm 7 giờ cho giờ Việt Nam)

| Run | Notebook | Tài khoản | Kết quả |
|---|---|---|---|
| exp_0008 | encode-queries-06b | boinhbo | Chạy xong, báo ERROR do kiểm tra phần cứng (xem 7.3); vector query hợp lệ (1200×1024) |
| exp_0009 | package-qwen8b | boinhbo | Lỗi: assertion kiến trúc quá chặt (xem 7.1) |
| exp_0010 | package-octen8b | boinhbo | Thành công (15,1 GB) |
| exp_0011 | package-qwen8b | boinhbo | Thành công (sau khi sửa) |
| exp_0012, 0014, 0016 | embed-corpus-octen8b s0, s1, s2 | boinhbo | Hoàn tất (metadata còn ghi RUNNING do tiến trình local bị dừng; trên Kaggle là COMPLETE) |
| exp_0013, 0015, 0017 | embed-corpus-qwen8b s0, s1, s2 | dinhbaobao | Như trên |
| exp_0018 | encode-queries-octen8b | boinhbo | 346 giây, 1200×4096, báo ERROR giả (7.3) |
| exp_0019 | embed-icd-octen8b | boinhbo | 66 đoạn, 7.329 token, ERROR giả (7.3) |
| exp_0020 | encode-queries-qwen8b | dinhbaobao | Hoàn tất, ERROR giả (7.3) |

### 6.2 Tốc độ

| Việc | Số đo |
|---|---|
| Embed 4B (RTX Pro 6000, batch 8) | ≈ 31,5k token/s; 987 triệu token; ≈ 3 giờ mỗi shard |
| Embed 8B (batch 16) | ≈ 19k token/s (đo ở shard 0: Octen 18,9–19,3k, Qwen 18,9–19,2k); 332 triệu token và ≈ 4,9 giờ (17.500 giây) mỗi shard; 0 NaN ở cả 6 shard |
| Encode 1.200 query | 0.6B: 44 giây; Octen: 346 giây (gồm nạp model) |
| Tìm kiếm local top-500 trên 2,6 triệu chunk, 4096 chiều | 2,5 phút, đỉnh RAM 5,4 GB (`retrieve_local_8b.py`, đọc mmap theo khối 50.000 hàng) |
| Dựng một bài nộp (`fuse_scores.py`) | khoảng 2–2,5 phút, đỉnh RAM 2,8–4,1 GB |

Chia sẻ tài khoản: boinhbo và dinhbaobao chạy song song (Kaggle cho một phiên GPU mỗi tài khoản). Quota GPU khi bắt đầu: boinhbo ≈ 22 giờ, dinhbaobao 30 giờ.

### 6.3 Notebook Kaggle mới và cách tạo lại

Danh sách đầy đủ và quan hệ phụ thuộc (kernel_sources) nằm trong memory/handoff; generator và config:

| Generator | Sinh ra |
|---|---|
| `scripts/build_package_8b_notebooks.py` | `kaggle_package_qwen8b`, `kaggle_package_octen8b` |
| `scripts/build_embed_corpus_notebook.py --size 8B --slug ... --repo ... --package ... [--prefix ' ']` | `kaggle_embed_corpus_{qwen8b,octen8b}_s{0,1,2}` |
| `scripts/build_retrieval_notebook.py` | `kaggle_encode_queries_{06b,qwen8b,octen8b}` |
| `scripts/build_icd_notebook.py` | `kaggle_embed_icd_octen8b` |

Config chạy: `configs/kaggle/*.yaml`. Công cụ Kaggle bắt buộc notebook phải được Git theo dõi (`git add`).

## 7. Sự cố và cách xử lý

1. **package-qwen8b lỗi lần đầu (exp_0009):** assertion `architectures == ['Qwen3Model']` quá chặt; Qwen3-Embedding-8B không khai báo kiến trúc đó. Sửa thành `any('Qwen3' in a ...)`.
2. **Hai lần chạy đồng thời tranh mã run:** `tools.kaggle` cấp `exp_NNNN` theo thứ tự; chạy hai kernel cùng lúc thì một bên báo "run already exists". Chạy lại sau vài giây (các script nền đã có vòng thử lại).
3. **Báo ERROR giả "no observed hardware":** công cụ báo lỗi dù kernel chạy xong; log trong kernel ghi rõ `GPU: NVIDIA RTX PRO 6000 Blackwell Server Edition`. Đã xác minh GPU từ log trước khi dùng kết quả.
4. **Máy local khởi động lại ≥ 3 lần (28/10 lúc 11:59, 19:06, 20:00):** các tiến trình nền bị mất mỗi lần (Kaggle không bị ảnh hưởng). Lần 20:00 ngay sau khi hết RAM do vừa tạo bài nộp vừa tải 7 GB vector. Đã chuyển các script nền sang `setsid nohup` và dùng trạng thái trên Kaggle để tiếp tục. Quy tắc: không chạy `fuse_scores.py` khi đang tải vector.
5. **`pkill -f` / `pgrep -f` tự dừng shell** (hai lần): mẫu trùng chính dòng lệnh. Dùng `ps -eo args | grep "[x]..."` hoặc kill theo PID.
6. **Tải vector Qwen s2 bị đứt (`IncompleteRead`):** CLI `kaggle kernels output` đọc nguyên file (2,5 GB) vào RAM, không tải tiếp được, và bỏ luôn các file sau khi lỗi. Thay bằng `runs/chains/fetch_resume.sh` (`curl -C -` trên URL ký mới ở mỗi lần thử), tải đủ 9 part, đúng kích thước theo manifest.
7. **Metadata `runs/experiments`:** các run embed ghi RUNNING vì tiến trình giám sát bị dừng; trạng thái đúng lấy từ Kaggle (`kaggle kernels status`).

## 8. Việc còn dở và gợi ý tiếp theo

- **Chưa nộp:** `ens4-icd02-b1-d200-c70-x1`; Qwen-8B chưa vào ensemble nào.
- **Chưa thử:** quét độ rộng cửa sổ (K=2,3) và đánh đổi số cửa sổ/độ rộng trong giới hạn 100 MB; ensemble 5 model (thêm Qwen-8B) với cửa sổ; chọn tâm cửa sổ theo độ phủ điểm; giảm trọng số nguồn bị lệch (Long Châu) và lọc trang mục lục; biến thể query bằng LLM ≤ 15B (viết lại, diễn đạt khác, HyDE, dịch Anh/Trung); PRF (trộn vector top-5 vào query).
- **Đã loại bỏ:** chunk lại lớn hơn rồi embed lại (bạn đã quyết không làm vì nội dung loãng và mất nhiều giờ); reranker (không nằm trong mục tiêu).
- **Git:** chưa commit gì từ `f1ba09a`. Các file mới (notebook, config, `build_*`, `retrieve_local_8b.py`, `icd_*.py`) đã `git add`; `fuse_scores.py`, `retrieve_local_06b.py`, `retrieve_lang_4b.py` chưa được theo dõi.
- **Vector 8B chỉ có ở local** (`artifacts/kaggle/vectors-{octen8b,qwen8b}-s{0,1,2}`, ≈ 21 GB mỗi model), không có trên Kaggle dưới dạng dataset; trên Kaggle chúng là đầu ra của các notebook embed.
