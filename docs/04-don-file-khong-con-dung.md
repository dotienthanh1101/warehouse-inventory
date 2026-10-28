# Kết luận dọn dự án ngày 23/10/2026

Báo cáo đầu vào: project_layout_audit_20261023_145055_419956.zip.
Đã so với báo cáo 14:27: mã ứng dụng và file còn tại vị trí cũ không thay đổi;
27 mục đã được chuyển vào archive/project_organization/20261023_144255_623104_e1568592.

## Giữ nguyên phần chạy ứng dụng

| Khu vực | Nội dung |
|---|---|
| backend/app | Giữ đủ 19 file: API, camera, ArUco, AI, cơ sở dữ liệu, phiên và ngày kiểm kê |
| frontend/src | App.jsx, CatalogPanel.jsx, main.jsx và CSS đang sử dụng |
| data | SQLite, bản nháp, biên nhận, dữ liệu đối chiếu |
| captures | Ảnh bằng chứng được API và các biên nhận tham chiếu |
| docs | Phạm vi, hướng dẫn và biên bản dọn |
| tools | Các công cụ quét, sắp xếp và dọn; không thuộc thuật toán nhận diện |
| archive | Giữ bộ ảnh, marker, cấu hình thử, CSV/JSON kết quả, HTML và ảnh minh họa |

main.py, inventory_service.py vẫn được daily_api -> review_api -> main gọi.
Không xóa hoặc chia lại backend chỉ vì tên file có vẻ thuộc phiên bản cũ.

## Danh sách xóa khỏi thư mục dự án

| Nhóm | Số file | Quy tắc |
|---|---:|---|
| Mã thử nghiệm cũ | 53 | Script test, công cụ thử và bản dự phòng React đã ở archive |
| Hai gói cập nhật cũ | 21 | dino_integration, surface_quality_fix và bản sao lưu kèm theo |
| Cache thử nghiệm | 306 | JSON/NPZ tạm của benchmark_validation, foreground_trial, holdout_validation |
| ZIP bộ ảnh đã giải nén | Tối đa 2 | Chỉ xóa nếu từng thành viên trong ZIP có bản rời giữ lại giống từng byte |

Tối đa 382 file, 74.425.430 byte (khoảng 74,4 MB) được bỏ khỏi thư mục dự án.
Nếu ZIP còn nội dung chưa có bản rời khớp hoàn toàn, công cụ giữ nguyên ZIP đó.
Danh sách đường dẫn cụ thể có trong tools/project_cleanup_plan.json hoặc chạy
finalize_project_cleanup.py không có --apply để xem.

Mã thử cũ có thể còn giá trị tái lập nghiên cứu, nhưng không được ứng dụng hiện tại
sử dụng. Vì vậy toàn bộ file sắp xóa được đóng vào MỘT ZIP phục hồi ngoài dự án:
D:\Kiem_ke_kho_Buoc_1\warehouse_inventory_backups\cleanup_<thời điểm>.zip.
ZIP được kiểm tra SHA-256 từng file trước khi xóa các bản rời trong dự án.
Bộ ảnh, báo cáo HTML, ảnh minh họa và CSV/JSON kết quả cuối không bị xóa.

## Một lệnh thực hiện

Giải nén project_cleanup.zip vào D:\Kiem_ke_kho_Buoc_1\warehouse_inventory.
Giữ backend và frontend đang chạy. Trong lúc dọn, tạm ngừng chụp/ghi kiểm kê.
Chạy trong PowerShell:

```powershell
& "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory\.venv\Scripts\python.exe" "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory\tools\finalize_project_cleanup.py" --apply
```

Kiểm tra trước: mã hiện tại phải trùng báo cáo; từng file xóa phải đúng bản đã quét.
Kiểm tra sau: hash của mã ứng dụng, SQLite, bản nháp, ảnh chụp và kết quả HTTP
backend/frontend phải khớp trước khi dọn. Lỗi hoặc Ctrl+C giữa bước di chuyển sẽ
trả các file về lại archive; không ghi đè số liệu kiểm kê.

## Khôi phục

Nếu cần lấy lại các file đã xóa, chạy tại thư mục gốc dự án:

```powershell
.\.venv\Scripts\python.exe .\tools\finalize_project_cleanup.py --restore
```

Sau khi khôi phục file bằng lệnh này mới dùng organize_project.py --undo nếu muốn
trả các bộ thử nghiệm từ archive ra vị trí cũ. Chức năng --undo của công cụ sắp xếp
cũ không chạy được khi các file trong kho đã bị dọn, vì nó kiểm tra đủ nội dung gốc.

## Kiểm tra trước khi gửi

8 kiểm tra giao dịch file với dữ liệu mô phỏng trên cùng 382 đường dẫn: xóa/khôi
phục, bảo toàn file vận hành, ZIP chưa có bản rời khớp, nguồn ứng dụng thay đổi,
lỗi HTTP sau khi dọn, Ctrl+C giữa chừng, không ghi đè file mới, và chặn đường dẫn
liên kết/ngoài archive. Chưa chạy ứng dụng Windows thực tế trong môi trường này;
kiểm tra hash và HTTP thực tế sẽ chạy trên máy bạn trong --apply.
Không thay đổi hoặc kiểm thử lại thuật toán AI trong lượt dọn file này.
