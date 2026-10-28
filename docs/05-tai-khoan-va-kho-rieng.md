# Bước 1 — Đăng ký, đăng nhập và kho riêng

## Phạm vi đã thực hiện

- Đăng ký bằng tên đăng nhập, tên hiển thị, mật khẩu và tên kho.
- Tên đăng nhập không phân biệt hoa/thường; 3–32 ký tự ASCII gồm chữ, số, dấu chấm/gạch ngang/gạch dưới. Mật khẩu 12–128 ký tự.
- Mỗi tài khoản tự đăng ký luôn là user, có một kho riêng được tạo trong cùng giao dịch với tài khoản.
- Phiên đăng nhập bằng cookie HttpOnly, SameSite=Lax, hết hạn tuyệt đối sau 12 giờ; logout thu hồi phiên phía server. Mật khẩu lưu Argon2id.
- API dữ liệu, ảnh, bản nháp và trang tài liệu API đều yêu cầu đăng nhập. GET /api/health được giữ công khai.
- CSRF và kiểm tra Origin trên thao tác ghi. Khóa lựa chọn kho tại backend theo phiên đã xác thực.
- Admin được tạo bằng CLI, không có API công khai cấp quyền admin. Admin xem danh sách tài khoản, tổng quan kho và cấp/thu hồi IP camera riêng cho từng kho; các thao tác này có nhật ký.
- Cache hiệu chỉnh/ngày trong trình duyệt tách theo kho; đăng xuất/hết phiên dọn cache hiệu chỉnh để tránh lưu dữ liệu trên máy dùng chung. Hiệu chỉnh đã lưu backend vẫn giữ.
- Giữ các thuật toán nhận diện, ngưỡng 0.40/0.75 và luồng ghi/chốt đã có.

## Quy mô 100 user: ý nghĩa và giới hạn

Đây là nền tài khoản cho mục tiêu khoảng 100 người truy cập đồng thời, chưa phải cam kết chịu tải triển khai thực tế. PostgreSQL dùng pool tối đa 20 kết nối trên mỗi tiến trình. Hash mật khẩu được giới hạn 4 tác vụ cùng lúc. Các yêu cầu vượt năng lực chờ có thể nhận 429/503 để thử lại.

Cấu trúc chuyển tiếp: PostgreSQL giữ tài khoản/phiên đăng nhập; mỗi kho có SQLite WAL riêng để giữ tương thích với các dịch vụ SQL hiện tại. Không chuyển toàn bộ dữ liệu kiểm kê sang PostgreSQL trong bản này. Một lần chốt ngày của kho A không khóa kho B.

Bản local vẫn chạy MỘT tiến trình backend. Camera là trạng thái trong bộ nhớ theo kho; mặc định tối đa 4 camera đang kết nối. Thay max_cameras không tạo thêm tài nguyên phần cứng. Capture và đối chiếu AI dùng chung khóa để tránh chạy chồng lên GPU 4 GB; nếu đang bận, yêu cầu khác nhận thông báo thử lại. Chưa có hàng đợi AI bền vững hoặc triển khai nhiều máy xử lý.

Mục tiêu 100 camera 1080p hoặc 100 tác vụ AI cùng lúc cần thiết kế hạ tầng khác và kiểm thử tải riêng. Không tăng --workers lúc này: mỗi tiến trình sẽ có trạng thái camera riêng và có thể nạp lại mô hình.

## 1. Chuẩn bị PostgreSQL

Tải bản Windows ổn định PostgreSQL 17 hoặc 18 từ https://www.postgresql.org/download/windows/ . Cài PostgreSQL Server và Command Line Tools, giữ cổng 5432 nếu chưa bị chiếm. Ghi nhớ mật khẩu tài khoản postgres do bạn đặt lúc cài. pgAdmin là tùy chọn; không cần cài StackBuilder để dùng bản này.

PostgreSQL cần chạy như một dịch vụ Windows. Thư viện psycopg không thay thế PostgreSQL Server.

## 2. Cài mã nguồn đã đối chiếu

Dừng backend và frontend bằng Ctrl+C trong từng cửa sổ. Lệnh khởi động lại backend trên CMD sau khi thiết lập hoàn tất:

```cmd
cd /d "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Giải nén ZIP vào thư mục gốc dự án. Chạy trong PowerShell:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.\.venv\Scripts\python.exe .\accounts_update\install.py --apply
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-auth.txt
```

Installer so SHA-256 với mã bạn đã gửi. Nếu có file không khớp, nó dừng trước khi sửa. Bản sao nguồn được lưu ngoài dự án, trong thư mục warehouse_inventory_backups. Nó không xóa dữ liệu, ảnh, sửa package.json hoặc thay CSS giao diện hiện tại.

## 3. Tạo admin và sao chép kho cũ

Khi PostgreSQL đã cài xong và backend đang dừng:

```powershell
.\.venv\Scripts\python.exe .\tools\setup_accounts.py --import-legacy
```

Nhập lần lượt:

- Địa chỉ PostgreSQL: Enter để dùng 127.0.0.1.
- Cổng: Enter để dùng 5432.
- Tài khoản PostgreSQL: Enter để dùng postgres.
- Mật khẩu PostgreSQL: mật khẩu đã đặt khi cài PostgreSQL; terminal không hiện ký tự khi gõ.
- IP camera được phép: chỉ nhập IP, ví dụ 10.136.180.143. Nhiều IP cách nhau dấu phẩy. Để trống sẽ chưa cho kết nối IP camera. Các IP nhập lúc thiết lập chỉ cấp cho kho admin. Với user mới, vào Quản trị → Xem tổng quan kho → IP camera cấp cho kho này → Lưu quyền camera. Mỗi IP chỉ cấp cho một kho. Đổi IP ở trang quản trị có hiệu lực ngay và ngắt kết nối cũ, không cần khởi động lại backend. Không cấp IP nội bộ không phải camera hoặc IP của kho khác.
- Tên đăng nhập admin, tên hiển thị và mật khẩu admin do bạn chọn, rồi nhập lại mật khẩu.

Script tạo database warehouse_accounts cùng role ứng dụng warehouse_auth_app riêng, không dùng tài khoản postgres để chạy web. Không chia sẻ file config/auth.local.json vì chứa thông tin kết nối. File này và vùng dữ liệu riêng được thêm vào .gitignore.

--import-legacy sao chép data/warehouse.db, captures, data/reviews và data/duplicate_checks vào kho admin. Dữ liệu cũ được giữ nguyên. Không liên kết các user mới với kho cũ. Nếu kho đích đã có phiên hoặc ngày, script từ chối ghi đè.

Nếu chưa có data/warehouse.db cũ, chạy lệnh không có --import-legacy để tạo kho admin trống.

Nếu cấu hình đã tạo nhưng nhập admin bị lỗi, chạy lại cùng lệnh để tiếp tục; không tự xóa PostgreSQL hoặc dữ liệu.

## 4. Khởi động lại

Backend, cửa sổ CMD:

```cmd
cd /d "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Frontend, terminal PowerShell riêng:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory\frontend"
npm.cmd run dev
```

Hoặc dùng start_project.bat như trước, sau khi hai cổng đã dừng. File khởi động vẫn trỏ backend.app.daily_api:app; không đổi entrypoint.

Mở http://127.0.0.1:5173 rồi Ctrl+F5. Đăng nhập admin để vào trang quản trị; bấm Kho của tôi để về giao diện 5 tab cũ. /api/health hiển thị version 0.6.0, accounts_enabled=true.

## 5. Kiểm tra trên máy bạn

1. Đăng nhập admin: thấy tài khoản của bạn. Mở Kho của tôi và kiểm tra dữ liệu cũ.
2. Đăng xuất, bấm Đăng ký, tạo user A. Kho mới có danh mục mặc định 01–30, không có phiên/ảnh của admin.
3. Tạo user B trong trình duyệt khác/InPrivate. A và B có thể đổi tên cùng marker độc lập.
4. Sao chép URL ảnh/bản nháp của A để mở khi đang đăng nhập B: không đọc được. Người chưa đăng nhập nhận 401 với API.
5. A không mở được /api/admin/users. Admin xem được danh sách và tổng quan kho A/B.
6. Đăng xuất A rồi tải lại trang: vẫn cần đăng nhập. Khi hết hạn phiên, hiệu chỉnh chưa lưu ở browser sẽ không được giữ; hãy lưu backend trước khi đăng xuất.
7. Thực hiện một phiên kiểm kê thử trong kho A; số liệu B phải giữ nguyên.

Tên tài khoản website và thông tin đăng nhập camera là hai thông tin độc lập. Camera IP vẫn phải nằm trong mạng backend có thể truy cập và IP đó phải được admin cấp riêng cho kho trong giao diện Quản trị. Việc nhiều tài khoản có kho riêng không tự giải quyết camera ở mạng của người dùng từ xa.

## 6. Trước khi đưa ra Internet

Bản này mặc định chỉ chạy local. Khi triển khai HTTPS, cấu hình origins chính xác và secure_cookie=true trong config/auth.local.json (hoặc biến môi trường WAREHOUSE_AUTH_ORIGINS, WAREHOUSE_AUTH_SECURE_COOKIE=true). Tất cả origin ngoài localhost phải dùng HTTPS, cookie Secure; backend từ chối cấu hình không đạt điều kiện này.

Chưa có quên mật khẩu/email xác minh, MFA, quota lưu trữ, phân quyền nhân viên trong cùng kho, trang khóa/mở khóa/reset mật khẩu hoặc màn hình truy xuất chi tiết mọi ảnh của admin. Bước này admin xem danh sách/tổng quan và cấp quyền camera; dữ liệu kiểm kê của người dùng vẫn chỉ đọc đối với admin. Có thể đặt registration_open=false để tạm đóng đăng ký.

Cần triển khai hàng đợi AI, kết nối camera từ xa, sao lưu, giám sát, kiểm thử tải trên máy chủ thật và đánh giá bảo mật trước khi phục vụ 100 khách hàng thực tế. Không chỉ mở tunnel rồi coi như đã hoàn tất.

## 7. Vị trí dữ liệu

- Tài khoản, kho, phiên đăng nhập, nhật ký, bộ đếm giới hạn: database PostgreSQL warehouse_accounts.
- Dữ liệu kiểm kê của một kho: data/warehouses/<warehouse_id>/data/warehouse.db.
- Bản nháp: data/warehouses/<warehouse_id>/data/reviews/.
- Ảnh: data/warehouses/<warehouse_id>/captures/.
- Kết quả đối chiếu: data/warehouses/<warehouse_id>/data/duplicate_checks/.

Phải sao lưu cả PostgreSQL và data/warehouses. Sao lưu config riêng một cách an toàn. Không dùng các script kiểm kê cũ chạy trực tiếp mà chưa có warehouse_scope để sửa kho user: CLI cũ vẫn trỏ kho legacy ở gốc.

## 8. Quay lại mã cũ

Dừng cả hai cửa sổ, sau đó PowerShell:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.\.venv\Scripts\python.exe .\accounts_update\install.py --undo
```

Installer chỉ hoàn tác các file nó đã đổi nếu chúng chưa bị chỉnh tiếp. PostgreSQL, config và dữ liệu kho mới được giữ; chúng không bị nhập ngược vào SQLite cũ. Chạy lại bản cũ sẽ thấy kho legacy tại thời điểm sao chép, không phải các thay đổi phát sinh sau đó trong kho admin mới.

Sau khi hoàn tác, khởi động backend bằng lệnh CMD ở mục 4.
