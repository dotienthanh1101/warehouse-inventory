# Kiểm kê kho bằng hình ảnh

**Xây dựng phần mềm hỗ trợ kiểm kê kho bằng xử lý ảnh từ camera IP di động.**

Ứng dụng hỗ trợ chụp ảnh, nhận diện marker loại hàng, đếm đơn vị hàng, hiệu chỉnh
kết quả và kiểm tra nghi trùng trong cùng phiên. Người giám sát xác nhận số liệu
trước khi chốt phiên và chốt ngày. Không điều khiển xe tự hành/UAV.

## Chức năng hiện có

- Đăng ký, đăng nhập, phân quyền user/admin; dữ liệu và camera theo kho riêng.
- User: camera IP, chụp/phân tích ảnh, danh mục marker/sản phẩm, hiệu chỉnh từng kiện.
- Đối chiếu ảnh gốc và ảnh che marker; tự chọn mới khi đủ điều kiện, trường hợp nghi
  trùng/chưa xác định cần người dùng quyết định. Marker xác định **loại hàng**, không
  phải định danh duy nhất của từng kiện. Một kiện có thể có nhiều marker.
- Tồn đầu ngày, các phiên đếm lại toàn bộ kho, đối chiếu với mốc đã chốt gần nhất,
  xem trước/chốt cuối ngày, xuất báo cáo CSV/Excel.
- Admin: quản lý tài khoản, khóa/thu hồi đăng nhập, cấp mật khẩu tạm, hỗ trợ người dùng,
  xem thống kê/dữ liệu từng kho và theo dõi dung lượng.
- Công cụ sao lưu thủ công, mã hóa, kiểm tra và phục hồi; kiểm thử tải API/hàng đợi GPU.

Tăng/giảm giữa các lần kiểm kê không chứng minh tổng nhập/xuất giao dịch thực tế.
Sau chốt ngày, các thao tác ghi thuộc ngày đó bị khóa. Chốt phiên yêu cầu xác nhận
đã kiểm kê đủ phạm vi; số ghi tạm trong phiên đang mở chưa phải tồn cuối.

## Thành phần kỹ thuật

| Thành phần | Vai trò |
| --- | --- |
| React / Vite | Giao diện user/admin, camera và trình hiệu chỉnh ảnh |
| FastAPI / Python | API, phân quyền, điều phối tác vụ và nghiệp vụ kiểm kê |
| PostgreSQL / psycopg | Tài khoản, phiên đăng nhập và quản lý hệ thống |
| SQLite theo kho | Dữ liệu kiểm kê riêng từng kho |
| OpenCV / ArUco | Đọc ID marker và xử lý ảnh |
| Grounding DINO | Đề xuất vùng đơn vị hàng để đếm và hiệu chỉnh |
| DINOv2 / LoFTR / PyTorch | Đặc trưng ngoại quan và đối chiếu hình học hỗ trợ nghi trùng |

So sánh hình ảnh hỗ trợ giám sát, không bảo đảm phân biệt được hai kiện có bao bì
hoàn toàn giống nhau. Điểm cosine không phải xác suất cùng một kiện.

## Chạy local trên Windows

Bản nguồn sử dụng Python 3.13 trên máy phát triển hiện tại. Frontend khai báo Node
22 từ 22.12 hoặc Node 24 trong package.json. Cần cài PostgreSQL và môi trường
PyTorch/CUDA tương thích GPU nếu chạy phân tích AI trên CUDA.

1. Clone repo, mở terminal tại thư mục dự án:

```powershell
git clone https://github.com/dotienthanh1101/warehouse-inventory.git
cd warehouse-inventory
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt -r .\backend\requirements-auth.txt -r .\backend\requirements-report.txt -r .\backend\requirements-backup.txt -r .\backend\requirements-video.txt
```

Các nhóm requirements hiện có chưa bao phủ toàn bộ stack AI và bài thử. Xem
[phụ thuộc và giới hạn tái lập](docs/DEPENDENCIES.md) trước khi chạy từ máy mới;
repo không chứa .venv, trọng số mô hình hoặc bản khóa môi trường GPU đầy đủ.

2. Tạo cấu hình và tài khoản quản trị bằng công cụ nhập tương tác:

```powershell
.\.venv\Scripts\python.exe .\tools\setup_accounts.py
```

Công cụ hỏi thông tin PostgreSQL và mật khẩu trên máy. Không dùng --import-legacy
trên bản clone sạch vì dữ liệu kho lịch sử không nằm trong GitHub. Nếu database/role
đã tồn tại, đọc thông báo của công cụ; không xóa database đang dùng để cài lại.

3. Cài frontend:

```powershell
cd frontend
npm.cmd ci
cd ..
```

4. Chạy start_project.bat hoặc mở hai terminal. Backend trong CMD, từ thư mục dự án:

```bat
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Frontend trong PowerShell:

```powershell
cd frontend
npm.cmd run dev
```

Mở http://127.0.0.1:5173. Tài liệu API: http://127.0.0.1:8000/docs.
Giữ PostgreSQL và hai terminal chạy; Ctrl+C để dừng từng dịch vụ.

## Cấu trúc

```text
backend/app/          API, nghiệp vụ, camera, xử lý ảnh, phạm vi dữ liệu kho
frontend/src/         Giao diện React và gọi API
tools/                Thiết lập tài khoản, sao lưu, dung lượng, bảo trì
tests/                Kiểm thử nguồn có trong bản xuất
load_test_100/        Bài thử đăng nhập và đọc API
gpu_queue_test/      Bài thử hàng đợi chụp/phân tích
duplicate_gpu_test/  Bài thử hàng đợi so sánh
camera_bridge/       Cầu nối camera từ xa (đang tạm dừng nghiệm thu)
internet_access/     Truy cập HTTPS thử nghiệm (đang tạm dừng nghiệm thu)
docs/                Hồ sơ kỹ thuật và hướng dẫn
start_project.bat    Khởi động local trên Windows
```

## Dữ liệu riêng và sao lưu

GitHub lưu mã nguồn. Dữ liệu PostgreSQL/SQLite, kho người dùng, ảnh, bản nháp,
config/auth.local.json, mật khẩu/token và các bản sao lưu không được đưa vào repo.
Có .env.example làm ví dụ tên cấu hình; ứng dụng đọc biến môi trường hoặc file
cấu hình theo mã auth_settings.py, không tự động nạp .env chỉ vì file này tồn tại.

Trước khi đổi máy hoặc cập nhật dữ liệu, dùng công cụ sao lưu trên máy đang chạy.
Repo này không thay thế bản sao lưu mã hóa đầy đủ của dự án và dữ liệu vận hành.

## Kiểm tra bản nguồn

```powershell
cd frontend
npm.cmd run build
```

Khi chuẩn bị repo ngày 28/10/2026 đã kiểm tra cú pháp Python, import nội bộ tĩnh,
quét mẫu bí mật phổ biến và build frontend. Không khẳng định đã chạy toàn bộ
nghiệp vụ Windows, PostgreSQL, camera, GPU hoặc tải Internet trên bản clone sạch.
Các bài tải/GPU cần môi trường và dữ liệu thử riêng; đọc README của từng bài trước
khi chạy. Nguồn thử và ảnh benchmark lịch sử không nằm trong bản xuất này.

[Xem phạm vi đóng gói và nguồn gốc](docs/SOURCE_EXPORT.md). README giai đoạn đầu
được giữ tại [docs/history/README-step-1.md](docs/history/README-step-1.md).

## Bản hoàn thiện local 29/10/2026

Bổ sung nút **Xem nguồn** ở từng dòng báo cáo: chọn phiên, xem biên nhận/ảnh/revision,
người xác nhận mới, lịch sử hiệu chỉnh và liên kết kiện trùng. Dữ liệu lịch sử thiếu
người duyệt hoặc ảnh được ghi rõ. Sửa URL camera có cổng 0 bị hiểu là mặc định.

- [Áp dụng vào bản local đang dùng](docs/UPDATE_LOCAL.md)
- [Sáu sơ đồ thiết kế](docs/design/LOCAL_DESIGN.md)
- [Kịch bản demo và kiểm tra](docs/DEMO_LOCAL.md)
- [Phần đã xử lý và giới hạn còn lại](docs/LOCAL_STATUS_20261029.md)

Không tiếp tục sửa HTTPS hoặc đổi mô hình/ngưỡng so sánh trong bản này.
