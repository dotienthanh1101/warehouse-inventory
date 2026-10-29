# Kịch bản demo local

Dùng tài khoản và kho thử riêng. Không thay tồn đầu ngày của kho đang vận hành để
chuẩn bị demo, không xóa ảnh hoặc phiên cũ. Nếu chưa thể có ngày thử trống, dùng
các bài kiểm tra cách ly thay cho can thiệp dữ liệu thật.

1. Đăng nhập user A. Mở kho, danh mục và camera LAN. User B không thấy dữ liệu A.
2. Mở ngày thử có tồn đầu 0; tạo phiên. Chụp A, lưu hiệu chỉnh và ghi mới: tổng 1.
3. Chụp lại A, kiểm tra trùng, chọn cùng kiện: tổng vẫn 1.
4. Chụp B khác vật, ghi mới theo điều kiện hoặc xác nhận thủ công: tổng 2.
5. Kiểm một vật có hai nhãn cùng ID: đếm theo vùng vật, không theo số nhãn.
6. Sau khi đã kiểm đủ toàn bộ phạm vi của phiên, chốt. Bản chốt là số thực đếm.
7. Tạo phiên tiếp theo: mốc đối chiếu lấy phiên đã chốt, số vừa ghi bắt đầu 0 và
   chưa kết luận tăng/giảm. Kiểm lại toàn kho có A, B, C: tồn phiên 3, tăng 1 so 2.
8. Chốt phiên, xem báo cáo và chốt ngày. Công thức tổng: tồn đầu + tăng − giảm =
   tồn cuối. Không cộng tồn 2 của phiên trước với tồn 3 để thành 5.
9. Ở Báo cáo, bấm Xem nguồn trên một dòng loại hàng. Chọn phiên; xem ảnh, revision,
   người xác nhận, lịch sử và quyết định mới/trùng. Bản cũ thiếu người duyệt/ảnh
   phải hiển thị thiếu, không suy đoán.
10. Xuất CSV/Excel. Sau chốt, không tạo thêm phiên/ghi thêm vào ngày đó. Tải lại
    bản nháp đã ghi không làm tăng số lượng.
11. Admin kiểm tài khoản, tra cứu kho chỉ đọc và nhật ký; không thao tác kiểm kê
    bằng vai trò admin. Không hiển thị mật khẩu gốc của người dùng.

## Kiểm tra có thể chạy mà không dùng dữ liệu thật

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\tools\check_local_integrity.py
.\.venv\Scripts\python.exe .\tools\check_local_readiness.py --gpu
```

Bài HTTP trong tests dùng httpx, có thể cài qua backend/requirements-test.txt.
Các kiểm tra trên dùng kho tạm. Bài HTTP dùng fixture xác thực/AI, không thay thế
kiểm thử PostgreSQL thật hoặc độ chính xác của mô hình. Công cụ readiness ghi
manifest phiên bản gói tại docs, không đọc config mật khẩu, database hay ảnh.

## Việc chưa thể nghiệm thu bằng mã nguồn

- CV: cần bộ ảnh/vật độc lập mới, có nhãn đúng/sai thật, nhiều vật và marker khó.
  Giữ nguyên ngưỡng; công bố số mẫu, cộng mới nhầm, tỷ lệ tự động/thủ công và thời gian.
- Camera: cần luồng LAN đang hoạt động và quan sát dài hạn trên máy Windows.
- Backup: chạy công cụ sao lưu thủ công của dự án trên máy có PostgreSQL/dữ liệu
  thật. Không coi GitHub source là bản sao lưu dữ liệu vận hành.
- Ảnh lịch sử mất: giữ ngoại lệ đã ghi nhận; không tạo ảnh giả, xóa tham chiếu hoặc
  tự đánh dấu NFR-11 hoàn tất. Chính sách lưu/xóa vẫn cần chủ dự án quyết định.
- HTTPS/camera Internet tiếp tục tạm dừng. Không tuyên bố đạt 100 camera/AI đồng thời.
