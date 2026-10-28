NFR-14 · BƯỚC 1: ĐO DUNG LƯỢNG

Giải nén ZIP vào D:\Kiem_ke_kho_Buoc_1\warehouse_inventory
File cần có: tools\inspect_storage.py
Không có file backend hoặc frontend nào bị thay thế.
Nếu đã có tools\inspect_storage.py từ nguồn khác, giữ lại file cũ trước khi chép.

Chạy trong PowerShell:
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.\.venv\Scripts\python.exe .\tools\inspect_storage.py

Không cần dừng backend; không cần cài thư viện.
Đọc kích thước file từng kho, ảnh, bản nháp và SQLite; đo riêng dữ liệu cũ và sao lưu.
Ghi một báo cáo mới vào docs\storage_audit_*.json. Không ghi đè báo cáo cũ.
Không kết nối PostgreSQL, không mở nội dung ảnh/SQLite/config hay đọc mật khẩu.
Không xóa ảnh, không sửa dữ liệu, không áp hạn mức và không tạo lịch.
Chỉ gửi lại phần kết quả terminal để chọn bước tiếp theo; chưa cần gửi dữ liệu kho.

GIỚI HẠN
- Thư mục kho lấy theo WAREHOUSE_STORAGE_ROOT nếu đã đặt, nếu không dùng data\warehouses.
- Thư mục sao lưu mặc định là warehouse_inventory_backups bên cạnh dự án.
- Nếu sao lưu ở nơi khác: thêm --backups "D:\duong_dan_sao_luu".
- Kho hiển thị bằng warehouse_id; không truy vấn tài khoản hoặc suy đoán chủ kho.
- Dung lượng là kích thước logic file, có thể khác dung lượng thực chiếm trên đĩa.
- Số đo khi ứng dụng đang chạy có thể biến động, không phải snapshot giao dịch.
- Không đo dữ liệu máy chủ PostgreSQL, Python/Node hệ thống hoặc model cache ngoài dự án.
- Không xác minh ảnh nào còn được tham chiếu; báo cáo không phải danh sách cho phép xóa.
- Khi gặp liên kết, junction hoặc không đọc được thư mục, báo cáo đánh dấu chưa đầy đủ.
- Chưa hoàn thành toàn bộ NFR-14: bước này chỉ thu số liệu để thiết kế hạn mức.

HOÀN TÁC
Công cụ độc lập. Muốn bỏ công cụ, chỉ bỏ tools\inspect_storage.py.
Các file docs\storage_audit_*.json là báo cáo thống kê do công cụ tạo.
Không cần phục hồi backend hoặc frontend.
