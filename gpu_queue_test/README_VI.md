# NFR-06 · Hàng đợi phân tích ảnh và GPU

Bài thử dùng **10 tài khoản, mỗi tài khoản một kho/phiên riêng**, hàng đợi tối đa 8 và một worker GPU. Không sửa mã backend/React. DB, ảnh, phiên và bản nháp thử đều nằm trong vùng riêng và được dọn khi kết thúc.

## Cần làm

1. Giải nén `gpu-queue-test.zip` vào `D:\Kiem_ke_kho_Buoc_1\warehouse_inventory`. Có file `gpu_queue_test\run.py`.
2. Chuẩn bị **một ảnh hàng JPG/PNG có sẵn**, nhìn rõ vật. Không cần chụp thêm hoặc có nhiều camera.
3. Chờ tác vụ ảnh đang chạy hoàn tất, dừng backend chính bằng **Ctrl+C**. Giữ PostgreSQL chạy.
4. PowerShell:

```powershell
cd "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.\.venv\Scripts\python.exe .\gpu_queue_test\run.py
```

Chọn ảnh trong cửa sổ hiện lên. Nếu Python không có cửa sổ chọn ảnh, dán đường dẫn ảnh khi terminal hỏi. Sau đó nhập mật khẩu PostgreSQL của `postgres`. Không cần cài thư viện mới hoặc tải mô hình mới; dùng CUDA và Grounding DINO đã cài/tải trong dự án.

Có thể chỉ định ảnh trực tiếp bằng `--image "D:\duong_dan\anh.jpg"` nếu cần.

5. Chờ đủ năm bước, gửi lại phần `KIỂM TRA HÀNG ĐỢI / GPU` ở terminal.
6. Khởi động lại backend chính trong **CMD**:

```cmd
cd /d "D:\Kiem_ke_kho_Buoc_1\warehouse_inventory"
.venv\Scripts\python.exe -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Không thêm `--reload` hoặc `--workers`. React có thể giữ nguyên; tải lại trang sau khi backend chính chạy lại.

## Kết quả mong đợi

- Nhận **8/10**, từ chối do hàng đợi đầy **2**; lỗi gửi không dự kiến **0**.
- Gửi lại cùng request trả đúng cùng task, không tạo thêm task.
- Cùng user gửi một request khác khi đang bận: **409**.
- Sau khi xử lý xong, gửi lại một tác vụ từng bị từ chối phải được nhận. Vì có bước này nên tổng **9 tác vụ hoàn tất** là đúng.
- Thất bại **0**, còn chờ **0**, lỗi phân tích GPU **0**.
- Không lỗi ảnh/kết quả/quyền truy cập kho; chỉ một worker chạy; số dòng kiểm kê ghi vào phiên **0**.
- Có số đo VRAM cấp phát/giữ của PyTorch, thời gian phân tích lượt đầu và p95 các lượt sau, p95 chờ trong điều kiện bài thử, phản hồi API đọc khi GPU bận.

Nếu chưa đạt, giữ báo cáo và gửi kết quả. Công cụ không tự thay mô hình/ngưỡng, tăng worker hoặc chỉnh chính sách hàng đợi để che lỗi.

## Bài thử tác động vào đâu?

- Đối chiếu hash 37 file backend theo bản đã xác nhận sau `server-capacity-update.zip`.
- Dùng cơ chế cách ly đã chạy đúng ở `load-test-100`: DB tên `warehouse_load_test_...`, có nhãn sở hữu; thư mục thử nằm **cùng ổ lưu dữ liệu kho**.
- Tạo 10 tài khoản/phiên kiểm kê thử với tồn 0. Chỉ đọc cấu hình hiện có để kết nối PostgreSQL; không ghi vào DB kiểm kê thật.
- Đọc ảnh bạn chọn; nếu cạnh lớn nhất vượt 1920 px, thu về 1920 px, giữ tỷ lệ. Không sửa file ảnh nguồn.
- Tạo bản sao ảnh có một nhãn góc riêng cho mỗi tài khoản. So hash JPEG để kiểm chứng kết quả chứa đúng ảnh của kho đó.
- Nguồn camera trong **backend thử** được thay bằng ảnh phát lại. Hàng đợi, xác thực, mã hóa/lưu ảnh, tạo bản nháp và suy luận Grounding DINO dùng code thật của dự án.
- Tạm giữ worker trong giai đoạn gửi 10 yêu cầu để kiểm ranh giới 8 một cách lặp lại được. Sau khi đủ phản hồi, mở worker và thực hiện suy luận thật. Thời gian giữ này được ghi riêng; thời gian chờ thô có chứa phần giữ có kiểm soát.
- API đọc chạy song song khi worker được mở. Kiểm tra quyền truy cập task/kết quả/ảnh bằng tài khoản khác; phải trả 404.
- Kiểm số receipt và instance bằng cách mở **SQLite của vùng thử ở chế độ chỉ đọc**; không dùng ngữ cảnh kho thật của tiến trình điều khiển.
- Không gọi xác nhận ghi số lượng, chốt phiên/ngày, kết nối IP camera hay gửi thông tin ra Internet. Tắt truy cập mạng Hugging Face trong backend thử; nếu cache thiếu sẽ báo lỗi.
- Chỉ DROP DB có đúng tên/nhãn bài thử. Không phục hồi đè dữ liệu thật. Nếu bị đóng cưỡng bức/mất điện, cần kiểm tra tên DB/thư mục thử còn lại theo cảnh báo, không xóa DB khác.

## Hiểu đúng số đo

- Đây là **phân tích ảnh chụp** qua hàng đợi, không phải tải so sánh trùng bằng DINOv2/LoFTR. Không đo thu nhận/preview camera thật, Internet hoặc 100 tác vụ GPU chạy song song.
- VRAM là số cấp phát/giữ cao nhất của PyTorch trong backend thử. Không phải toàn bộ VRAM của card; desktop/ứng dụng khác vẫn có thể chiếm thêm.
- Lượt đầu có nạp mô hình từ cache, vì vậy được báo riêng. Không so trực tiếp lượt đầu với các lượt đã nạp mô hình.
- Cổng thử loopback 8001–8090. Một backend thử duy nhất. Không mở cổng ra Internet.
- Kết quả nhận diện có thể có cảnh báo nếu ảnh khó; bài này kiểm tài nguyên/hàng đợi/cách ly, không đánh giá độ chính xác CV hoặc đếm số kiện.
- Không tự kết luận nghiệm thu toàn bộ 100 user/production từ bài này. Cần ghi nhận số đo trên máy thật và tách điều kiện đo.

## Báo cáo và hoàn tác

Báo cáo ở `docs\gpu_queue_*.json`, không chứa mật khẩu/cookie hay ảnh nguồn. Không cần gửi file cấu hình bí mật hoặc backup toàn dự án.

Không có thay đổi mã ứng dụng cần hoàn tác. Có thể bỏ thư mục `gpu_queue_test` khi không cần. Kết quả kiểm tra công cụ và giới hạn môi trường được ghi trong `validation.json`; GPU thật chỉ được xác nhận sau khi chạy trên máy người dùng.
