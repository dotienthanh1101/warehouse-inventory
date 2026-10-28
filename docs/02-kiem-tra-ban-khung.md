# Kết quả kiểm tra bộ khung bước 1

Thời điểm: 30/09/2026. Môi trường thực hiện: Linux, Python 3.12.14, Node.js 24.19.0, npm 11.9.0.

| Kiểm tra đã thực hiện | Kết quả |
|---|---|
| Tạo môi trường Python riêng và cài requirements | Thành công |
| Cài thư viện frontend và tạo package-lock.json | Thành công |
| `npm run build` | Thành công |
| Khởi động Uvicorn, gọi `/api/health` | HTTP 200, đúng tên service và trạng thái `ok` |
| Mở `/docs` qua HTTP | HTTP 200 |
| Khởi động Vite, lấy trang HTML đầu vào | HTTP 200 |
| Gọi `/api/health` thông qua Vite | Chuyển tiếp đúng tới FastAPI, HTTP 200 |
| Dừng backend rồi gọi API thông qua Vite | Trả lỗi, không báo kết nối thành công giả |

Các tiến trình kiểm tra đã được dừng sau khi hoàn tất.

Chưa kiểm tra trực tiếp giao diện trong trình duyệt Windows, chưa thực thi các lệnh PowerShell trên máy của người dùng. Cần thực hiện các thao tác trong README để xác nhận cài đặt và giao diện trên máy đích.

Đây là kiểm tra bộ khung và kết nối HTTP. Chưa có kết quả đo độ chính xác đọc QR, tích hợp camera hoặc hiệu năng kiểm kê.
