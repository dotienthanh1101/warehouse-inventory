# Áp dụng bản hoàn thiện local 29/10/2026

Bản này sửa cổng camera 0, bổ sung nguồn số liệu/người xác nhận và kiểm thử/tài liệu.
Không thay mô hình/ngưỡng AI, schema dữ liệu kho, mật khẩu hay cấu hình đang dùng.

1. Tải ZIP nhánh complete-local-20261029 từ GitHub. Giải nén **bên cạnh** thư mục
   warehouse_inventory, không chép đè toàn bộ bản clone vào thư mục đang có dữ liệu.
   Thư mục giải nén có tên warehouse-inventory-complete-local-20261029.
2. Dừng backend và frontend bằng Ctrl+C; giữ PostgreSQL chạy.
3. Trong PowerShell:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1"
.\warehouse_inventory\.venv\Scripts\python.exe .\warehouse-inventory-complete-local-20261029\tools\apply_local_update.py --project ".\warehouse_inventory" --apply
```

Trình áp dụng kiểm hash với bản đã gửi ngày 28/10, sao lưu file cũ rồi build frontend.
Nếu mã khác, trình dừng trước khi ghi. Nếu lỗi khi ghi/build, trình thử hoàn tác và
in đường dẫn receipt.json. Không tự cài dependency hoặc hỏi mật khẩu.
Bản sao nguồn cũ và rollback.py nằm tại archive/local_closeout/<moc_cai>/.

4. Khi có dòng DA AP DUNG BAN LOCAL · Build thanh cong, mở CMD chạy backend:

```bat
cd /d "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Mở terminal PowerShell khác chạy frontend:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory\frontend"
npm.cmd run dev
```

Nhấn Ctrl+F5 trên trình duyệt. Mở một báo cáo đã có, bấm Xem nguồn. Biên nhận lịch
sử thiếu người duyệt phải hiển thị đúng thiếu. Chụp/hiệu chỉnh/ghi một mẫu thử mới
trong kho thử để kiểm thông tin người xác nhận; không đổi số liệu kho thật vì demo.

## Kiểm thử tùy chọn trên máy Windows

Các bài dưới dùng kho tạm, không chạy camera/AI thật. Có thể cài HTTP test client
qua backend/requirements-test.txt; không gỡ hoặc thay gói OpenCV/PyTorch đang dùng.

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\tools\check_local_integrity.py
.\.venv\Scripts\python.exe .\tools\check_local_readiness.py --gpu
```

## Hoàn tác nếu cần

Dừng backend/frontend. Trình áp dụng in đường dẫn biên bản thật; dùng lại đường dẫn
đó, không gõ nguyên ký hiệu mốc cài. Có thể dùng rollback.py trong thư mục sao lưu
nếu đã xóa bản nguồn tải về. Lệnh từ thư mục chứa bản nguồn tải về:

```powershell
D:\Kiem_ke_kho_Buoc_1\warehouse_inventory\.venv\Scripts\python.exe .\tools\apply_local_update.py --project "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory" --rollback "DUONG_DAN_RECEIPT_JSON_THAT"
```

Hoàn tác chỉ phục hồi nguồn. Metadata người duyệt đã lưu thêm vẫn ở trong snapshot
và không đổi số lượng, mã nguồn cũ bỏ qua trường thêm. Không phục hồi đè database.
Nếu đang dùng frontend/dist thay cho Vite dev, cần build lại sau hoàn tác.
