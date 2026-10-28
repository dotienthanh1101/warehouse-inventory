# Cầu nối video ổn định theo nguồn 1080p/30 đã đo

Chạy `.venv\Scripts\python.exe camera_bridge\run.py` trên máy cùng LAN với camera. Nhập HTTPS hiện tại, URL camera, tài khoản CAMERA nếu có và mã ghép mới từ React. Không nhập mật khẩu website vào ô camera.

Bản này thay chế độ H264 COPY trước đó bằng một lần đọc camera qua FFmpeg native. FFmpeg giải mã, chuẩn hóa timestamp theo thứ tự các khung đã nhận và tách hai đầu ra:

- Video giữ kích thước nguồn (tối đa 1920×1080), mục tiêu 30 FPS, GOP cố định khoảng 0,5 giây. Tự thử NVIDIA NVENC; khi không dùng được thì chạy encoder CPU native. Không đưa toàn bộ ảnh BGR qua pipe Python. Không dùng bộ lọc FPS video để tạo các khung lặp nhằm giả đạt 30 FPS. Nếu nguồn thật chậm, FPS hiển thị vẫn giảm.
- JPEG đầy đủ độ phân giải, khoảng 1 ảnh/giây, lấy từ ảnh đã giải mã trước bước mã hóa H.264 đầu ra. JPEG đi qua cổng TCP tạm chỉ bind 127.0.0.1 tại máy kho, giữ khung mới nhất trong RAM. Không mở cổng này ra Internet, không thêm quy tắc firewall và không lưu JPEG ra đĩa tại máy kho. Nguồn camera không bị mở thêm một lần bằng OpenCV.

Chất lượng video được mã hóa lại với mục tiêu khoảng 4 Mbps, đỉnh cấu hình 5 Mbps; đây không phải video H264 nguyên bản. JPEG dùng thang chất lượng FFmpeg, ánh xạ từ tùy chọn CLI `--jpeg-quality` (mặc định 80). Ảnh kiểm kê không lấy từ video đầu ra đã mã hóa lại. Bài thử JPEG 1080p đọc được ArUco ID23, nhưng chưa phải kiểm định độ chính xác toàn bộ dữ liệu thực tế.

Đã bỏ việc tự khởi động lại vì FPS/GOP ước lượng của một mảnh video. Mất WebSocket tạm thời giữ tiến trình camera còn khỏe; chỉ đổi tiến trình khi nguồn thực sự lỗi hoặc dừng. Số thứ tự JPEG vẫn tăng sau khi tiến trình nguồn phải khởi động lại. Lỗi kết nối mạng của kênh JPEG được thử lại mà không tự dừng kênh video còn hoạt động; lỗi thu hồi quyền/đăng nhập vẫn dừng theo chính sách.

React đo FPS thực tế được trình bày. Có bộ đệm ngắn, điều chỉnh nhẹ tốc độ phát để hạn chế thiếu đệm và không nhảy bỏ hàng loạt khung mỗi khi nhận đoạn mới. Tốc độ cấu hình 30 không phải cam kết 30 khung mới mỗi giây khi nguồn hoặc mạng thiếu dữ liệu.

Giữ ứng dụng camera hoạt động và máy tính đồng bộ giờ. Khóa ghép dùng một lần, hết hạn sau 5 phút; khóa cầu nối chỉ ở RAM, tối đa 8 giờ và phụ thuộc phiên đăng nhập tạo mã. Khi đăng xuất, thu hồi, đổi mật khẩu, ngắt nguồn hoặc máy chủ khởi động lại có thể cần ghép mã mới.

Nhấn Ctrl+C ở cửa sổ cầu nối để dừng; tạo mã mới rồi chạy lại cùng lệnh. Nếu nguồn cũ còn chiếm vì mất mạng, bấm Ngắt cầu nối trên React trước khi tạo mã.
