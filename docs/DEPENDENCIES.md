# Phụ thuộc và giới hạn tái lập môi trường

## Các khai báo đi kèm nguồn

- backend/requirements.txt: FastAPI, Uvicorn.
- backend/requirements-auth.txt: psycopg và Argon2.
- backend/requirements-report.txt: openpyxl.
- backend/requirements-backup.txt: cryptography.
- backend/requirements-video.txt: websockets, imageio-ffmpeg.
- camera_bridge/requirements.txt: phụ thuộc riêng cho cầu nối.
- frontend/package.json và package-lock.json: React, Vite và bộ build.

## Thành phần được import nhưng chưa khóa đầy đủ trong requirements

OpenCV có aruco, NumPy, Pillow, torch, torchvision, transformers, huggingface_hub,
Kornia và truststore. Bài thử có thể dùng thêm thành phần được import động.
Cần lấy phiên bản tương thích từ môi trường Windows/GPU đang chạy để lập khóa
phụ thuộc trước khi tuyên bố cài mới hoàn toàn tái lập. Không tự chọn một bản
PyTorch/CUDA khác rồi ghi nhận là môi trường đã nghiệm thu.

Grounding DINO và DINOv2 tải trọng số theo mã nguồn. LoFTR cần checkpoint và kết nối
tải lần đầu hoặc cache phù hợp. Trọng số không nằm trong Git. FFmpeg/ffprobe và các
công cụ PostgreSQL cần được cài/tìm thấy theo các công cụ vận hành thực tế.

Bản README được cập nhật để phản ánh đúng khoảng trống này; không sửa thuật toán,
ngưỡng, package-lock hoặc tự đổi requirements của hệ thống đang dùng.
