# NFR-06 — Kiểm tra hàng đợi so sánh trùng / GPU

Bộ kiểm tra riêng; không thay file backend/frontend, không thay ngưỡng 0.40/0.75.
Dùng hàng đợi và DINOv2/LoFTR đang có của dự án, không huấn luyện mô hình.

## Chạy trên Windows

1. Giải nén để có `warehouse_inventory\duplicate_gpu_test\run.py`.
2. Đợi tác vụ ảnh đang chạy xong. Dừng backend chính bằng Ctrl+C; giữ PostgreSQL chạy.
3. PowerShell:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.\.venv\Scripts\python.exe .\duplicate_gpu_test\run.py
```

4. Chọn một JPG/PNG có sẵn, có vật và chi tiết rõ. Không cần chụp thêm hay thao tác React. Nhập mật khẩu PostgreSQL của `postgres` để tạo/dọn DB thử. Không phải mật khẩu admin website.
5. Chờ đủ năm bước. Không chạy song song công cụ AI khác hoặc khởi động backend chính trong bài thử.
6. Gửi phần `KIỂM TRA HÀNG ĐỢI SO SÁNH TRÙNG / GPU`. Nếu chưa hoàn tất, gửi lỗi cùng biên bản JSON mà công cụ chỉ ra. Không gửi cấu hình chứa mật khẩu.

Nếu cửa sổ chọn ảnh không mở, dán đường dẫn ảnh khi được hỏi, hoặc chỉ định:

```powershell
.\.venv\Scripts\python.exe .\duplicate_gpu_test\run.py --image "D:\duong_dan\anh.jpg"
```

Khởi động lại backend chính trong CMD khi công cụ đã dọn xong:

```cmd
cd /d "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

## Bài thử làm gì

- Kiểm tra mã backend khớp bản hiện tại đã dùng cho bài kiểm tải trước. Nếu lệch, dừng và báo tên file; không ghi đè.
- Tạo PostgreSQL tạm được đánh dấu, 10 tài khoản và 10 kho riêng, trên cùng ổ với vùng lưu kho để kiểm tra dung lượng đúng ổ. Không dùng tài khoản/kho thật.
- Tạo ba bản nháp đã duyệt trong mỗi kho bằng các bản sao của một ảnh đã chọn: hai ảnh lịch sử và một ảnh hiện tại; một vùng hàng cố định toàn ảnh. Nhãn TEST riêng chứng minh đúng kho. Một nhãn góc giả có vùng che cố định để chạy hai nhánh gốc/che; không kiểm định khả năng đọc mã.
- Giữ worker trong lúc gửi 10 yêu cầu để kiểm tra giới hạn 8 một cách tái lập. Đây là tải dồn có kiểm soát, không phải lưu lượng tự nhiên.
- Kiểm tra cùng request trả cùng task; yêu cầu mới khi user bận trả 409; đầy toàn hệ thống trả 429.
- Chạy một lượt phân tích Grounding DINO thật trước lượt so sánh đầu, giữ mô hình theo cách chương trình đang dùng để phản ánh áp lực GPU sau khi đã chụp ảnh. Không thay chính sách GPU/CPU của chương trình.
- Chạy thật DINOv2/LoFTR trong hàng đợi; mỗi tác vụ phải đối chiếu đủ hai ảnh lịch sử, cả hai nhánh gốc/che, trả đúng review/session và nguyên ảnh đúng kho; tài khoản khác phải nhận 404 khi đọc dữ liệu đó.
- Sau 8 tác vụ, gửi lại một tác vụ từng bị từ chối để kiểm tra hàng đợi phục hồi: tổng 9 hoàn tất, 18 cặp ảnh, 45 lượt thao tác mô hình (mỗi thao tác xử lý hai nhánh; không phải 45 cặp ảnh).
- Không xác nhận bản nháp vào phiên; số dòng biên nhận/kiện phải bằng 0. Các JSON kết quả đối chiếu chỉ nằm trong kho thử và được dọn cùng kho.
- Lưu biên bản vào `docs\duplicate_gpu_queue_*.json`, dọn DB/kho thử sau khi dừng worker.

## Đọc kết quả

Mong đợi: nhận 8/10, từ chối đầy 2, hoàn tất 9, thất bại/chờ/lỗi quyền/lỗi mô hình bằng 0, một worker, 18 cặp, không ghi kiểm kê, dọn xong.

`Kiểm tra vận hành đạt` đòi hỏi đủ kết quả, đủ các lượt mô hình, quyền kho đúng, API đọc không lỗi và dọn sạch.
`Mọi thao tác mô hình chạy CUDA` là kiểm tra riêng: chính sách hiện tại có thể chuyển mô hình sang CPU khi GPU thiếu bộ nhớ. Khi có CPU, không gọi đó là toàn bộ xử lý trên GPU; xem số lượt CPU và thời gian. Lỗi OOM trong thao tác mô hình được ghi kể cả sau đó phục hồi CPU.

VRAM là bộ nhớ PyTorch cấp phát/giữ trong tiến trình, không phải tổng bộ nhớ của toàn card.
Thời gian lượt đối chiếu đầu không gồm lượt nạp trước Grounding DINO (in riêng); thời gian chờ hàng đợi có gồm lượt nạp trước và khoảng giữ worker có kiểm soát. p95 không phải SLA nghiệm thu đã định trước.
CPU tính theo một lõi nên có thể vượt 100%. RAM đo PID Uvicorn thật, không lấy PID launcher Python trên Windows.

Nếu dừng sớm, biên bản lỗi chỉ chứa bước, loại lỗi và trace mô hình thử; không chứa mật khẩu/cookie. Luôn xem kết quả dọn dẹp trước khi tiếp tục.

## Phạm vi

Đây là kiểm tra vận hành hàng đợi với dữ liệu tổng hợp từ ảnh có sẵn, không phải kiểm định độ chính xác trùng/khác kiện, camera thật, tự khoanh vùng, đọc/che ArUco thực, nhiều kiện che lấp, phiên dài hoặc 100 GPU task đồng thời. Hai ảnh lịch sử giống nhau chỉ để bảo đảm mô hình thật chạy và hệ thống rà soát đủ lịch sử, không dùng làm bằng chứng độ chính xác.

Đã kiểm 15 ca đơn vị và chạy tích hợp hàng đợi/HTTP/SQL/SQLite/OpenCV thật với suy luận mô phỏng trong môi trường phát triển; không có GPU tại môi trường đó. Số đo GPU thật phải lấy từ lần chạy trên máy Windows của bạn. Xem `validation.json`.
