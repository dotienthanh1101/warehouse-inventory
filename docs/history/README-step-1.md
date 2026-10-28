# Kiểm kê kho — Bước 1: khởi tạo dự án

Đề tài: **Xây dựng phần mềm hỗ trợ kiểm kê kho bằng xử lý ảnh từ camera IP di động.**

Mục tiêu của bước này: mở được giao diện React và nhận phản hồi thực từ backend FastAPI. Đây là bộ khung phát triển, chưa có thu nhận ảnh, đọc QR, cơ sở dữ liệu hoặc đối soát.

## 1. Công cụ trên máy Windows

- VS Code để mở và sửa dự án.
- Python 3.11–3.13; môi trường đã kiểm tra bộ khung sử dụng Python 3.12.
- Node.js 24 LTS; Node 22.12 trở lên trong nhánh 22 cũng dùng được.
- Terminal PowerShell trong VS Code.

Link cài đặt chính thức: [Python](https://www.python.org/downloads/windows/), [Node.js](https://nodejs.org/en/download), [VS Code](https://code.visualstudio.com/download).

Kiểm tra trước khi cài thư viện:

```powershell
py -3 --version
node --version
npm.cmd --version
```

Nếu `py` không được nhận diện nhưng `python --version` cho đúng phiên bản, thay `py -3` bằng `python` trong lệnh tạo môi trường bên dưới. Nếu vừa cài Python hoặc Node.js, đóng rồi mở lại VS Code để nhận đường dẫn mới.

## 2. Mở dự án

Giải nén ZIP. Trong VS Code, chọn **File → Open Folder** và mở thư mục `warehouse_inventory` chứa file README này. Chọn **Terminal → New Terminal**. Các lệnh bên dưới bắt đầu từ thư mục gốc dự án.

## 3. Chạy backend — terminal thứ nhất

Chạy lần lượt và dừng để xem lỗi nếu một lệnh thất bại:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Giữ terminal này mở. Truy cập http://127.0.0.1:8000/api/health — phản hồi phải có `"status":"ok"` và `"service":"warehouse-inventory"`.

Trang thử API: http://127.0.0.1:8000/docs.

## 4. Chạy giao diện — terminal thứ hai

Mở một terminal PowerShell mới tại thư mục gốc, rồi chạy:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Mở http://127.0.0.1:5173. Mục **Máy chủ xử lý** phải hiển thị **Đã kết nối**. Camera vẫn hiển thị **Chưa cấu hình**, vì chức năng camera sẽ được xây dựng ở bước sau.

Dùng `npm.cmd` và gọi trực tiếp Python trong `.venv` để không cần thay đổi chính sách chạy script của PowerShell.

## 5. Tiêu chí hoàn thành bước 1

- Backend khởi động và trả JSON tại `/api/health`.
- Giao diện mở được, hiển thị trạng thái kết nối backend thực tế.
- Tắt backend bằng `Ctrl+C`, bấm **Kiểm tra lại kết nối**: trạng thái chuyển thành **Chưa kết nối**.
- Khởi động backend lại, bấm kiểm tra: trạng thái trở lại **Đã kết nối**.

Trạng thái hiển thị là kết quả của lần kiểm tra gần nhất; giao diện chưa tự kiểm tra định kỳ. Để dừng, dùng `Ctrl+C` ở cả hai terminal.

## Các lần chạy tiếp theo

Không cần tạo lại `.venv` hay cài lại thư viện mỗi lần. Chỉ chạy lệnh `uvicorn` ở terminal thứ nhất, và `npm.cmd run dev` trong thư mục `frontend` ở terminal thứ hai. Chỉ cài lại khi đổi môi trường hoặc cập nhật dependencies.

## Cấu trúc hiện có

| Đường dẫn | Vai trò |
|---|---|
| `backend/app/main.py` | FastAPI và API kiểm tra kết nối |
| `backend/requirements.txt` | Thư viện Python cho bước 1 |
| `frontend/src/App.jsx` | Giao diện React và kiểm tra backend |
| `frontend/src/styles.css` | Giao diện CSS |
| `frontend/vite.config.js` | Máy chủ phát triển, chuyển tiếp `/api` tới backend |
| `frontend/package-lock.json` | Các phiên bản thư viện JavaScript đã cài |
| `docs/01-pham-vi-demo.md` | Đầu vào, đầu ra và các quy tắc đã thống nhất |
| `docs/02-kiem-tra-ban-khung.md` | Kết quả kiểm tra thực tế của bộ khung |

Giao diện dùng HTTP/JSON qua đường dẫn `/api`. Vite chuyển tiếp yêu cầu đến FastAPI trên máy đang chạy, vì vậy cấu hình hiện tại không cần CORS. Bộ khung chạy trên CPU; bước này chưa cài OpenCV, YOLO hoặc thư viện GPU.

## Giới hạn hiện tại

Đây là cấu hình phát triển cục bộ. `npm.cmd run build` kiểm tra khả năng đóng gói frontend; cấu hình phục vụ bản build cùng backend sẽ được làm khi hoàn thiện sản phẩm. Chưa có tài khoản, phân quyền hoặc triển khai lên Internet.

Nếu báo cổng 8000 hoặc 5173 đang được sử dụng, kiểm tra và dừng phiên chạy cũ của chính dự án rồi chạy lại. Không tắt tiến trình lạ. Nếu lỗi khác, gửi lại nguyên văn lỗi cùng phiên bản Python và Node.

## Tài liệu công cụ

- [Vite: Getting Started](https://vite.dev/guide/)
- [FastAPI: First Steps](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [Node.js: Releases](https://nodejs.org/en/about/previous-releases)

Bước tiếp theo sau khi chạy thành công: thu nhận và lưu ảnh đầu vào; khi đó cần biết camera cung cấp ảnh HTTP hay luồng RTSP và nguồn thử nghiệm có sẵn.
