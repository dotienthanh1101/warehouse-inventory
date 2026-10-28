# Nguồn gốc và phạm vi bản đưa lên GitHub

- Nguồn: ZIP warehouse_inventory_source_20261028_104213_581af0d0.zip do chủ dự án gửi,
  chứa 135 file, xuất từ thư mục đang dùng ngày 28/10/2026.
- Giữ mã backend/frontend, công cụ và bài thử đã có. Không ghép nguồn cũ hoặc tự áp
  dụng thêm bản vá giao diện. README chính được cập nhật, README cũ giữ ở docs/history.
- GITHUB_EXPORT.json là danh sách file tại thời điểm xuất, không phải danh sách hash
  của mọi file sau khi cập nhật tài liệu cho repo.
- Đây là bản nguồn, không phải toàn bộ dữ liệu sao lưu của máy. Không gồm database,
  tài khoản, mật khẩu, config riêng, ảnh, bản nháp, kết quả, .venv/node_modules,
  model cache, bộ cài/bản vá ZIP và tài liệu nhị phân chưa đưa vào bản xuất.
- Các công cụ tổ chức/cài bản vá lịch sử có thể cần manifest/payload/biên bản không có
  trong repo. Đặc biệt tools/frontend_design/install.py là bộ cài đã áp dụng trước đó;
  không chạy lại từ repo này vì manifest và payload không đi kèm. Giao diện hiện tại
  nằm trực tiếp trong frontend/src và build từ đó.
- Các phép kiểm tra khi chuẩn bị repo chỉ đọc/phân tích nguồn và build frontend;
  không chạy tác vụ sao lưu/khôi phục, tạo tài khoản thật hay kiểm tải lên kho vận hành.
- Chưa chỉ định license mới thay cho chủ dự án.
