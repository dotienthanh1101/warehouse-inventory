# Đối chiếu phần còn lại ngày 29/10/2026

Căn cứ hồ sơ yêu cầu phiên bản 2 (27/10) và nguồn GitHub tại commit 70ba805.

| Mục | Đã xử lý trong bản này | Giới hạn còn lại |
| --- | --- | --- |
| NFR-13, URL camera | Hồi quy URL cấm; sửa cổng 0 bị hiểu là mặc định; giữ LAN hiện hành | Không nghiệm thu redirect/native FFmpeg hoặc video Internet; cần kiểm camera thật trên Windows |
| NFR-12, nguồn báo cáo | Dòng báo cáo mở biên nhận SQLite bất biến; ảnh, revision, lịch sử, quyết định và actor mới | Bản cũ không có actor vẫn báo thiếu; tồn đầu/snapshot 0 không được tự gán ảnh hoặc người chốt |
| NFR-10, bảo trì | Mã/hash, hồi quy cũ/mới, công cụ kiểm môi trường, bản vá có đối chiếu và hoàn tác | Manifest phụ thuộc CUDA/Windows phải lấy từ máy thật; chưa coi requirements là khóa môi trường GPU đầy đủ |
| DOC-01..08 | Use case, activity, state, sequence, ERD, screen flow và kịch bản demo theo code | Tên ứng dụng chính thức chưa đổi; bản UML chữ hỗ trợ giải thích, chưa xuất thành bộ hình đồ án |
| NFR-07, FR-17 | Giữ mô hình/ngưỡng, ghi rõ quy trình kiểm định | Chưa có ảnh/vật độc lập mới để chạy và nghiệm thu |
| NFR-08, NFR-05 | Giữ ca đã đạt; không lặp lại thành tiến độ mới | Chưa đo camera dài hạn hoặc workload hỗn hợp 100 user/camera thật |
| NFR-11, NFR-14 | Giữ backup thủ công, hạn mức và ngoại lệ ảnh mất | Cần backup mới trên máy thật và quyết định thời hạn lưu; không bật tự xóa |
| FR-35, HTTPS | Giữ mã hiện có | Tạm dừng theo yêu cầu, không sửa trong bản này |

Không kết luận toàn bộ dự án đã nghiệm thu. Các chức năng phụ thuộc hình ảnh thực,
phần cứng, dữ liệu và quyết định chủ kho không được tự đánh dấu hoàn thành bằng
kiểm thử giả lập. Chi tiết số ca và kết quả từng lần chạy nằm trong biên bản kèm bản vá.
