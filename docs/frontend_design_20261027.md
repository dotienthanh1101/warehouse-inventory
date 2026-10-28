# Giao diện kiểm kê kho · 27/10/2026

Áp dụng mẫu đã duyệt: sidebar navy, tab xanh dương, nội dung sáng, tiêu đề đậm và chữ thông thường vừa phải. Camera, kiểm tra ảnh, phiên, danh mục, báo cáo, đăng nhập và quản trị dùng cùng hệ màu.

## Cài đặt

1. Giải nén để có `tools/frontend_design/install.py` bên trong thư mục `warehouse_inventory`. Không giải nén thành một dự án lồng bên trong.
2. Dừng terminal chạy frontend bằng Ctrl+C. Giữ backend và PostgreSQL chạy.
3. Trong PowerShell:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.\.venv\Scripts\python.exe .\tools\frontend_design\install.py --apply
```

Chỉ tiếp tục khi có dòng `DA AP DUNG GIAO DIEN · Build thanh cong.`. Nếu mã nguồn khác bản đã đối chiếu, trình cài dừng trước khi sửa. Nếu build thất bại, trình cài cố gắng hoàn tác sáu file nguồn và in biên bản để kiểm tra.

4. Chạy frontend:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory\frontend"
npm.cmd run dev
```

Mở địa chỉ Vite in ra và nhấn Ctrl+F5. Không cần khởi động lại backend. Nếu cổng Vite đã chạy sẵn, không mở thêm một Vite thứ hai.

## Phạm vi

Sửa bốn file JSX: main, AuthShell, App và ImageTaskPanel. Thêm design.css và TransientNotice.jsx. Các CSS cũ được giữ nguyên. Không đổi package.json, lockfile, backend, cấu hình, thuật toán AI hoặc dữ liệu kho.

Animation: tab mờ vào 160ms, menu hiện nhẹ 160ms, phản hồi nút/chọn kiện, spinner theo tác vụ thật. Không chuyển động vị trí khung khi vẽ. Tự tắt chuyển động khi hệ điều hành bật giảm chuyển động. Thông báo thường tự đóng sau 4 giây; tác vụ hoàn tất chưa mở kết quả và lỗi vẫn được giữ để xử lý.

## Đối chiếu đã thực hiện

- 24 file JavaScript/cấu hình nguồn khớp SHA-256 trong báo cáo kiểm kê file người dùng gửi. CSS cũ không bị ghi đè.
- Build production của bản gốc và bản mới thành công; không thêm dependency.
- 50 kiểm tra trình duyệt trên production build với API giả lập: hiệu chỉnh một kiện lỗi trong ba kiện; lưu, kiểm tra trùng, hai kiện mới và một kiện trùng; khóa sau ghi, tải lại không cộng thêm; các tab nghiệp vụ; nút quản trị; đăng ký/email/khôi phục; mobile; giảm chuyển động và thông báo sau F5.
- Payload API hiệu chỉnh và ghi nhận của bản cũ/bản mới giống nhau trong bài thử. Toàn bộ phần xử lý nghiệp vụ trước JSX return của App giữ nguyên, ngoài hai import giao diện.
- 8 kiểm tra trình cài: cài/build thật trên nguồn dựng lại, chạy lại không ghi thêm, hoàn tác, từ chối sai nguồn/xung đột file, từ chối payload hỏng/đường dẫn thoát phạm vi, tự hoàn tác khi build lỗi, bảo vệ thay đổi phát sinh sau cài.

Đây là kiểm tra trình duyệt trên dữ liệu giả lập và build Linux. Chưa thay thế nghiệm thu Windows, camera/GPU, mạng thật hoặc toàn bộ chức năng với dữ liệu kho thực tế. Sau cài cần mở các tab và thử một ảnh mới tại local.

## Bản sao mã cũ và hoàn tác

Bản sao nằm trong `archive/frontend_design/<moc_cai>/originals`, kèm receipt.json và build.log. Trình cài in chính xác đường dẫn biên bản.

Nếu cần trở lại giao diện cũ, dừng frontend và chạy từ thư mục dự án:

```powershell
.\.venv\Scripts\python.exe .\tools\frontend_design\install.py --rollback "DUONG_DAN_RECEIPT_JSON_DA_IN_KHI_CAI"
```

Thay phần trong dấu nháy bằng đường dẫn receipt.json thật. Chạy lại Vite và Ctrl+F5. Hoàn tác phục hồi nguồn, không đụng dữ liệu kiểm kê; nếu dùng frontend/dist thay vì Vite dev thì cần build lại sau hoàn tác.
