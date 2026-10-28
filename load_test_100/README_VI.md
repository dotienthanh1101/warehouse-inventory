# NFR-05 · Kiểm tra tải tài khoản và API đọc dữ liệu

Đây là công cụ đo, **không phải bản vá backend/React**. Chạy với 100 tài khoản thử, cơ sở dữ liệu PostgreSQL và thư mục kho tạm riêng. Không dùng kho kiểm kê thật làm dữ liệu thử.

## Chạy đúng các bước

1. Giải nén `load-test-100.zip` vào `D:\Kiem_ke_kho_Buoc_1\warehouse_inventory`. Có file `load_test_100\run.py`.
2. Chờ tác vụ ảnh đang chạy hoàn tất. Nhấn **Ctrl+C** dừng backend chính trong CMD. Giữ PostgreSQL chạy. Không cần tắt React.
3. Trong PowerShell:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.\.venv\Scripts\python.exe .\load_test_100\run.py
```

Nhập **mật khẩu PostgreSQL của tài khoản postgres** khi được hỏi. Chờ công cụ chạy đủ bốn bước; việc khởi tạo 100 tài khoản và nạp backend có thể mất vài phút. Không cần cài thêm thư viện ngoài môi trường dự án hiện có.

4. Gửi lại phần `KẾT QUẢ KIỂM TẢI` ở terminal. Báo cáo JSON nằm trong `docs\load_100_*.json`. Báo cáo không có mật khẩu hoặc cookie đăng nhập.
5. Khởi động lại backend chính trong **CMD**:

```cmd
cd /d "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Tải lại React nếu cần. Không thêm `--reload` hoặc `--workers`.

## Công cụ thực hiện những gì?

- Đối chiếu hash backend với bản đã xác nhận sau `server-capacity-update.zip`. Nếu khác, dừng trước khi tạo dữ liệu thử.
- Đọc cấu hình kết nối hiện có để xác định máy PostgreSQL và tài khoản sở hữu; không sửa cấu hình đó.
- Tạo DB tên ngẫu nhiên `warehouse_load_test_...`, gắn nhãn sở hữu bài thử. PostgreSQL postgres cần quyền tạo/xóa DB thử trên cùng máy PostgreSQL.
- Tạo 100 tài khoản và 100 kho thử bằng chính dịch vụ đăng ký của dự án. Mật khẩu thử sinh ngẫu nhiên, không đưa vào báo cáo. Giữ nguyên kiểm tra ngân sách 512 MiB/kho của ứng dụng; nếu ổ không đủ ngân sách, khởi tạo bị từ chối.
- Khởi động backend thử ở loopback trên một cổng trống 8001–8090, dùng riêng DB/kho và khóa phiên thử. Backend chính cổng 8000 phải dừng để tránh sai lệch đo tài nguyên.
- Đăng nhập đồng thời 100 tài khoản. Sau đó mỗi tài khoản đọc một API mỗi 2 giây, xoay vòng 6 endpoint, trong 30 giây: hồ sơ đăng nhập, thông tin kho, danh sách ngày, danh mục, trạng thái camera và dung lượng kho.
- Mỗi request dùng một kết nối HTTP mới; không phải bài đo keep-alive của trình duyệt. Trung bình mục tiêu khoảng 50 request đọc/giây, ngoài lượt đăng nhập đầu.
- Kiểm tra tên đăng nhập/mã kho được trả đúng với cookie của tài khoản, ngày mới rỗng và camera thử chưa kết nối. Ghi rõ số tài khoản đăng nhập được; không báo 100 user nếu chỉ một phần đăng nhập thành công.
- Đo lỗi, độ trễ p50/p95 và RAM/CPU tiến trình backend. CPU 100% tương đương một lõi; CPU nhiều lõi có thể vượt 100%.
- Dừng backend thử, dọn DB và thư mục do bài thử tạo. Chỉ DROP DB có đúng tên thử và đúng nhãn sinh trong lượt này. Không DROP/khôi phục/ghi đè DB thật.

## Hiểu đúng kết quả

- Đây là kiểm tra HTTP/API **trên máy local**. Không chạy 100 trình duyệt, không nạp giao diện React trong các phiên thử.
- Không kết nối camera, không chạy tác vụ GPU hoặc xác nhận số lượng hàng. Không đo tài nguyên riêng của PostgreSQL, trình duyệt hay VRAM. Các phần đó cần bài thử tiếp theo.
- Không thay cho nghiệm thu HTTPS/Internet, đường mạng camera hoặc 100 người dùng thực tế trên máy chủ triển khai.
- Báo cáo chỉ đưa số đo, không tự kết luận hệ thống production đạt mục tiêu. Cần đánh giá lỗi, tỷ lệ đủ 100 tài khoản, p95 và tài nguyên trên đúng máy sẽ chạy.
- Chuẩn đo có pool PostgreSQL 20 và hàng đợi tối đa 8 như cấu hình chuẩn của dự án. Cookie thử dùng HTTP loopback; không thay cookie/cấu hình của backend thật.
- Dữ liệu thử chỉ khoảng vài chục MiB, nhưng vẫn tuân thủ kiểm tra ngân sách hạn mức logic khoảng 50 GiB cho 100 kho.
- Nếu bị mất điện/đóng cưỡng bức, DB thử có thể còn lại. Nếu công cụ báo chưa dọn, giữ tên DB thử được in ra và kiểm tra trong pgAdmin; không xóa DB khác.
- Nếu thấy `Chưa hoàn tất`, gửi lại thông báo terminal. Không gửi mật khẩu PostgreSQL, file cấu hình chứa bí mật hoặc bản sao lưu toàn dự án.

## Tham số kỹ thuật

Mặc định `--users 100 --duration 30 --interval 2`. Có thể dùng 1–100 user, thời lượng 5–300 giây và khoảng nghỉ 0.25–10 giây để chẩn đoán sau khi có số đo đầu. Luôn ghi đúng tham số trong báo cáo; không gọi thử ít user là đã kiểm 100 user.

Xóa công cụ nếu không cần bằng cách bỏ thư mục `load_test_100`. Không cần hoàn tác backend/React vì công cụ không thay mã ứng dụng.

## Sửa lỗi khởi tạo vùng thử · 26/10/2026

Vùng thử hiện nằm cùng ổ với thư mục lưu kho, thay vì dùng thư mục Temp mặc định của Windows. Nếu khởi tạo lỗi, công cụ in `CHI TIẾT KHỞI TẠO` với bước lỗi, số tài khoản đã tạo, loại lỗi, HTTP/SQLSTATE và vị trí code; không in mật khẩu hoặc chuỗi kết nối. Chỉ sửa công cụ đo tải, không thay backend/React hay ngưỡng AI. Nguyên nhân lỗi trên máy người dùng vẫn cần xác nhận qua lần chạy lại.

## Sửa chọn PID đo CPU/RAM

Đọc PID backend thực từ thông báo khởi động Uvicorn thay vì mặc định dùng PID tiến trình khởi động. Ghi cả hai PID vào báo cáo và terminal. Nếu thiếu hoặc có nhiều PID backend khác nhau thì dừng đo, không tự đoán. Luồng đăng nhập/đọc HTTP, vùng thử cùng ổ dữ liệu và dọn dữ liệu thử được giữ nguyên. Các số đo RAM 4,11 MiB/CPU 0% của lần chạy trước cần đo lại trước khi dùng đánh giá tài nguyên.
