# Sắp xếp dự án theo báo cáo ngày 23/10/2026

Báo cáo đã đọc: 1.780 file, 94 file mã nguồn, không có lỗi phân tích cú pháp.
19 file Python trong backend/app đều nằm trong chuỗi import từ daily_api.py.
main.py và inventory_service.py vẫn được gọi qua review_api.py, nên giữ nguyên.
SHA-256 của visual_similarity.py và duplicate_check.py khớp bản trước thử ngưỡng linh hoạt.

## Phương án đã chuẩn bị

- Giữ backend, frontend đang chạy, data, captures, môi trường Python và file khởi động ở nguyên vị trí.
- Chuyển 27 mục (26 mục cấp gốc và một file dự phòng trong frontend) vào kho lưu trữ.
- Tổng cộng 1.478 file, khoảng 478 MB theo báo cáo; không xóa vĩnh viễn.
- Không sửa nội dung file Python/React/CSS hoặc tham số nhận diện/đối chiếu.
- Các bản cập nhật cũ được giữ cả thư mục backups bên trong để tra cứu.
- Hai file __init__.py giống nhau vẫn cần cho hai package khác nhau, không xóa vì trùng nội dung.
- Các bản benchmark_engine.py giống visual_similarity.py là bản chụp thử nghiệm; giữ nguyên trong kho lưu trữ.

## Cách dùng

Giải nén project_organizer.zip vào D:\Kiem_ke_kho_Buoc_1\warehouse_inventory.
Bật dự án như bình thường bằng start_project.bat. Giữ backend và frontend chạy;
trong lúc sắp xếp không bấm chụp, sửa danh mục hoặc ghi kiểm kê.

Xem danh sách trước, trong PowerShell tại thư mục gốc dự án:

```powershell
.\.venv\Scripts\python.exe .\tools\organize_project.py
```

Thực hiện:

```powershell
.\.venv\Scripts\python.exe .\tools\organize_project.py --apply
```

Bộ sắp xếp kiểm tra nguồn và danh sách file so với báo cáo đã gửi. Nếu phát sinh thay
đổi hoặc thiếu file thì dừng trước khi di chuyển. Nó kiểm tra backend, danh mục/ngày,
trang Vite và hai module frontend trước/sau, đồng thời so SHA-256 của mã ứng dụng,
ảnh chụp và dữ liệu. Lỗi ở bước kiểm tra sau sẽ đảo ngược các lần di chuyển.
Không ghi lại tồn kho hoặc thay thế SQLite khi khôi phục.

## Vị trí sau sắp xếp

Kho lưu trữ mỗi lượt nằm ở archive/project_organization/<thời điểm>/.
Bảng dưới dùng đường dẫn tương đối bên trong kho đó.

| Trước đây | Bên trong kho lưu trữ |
|---|---|
| `adaptive_threshold_trial` | `research/adaptive_threshold_trial` |
| `benchmark_capture` | `research/benchmark_capture` |
| `benchmark_dataset` | `research/benchmark_dataset` |
| `benchmark_holdout` | `research/benchmark_holdout` |
| `benchmark_holdout_dataset` | `research/benchmark_holdout_dataset` |
| `benchmark_validation` | `research/benchmark_validation` |
| `comparison_images` | `research/comparison_images` |
| `comparison_results` | `research/comparison_results` |
| `de_validation` | `research/de_validation` |
| `fg_validation` | `research/fg_validation` |
| `foreground_trial` | `research/foreground_trial` |
| `holdout_validation` | `research/holdout_validation` |
| `loftr_trial` | `research/loftr_trial` |
| `test_markers` | `research/test_markers` |
| `dino_integration` | `old_updates/dino_integration` |
| `surface_quality_fix` | `old_updates/surface_quality_fix` |
| `test_camera.py` | `manual_tests/test_camera.py` |
| `test_compare_ab_manual.py` | `manual_tests/test_compare_ab_manual.py` |
| `test_compare_boxes.py` | `manual_tests/test_compare_boxes.py` |
| `test_compare_de.py` | `manual_tests/test_compare_de.py` |
| `test_duplicate_regions.py` | `manual_tests/test_duplicate_regions.py` |
| `test_duplicate_review.py` | `manual_tests/test_duplicate_review.py` |
| `test_fix_review_mask.py` | `manual_tests/test_fix_review_mask.py` |
| `test_fullscene_overlay.py` | `manual_tests/test_fullscene_overlay.py` |
| `test_mask_c.py` | `manual_tests/test_mask_c.py` |
| `test_viewpoint_baseline.py` | `manual_tests/test_viewpoint_baseline.py` |
| `frontend/src/App.before-review.jsx` | `frontend_backups/App.before-review.jsx` |

Bên ngoài archive, các mục thao tác hằng ngày còn backend, frontend, data, captures,
docs, tools, môi trường Python, README và start_project.bat.
Các thư mục cache/thư viện đang dùng không được dọn bỏ trong lượt này.

## Chạy lại các thí nghiệm cũ

Các bộ thử nghiệm đã được đóng băng để lưu trữ. Lệnh cũ dùng vị trí cũ và đường dẫn
__file__/parents nên KHÔNG hứa chạy trực tiếp sau khi chuyển. Cần khôi phục vị trí
trước khi chạy lại thí nghiệm:

```powershell
.\.venv\Scripts\python.exe .\tools\organize_project.py --undo
```

Lệnh này kiểm tra nội dung đã lưu rồi trả tất cả 27 mục về vị trí cũ. Nó dừng nếu có
file mới chiếm vị trí cũ, thay vì ghi đè công việc mới. Nội dung ứng dụng/kiểm kê mới
phát sinh sau khi sắp xếp không bị thay thế.

## Kiểm tra đã thực hiện trước khi gửi

Đã thử trên cấu trúc mô phỏng cùng 27 mục và 1.478 đường dẫn ứng viên của báo cáo:
chuyển + khôi phục; mã nguồn thay đổi; bộ ảnh thay đổi; HTTP lỗi sau khi chuyển;
ngắt bằng Ctrl+C giữa chừng; và tránh ghi đè file mới lúc khôi phục.
Đây là kiểm tra giao dịch file với dữ liệu mô phỏng, chưa phải chạy ứng dụng Windows
của bạn. Kiểm tra HTTP và SHA-256 thực tế sẽ chạy trên máy bạn khi dùng --apply.
Không chạy lại mô hình AI vì mã/đầu vào vận hành không đổi.
