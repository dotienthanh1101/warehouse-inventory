# Cấu trúc dự án kiểm kê kho

Cập nhật theo bản quét ngày 27/10/2026. Gói dọn dẹp giữ đường dẫn vận hành hiện tại.

## Mã vận hành

| Vị trí | Vai trò |
| --- | --- |
| `backend/app/` | FastAPI, tài khoản, phạm vi kho, camera, AI, kiểm kê, báo cáo và dung lượng. 40 file nằm trong đồ thị import tĩnh từ `daily_api`; giữ nguyên. |
| `backend/requirements*.txt` | Các nhóm thư viện backend; giữ tên và vị trí. |
| `frontend/src/` | React, màn hình người dùng/admin, giao diện và gọi API. |
| `frontend/package.json`, `frontend/package-lock.json`, cấu hình Vite | Môi trường và build frontend. Không xóa lockfile. |
| `start_project.bat` | Điểm khởi động local hiện tại. |

## Cấu hình và dữ liệu

| Vị trí | Vai trò |
| --- | --- |
| `config/` | Cấu hình xác thực và kết nối; giữ riêng bí mật. |
| `data/warehouses/` | Dữ liệu, ảnh và bản nháp tách theo kho. |
| `data/`, `captures/` | Bao gồm dữ liệu và ảnh lịch sử còn được tham chiếu. Không suy ra thừa từ tuổi file. |

Không đổi tên hoặc di chuyển các mục này trong lần dọn dẹp. Công cụ không đọc nội dung database, ảnh hoặc cấu hình.

## Công cụ, kiểm thử và hồ sơ

| Vị trí | Vai trò |
| --- | --- |
| `tools/` | Cài tài khoản, sao lưu/khôi phục, đo dung lượng, kiểm tra toàn vẹn và quản lý cấu trúc. |
| `tests/` | Kiểm thử tự động hiện có. |
| `load_test_100/` | Bài nghiệm thu đăng nhập/đọc API cho 100 tài khoản. |
| `gpu_queue_test/` | Bài kiểm hàng đợi chụp/phân tích GPU. |
| `duplicate_gpu_test/` | Bài kiểm hàng đợi đối chiếu trùng GPU. |
| `check_camera_1080.py` | Chẩn đoán nguồn camera/encoder; giữ vị trí hiện tại. |
| `find_missing_evidence.py` | Tra cứu ảnh lịch sử đã mất; giữ để liên hệ biên bản ngoại lệ. |
| `docs/` | Hồ sơ, báo cáo, kết quả nghiệm thu và hướng dẫn cấu trúc này. |
| `archive/project_organization/` | Dữ liệu thử nghiệm và chứng cứ đã lưu từ lần tổ chức trước. |
| `archive/installed_update_packages/` | ZIP bộ cài cũ đã kiểm tra từng file, kèm biên bản để khôi phục. |

Các bộ tải/GPU có đường dẫn phụ thuộc vị trí và vẫn cần cho nghiệm thu lại. Lần dọn này giữ nguyên để tránh làm hỏng chúng; không coi chúng là mã ứng dụng thừa.

## Phần tạm dừng nhưng vẫn giữ

`camera_bridge/` và `internet_access/` phục vụ đường camera từ xa/HTTPS. Chức năng đang tạm dừng theo quyết định ưu tiên local, chưa bị loại khỏi phạm vi dự án. Không chuyển vào nhóm bộ cài cũ.

## Nhóm được thu gọn

20 thư mục bộ cài/bản vá, tổng 254 file được liệt kê trong audit, không có tham chiếu tên thư mục từ các file mã nguồn hoạt động được quét. Công cụ lưu chúng vào ZIP, kiểm tra SHA-256 toàn bộ nội dung rồi mới xóa bản rời. Cache có trong các bộ cài cũng được lưu để hoàn tác chính xác.

Việc không có tham chiếu tĩnh không đủ để xóa tùy ý một module. Lần dọn này chỉ áp dụng danh sách đóng của 20 **bộ cài**, có đối chiếu mã hiện tại và bản lưu khôi phục; không áp dụng quy tắc đó lên backend hay ảnh dữ liệu.

`.venv/`, `frontend/node_modules/` và cache không nằm trong số 2.141 file được báo cáo đầy đủ. Môi trường đang dùng được giữ nguyên. Lần dọn này không thay đổi mô hình/ngưỡng AI hoặc cấu trúc cơ sở dữ liệu.
