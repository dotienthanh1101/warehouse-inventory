# Sửa lỗi sao chép kho SQLite trên Windows

## Nguyên nhân và phạm vi

Script cũ dùng `with sqlite3.connect(...)`. Context manager này commit/rollback nhưng không đóng kết nối. File database nguồn, đích và tạm vì vậy còn mở tại lúc `os.replace()` hoặc dọn file tạm. Windows có thể trả WinError 32.

Bản sửa bổ sung `contextlib.closing()` tại ba kết nối trong `import_legacy()`. Chỉ cập nhật `tools/setup_accounts.py`. Không đổi cấu hình PostgreSQL, mật khẩu, thuật toán CV, ngưỡng so sánh hoặc giao diện.

Tài liệu Python: https://docs.python.org/3.13/library/sqlite3.html#how-to-use-the-connection-context-manager

## Cài đặt

Giải nén nội dung ZIP vào:

`D:\Kiem_ke_kho_Buoc_1\warehouse_inventory`

Sau giải nén phải có `warehouse_inventory\tools\fix_sqlite_import.py`, không phải thêm một thư mục lồng cùng tên ZIP.

Giữ backend đang dừng trong lúc nhập kho. Chạy PowerShell:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.\.venv\Scripts\python.exe .\tools\fix_sqlite_import.py --apply
```

Helper chỉ nhận bản setup đã đối chiếu bằng SHA-256. Trước khi sửa, nó lưu mã cũ vào thư mục `warehouse_inventory_backups` nằm cạnh dự án. Bản vá chỉ thay nguồn khi các kiểm tra khớp; lỗi ghi biên nhận sẽ hoàn tác mã nguồn.

Khi thấy `Da sua loi dong ket noi SQLite.`, chạy:

```powershell
.\.venv\Scripts\python.exe .\tools\setup_accounts.py --import-legacy
```

Nhập mật khẩu admin WEBSITE vừa đặt, không phải mật khẩu PostgreSQL.

Không xóa thủ công `warehouse.db` hoặc dữ liệu kho. Nếu báo kho đích đã có phiên/ngày, dừng và kiểm tra dữ liệu hiện có thay vì ép ghi đè. Nếu vẫn báo file bị sử dụng, cần kiểm tra backend hoặc phần mềm khác đang mở database.

Kết quả mong đợi:

```text
Admin đã tồn tại; không tạo thêm.
Đã sao chép kho cũ sang kho admin. Dữ liệu nguồn được giữ nguyên.
Sẵn sàng. Khởi động backend và frontend rồi mở http://127.0.0.1:5173
```

## Khởi động sau khi nhập kho thành công

Backend trên CMD:

```cmd
cd /d "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Frontend trên PowerShell riêng:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory\frontend"
npm.cmd run dev
```

## Hoàn tác mã sửa

```powershell
.\.venv\Scripts\python.exe .\tools\fix_sqlite_import.py --undo
```

Chỉ hoàn tác `setup_accounts.py` nếu file chưa bị chỉnh tiếp. Dữ liệu đã nhập không bị nhập ngược hay xóa.

Nếu sau này muốn dùng `accounts_update/install.py --undo`, hoàn tác bản sửa này trước để hash nguồn khớp biên nhận cài accounts ban đầu. Không chạy lại installer accounts với `--apply` trên bản đã cài.

## Kiểm chứng

Đã dùng SQLite thật trên Linux và mô phỏng quy tắc Windows không cho thay/xóa file khi còn kết nối mở. Tái hiện bản cũ thất bại, bản sửa qua luồng nhập. Các kiểm tra bao gồm đóng kết nối, giữ nguồn, từ chối kho đích đã có dữ liệu, lỗi backup/copy và backup/undo bản sửa. Chưa chạy native Windows trong môi trường thử này; cần xác nhận bằng lần nhập trên máy người dùng.
