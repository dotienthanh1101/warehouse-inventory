# Chuẩn bị mã nguồn kiểm kê kho cho GitHub

Bản xuất từ thư mục dự án hiện tại, không thay mã nguồn đang chạy. Đây là mã nguồn,
không phải bản sao lưu dữ liệu vận hành. File README gốc (nếu có) được giữ nguyên.

## Thành phần

- React/Vite: giao diện user và admin, camera, hiệu chỉnh ảnh, phiên, danh mục, báo cáo.
- FastAPI/Python: API và điều phối xử lý ảnh; PostgreSQL cho tài khoản; SQLite theo kho.
- OpenCV/ArUco, Grounding DINO và DINOv2; các thử nghiệm LoFTR theo mã thực tế.
- Công cụ và kiểm thử nguồn hiện có. HTTPS/cầu nối được giữ nếu có nguồn, chưa coi là đã nghiệm thu.

## Máy mới

Cài Python/Node.js/PostgreSQL và môi trường CUDA/PyTorch tương thích. Xem requirements
thực tế trong backend; tránh dùng một lệnh cài PyTorch CPU thay cho môi trường GPU.
Tạo venv, cài các requirements cần thiết, chạy npm.cmd ci trong frontend.
Thiết lập tài khoản mới bằng tools/setup_accounts.py. Không dùng --import-legacy trên
repo sạch vì dữ liệu kho cũ không nằm trong GitHub. Mật khẩu nhập tương tác tại máy.

Chạy bằng start_project.bat hiện tại của dự án sau khi thiết lập môi trường.
Script xuất không thay entrypoint backend đã được kiểm tra trên máy đang dùng.

Frontend (PowerShell):

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

## Không nằm trong repo

Mật khẩu/token, config/auth.local.json, tài khoản và dữ liệu người dùng, database,
ảnh chụp/bản nháp/kết quả vận hành, môi trường .venv/node_modules, trọng số mô hình,
ZIP bản vá/sao lưu cũ. Mô hình được tải theo mã nguồn, không nhét trọng số vào Git.
Ảnh kiểm thử/tài liệu nhị phân không được xuất tự động; cần bộ dữ liệu riêng để chạy
những bài kiểm thử phụ thuộc ảnh. Danh sách file đã chọn có trong GITHUB_EXPORT.json.

Quét bí mật chỉ là bước hỗ trợ, không thay thế kiểm tra nội dung trước commit.
Không tự đổi license hoặc tuyên bố độ chính xác AI/100 camera đồng thời.
