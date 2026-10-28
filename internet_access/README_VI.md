# HTTPS và cầu nối camera

Dùng bản cập nhật `camera_gateway_update` cùng hướng dẫn chi tiết trong thư mục đó. Giữ PostgreSQL chạy, đợi tác vụ ảnh hoàn tất rồi dừng backend cũ trước khi mở public.

PowerShell tại gốc dự án:

```powershell
.\.venv\Scripts\python.exe .\internet_access\start.py
```

Công cụ build React hiện có, mở Quick Tunnel và in link HTTPS mới. Chỉ chạy một backend, dùng phiên cookie Secure và đúng Origin. Không sửa file cấu hình/mật khẩu. Máy chủ, PostgreSQL và cửa sổ tunnel phải hoạt động trong khi dùng.

User đăng nhập trên link HTTPS, mở Camera, tạo mã kết nối và chạy `camera_bridge/run.py` trên máy cùng mạng với camera. Dùng hướng dẫn `camera_bridge/README_VI.md`, hoặc tải bộ cầu nối qua nút Tải cầu nối trên React. Cầu nối gửi ảnh có khóa riêng của kho, không đưa IP/mật khẩu camera lên máy chủ. Cấm server public truy cập URL LAN trực tiếp vẫn được giữ nguyên.

Link Quick Tunnel tạm thay đổi khi tạo lượt mới. Dừng tunnel bằng Ctrl+C sau khi các tác vụ hoàn tất; tạo link/mã ghép mới khi chạy lại. Đây chưa phải địa chỉ production cố định hoặc nghiệm thu tải 100 camera.

Quay về local, chạy backend trong CMD:

```cmd
cd /d "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Nếu frontend local đã dừng, mở PowerShell riêng trong `frontend` rồi chạy `npm.cmd run dev`. Không chạy backend local đồng thời với bản HTTPS dùng cùng dữ liệu.

Khi lỗi, giữ log của lượt chạy trong `internet_access/runtime/` và gửi thông báo lỗi. Không gửi cấu hình chứa mật khẩu, mã ghép hoặc khóa cầu nối.
