# Thiết kế hệ thống local

Đối chiếu mã tại nhánh hoàn thiện local ngày 29/10/2026 và hồ sơ yêu cầu phiên bản 2.
Các sơ đồ mô tả chức năng hiện có và phần truy vết trong bản này. Không coi GitHub
Public là triển khai ứng dụng HTTPS; phần truyền camera Internet vẫn tạm dừng.

## Ca sử dụng và quyền

```mermaid
flowchart TD
  guest["Khách"] --> auth["Đăng ký, đăng nhập và hỗ trợ khôi phục"]
  user["User / người giám sát"] --> stock["Kho riêng: camera, danh mục và kiểm kê"]
  user --> review["Hiệu chỉnh và quyết định mới/trùng"]
  user --> report["Chốt phiên/ngày, xuất và truy vết báo cáo"]
  admin["Admin hệ thống"] --> accounts["Tài khoản, khóa, thu hồi và mật khẩu tạm"]
  admin --> inspect["Thống kê, tra cứu kho chỉ đọc và hạn mức"]
  admin --> support["Tiếp nhận và xử lý hỗ trợ"]
```

Admin không trực tiếp kiểm kê bằng vai trò admin. Khôi phục không hiển thị mật khẩu
cũ. Mỗi API nghiệp vụ chạy trong phạm vi kho được xác thực, không tin warehouse_id
tùy ý từ request.

## Hoạt động kiểm kê

```mermaid
flowchart TD
  day["Mở ngày và nhận tồn đầu"] --> session["Tạo phiên đếm toàn bộ kho"]
  session --> shot["Chụp một frame còn mới"]
  shot --> proposals["Đọc marker và đề xuất vùng"]
  proposals --> correction["Duyệt vùng lỗi và lưu hiệu chỉnh"]
  correction --> compare["Đối chiếu lịch sử cùng phiên"]
  compare --> decision{"Đủ điều kiện mới?"}
  decision -->|"Có"| choose["Tự chọn mới"]
  decision -->|"Chưa rõ / nghi trùng"| human["Người giám sát chọn từng kiện"]
  choose --> commit["Ghi biên nhận và kiện mới trong giao dịch"]
  human --> commit
  commit --> coverage{"Đủ phạm vi kho?"}
  coverage -->|"Chưa"| shot
  coverage -->|"Đủ"| finalize["Chốt tồn phiên và so mốc trước"]
  finalize --> next{"Còn phiên trong ngày?"}
  next -->|"Có"| session
  next -->|"Không"| close["Xem báo cáo và chốt ngày"]
```

Chốt phiên/ngày vẫn có xác nhận người dùng. Ngưỡng và mô hình không thay trong bản
hoàn thiện này. Chênh lệch là biến động qua kiểm kê, không phải sổ nhập/xuất.

## Trạng thái

```mermaid
stateDiagram-v2
  [*] --> Draft
  Draft --> Corrected: Lưu hiệu chỉnh và tăng revision
  Corrected --> Corrected: Sửa lại, kiểm tra trùng hết hiệu lực
  Corrected --> Checked: Đối chiếu đúng revision/lịch sử
  Checked --> Corrected: Bản nháp hoặc tham chiếu đổi
  Checked --> Recorded: Quyết định đủ, phiên/ngày còn mở
  Recorded --> Recorded: Gửi lại trả biên nhận cũ
  Recorded --> [*]: Khóa sửa
```

Ngày và phiên dùng trạng thái open/closed. Tác vụ ảnh dùng preparing, queued,
running, completed hoặc failed; refresh không được biến tác vụ đang chạy thành
một lần ghi kiểm kê mới.

## Trình tự ghi và truy vết

```mermaid
sequenceDiagram
  participant U as User
  participant UI as React
  participant API as FastAPI
  participant DB as SQLite theo kho
  U->>UI: Lưu hiệu chỉnh
  UI->>API: Review ID và revision hiện tại
  API->>API: Xác thực, CSRF và phạm vi kho
  API-->>UI: Revision mới và người hiệu chỉnh
  U->>UI: Xác nhận quyết định mới/trùng
  UI->>API: Check ID, revision và quyết định
  API->>DB: BEGIN IMMEDIATE, kiểm tra và ghi snapshot
  DB-->>API: Biên nhận, instance và danh tính người xác nhận
  API-->>UI: Số kiện mới hoặc biên nhận đã ghi
  U->>UI: Xem nguồn một dòng báo cáo
  UI->>API: Ngày, marker và phiên đã chốt
  API->>DB: Đọc snapshot và biên nhận bất biến
  DB-->>API: Số tồn, ảnh, revision và quyết định
  API-->>UI: Nguồn số liệu chỉ đọc
```

## Quan hệ dữ liệu

```mermaid
erDiagram
  AUTH_USER ||--o| WAREHOUSE : "sở hữu khi role user"
  AUTH_USER ||--o{ AUTH_SESSION : "đăng nhập"
  WAREHOUSE ||--o{ INVENTORY_DAY : "phạm vi dữ liệu"
  INVENTORY_DAY ||--o{ DAY_SESSION : "chứa"
  DAY_SESSION ||--|| INVENTORY_SESSION : "liên kết"
  INVENTORY_SESSION ||--o{ REVIEW_RECEIPT : "ghi nhận"
  REVIEW_RECEIPT ||--o{ BOX_INSTANCE : "kiện mới"
  PRODUCT_TYPE ||--o{ BOX_INSTANCE : "loại hàng"
  DAY_SESSION ||--o{ SESSION_STOCK : "tồn chốt theo loại"
  PRODUCT_TYPE ||--o{ SESSION_STOCK : "phân loại"
  INVENTORY_DAY ||--o{ DAY_OPENING_ITEM : "tồn đầu"
  INVENTORY_DAY ||--o{ DAY_CLOSING_ITEM : "báo cáo chốt"
```

Sơ đồ tập trung vào luồng kiểm kê theo ngày; phiên legacy có thể chưa liên kết ngày.
Auth user/session/warehouse nằm ở PostgreSQL. Nghiệp vụ nằm trong SQLite từng kho.
Quan hệ warehouse đến ngày là quan hệ logic qua phạm vi thư mục, không phải khóa
ngoại xuyên PostgreSQL–SQLite. Ảnh là file riêng. reviewed_payload_json của biên
nhận giữ hiệu chỉnh, lịch sử, quyết định trùng và confirmation.confirmed_by cho
lần ghi mới. Không hồi điền người duyệt cho biên nhận lịch sử.

## Luồng màn hình

```mermaid
flowchart TD
  login["Đăng nhập"] --> role{"Vai trò"}
  role -->|"User"| overview["Tổng quan kho"]
  role -->|"Admin"| admin["Tổng quan hệ thống"]
  overview --> camera["Camera"]
  camera --> editor["Kiểm tra ảnh"]
  editor --> compare["Đối chiếu ngay trong kiểm tra ảnh"]
  overview --> session["Ngày và phiên kiểm kê"]
  session --> report["Báo cáo ngày"]
  report --> trace["Xem nguồn theo loại và phiên"]
  overview --> catalog["Danh mục hàng"]
  admin --> accounts["Tài khoản"]
  admin --> warehouses["Dữ liệu kho chỉ đọc"]
  admin --> tickets["Hộp hỗ trợ"]
```

Không tạo thêm tab cho truy vết. Người dùng mở từ từng dòng báo cáo và đóng lại
khi xong. Email, mật khẩu và token không được đưa vào metadata biên nhận.
