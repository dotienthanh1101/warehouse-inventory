# Đầu vào, đầu ra và phạm vi demo

Tài liệu ghi lại các quyết định đã thống nhất để làm căn cứ phát triển. Đây là đặc tả mục tiêu; các chức năng dưới đây chưa được triển khai trong bộ khung bước 1.

## Bài toán

Phần mềm nhận ảnh qua mạng từ camera IP gắn trên xe tự hành hoặc UAV, đọc mã kiện hàng và ô kệ, tổng hợp theo phiên và đối soát với danh sách dự kiến. Phạm vi bắt đầu tại đầu vào hình ảnh; phương tiện di chuyển và điều khiển thuộc hệ thống bên ngoài. Xử lý theo phiên, không đặt yêu cầu thời gian thực.

## Quy tắc nhận dạng

- Đối tượng ban đầu là kiện hàng/thùng có QR định danh riêng; hai kiện khác nhau phải có hai mã khác nhau, kể cả cùng loại hàng.
- Quy ước payload thử nghiệm: `ITEM:BOX001` cho kiện hàng và `LOC:A01` cho ô kệ. Nội dung trong QR cần tuân thủ quy ước này.
- QR ô kệ dùng để nhận dạng vị trí. Chỉ gán kiện hàng cho ô kệ khi xác định được vùng kệ tương ứng; việc hai mã xuất hiện cùng ảnh chưa đủ nếu ảnh có nhiều ô kệ.
- Trong một phiên, một mã kiện chỉ được tính một lần trong tổng số kiện đã ghi nhận. Nếu có bằng chứng vị trí mâu thuẫn, lưu các quan sát và chuyển sang cần kiểm tra.
- Mã không đọc được hoặc ảnh mờ/tối không được tự điền bằng mã từ danh sách dự kiến.

## Đầu vào dự kiến

1. Ảnh từ nguồn camera IP qua mạng, kèm thời điểm tiếp nhận và nguồn ảnh. Giao thức cụ thể sẽ chốt theo thiết bị thực tế.
2. Danh sách kiện dự kiến với mã kiện và vị trí dự kiến.
3. Cấu hình phiên kiểm kê: mã phiên, nguồn ảnh, thời điểm bắt đầu/kết thúc.

Ảnh hoặc video đã lưu có thể dùng để phát triển và kiểm thử lặp lại. Kết quả trên nguồn mô phỏng không thay thế việc kiểm tra tích hợp camera IP thật.

## Đầu ra dự kiến

- Danh sách mã kiện đã ghi nhận, vị trí quan sát được nếu đủ căn cứ, ảnh bằng chứng và thời gian.
- Số lượng mã kiện duy nhất đã ghi nhận theo phiên và theo vị trí đã xác định. Đây không phải số lượng toàn bộ hàng bị che khuất hoặc chưa quan sát.
- Đối soát: đúng vị trí, sai vị trí, mã ngoài danh sách, chưa quan sát được, cần kiểm tra lại.
- Lịch sử phiên và báo cáo xuất từ giao diện web.

"Chưa quan sát được" không đồng nghĩa với "thiếu hàng". Những kiện có bằng chứng đọc mã nhưng chưa rõ vị trí cần được giữ riêng để kiểm tra.

## Đánh giá dự kiến

Dùng ảnh có nhãn đối chiếu được kiểm tra thủ công, gồm ảnh rõ, mờ, thiếu sáng, chụp nghiêng, ảnh lặp và ảnh có nhiều ô kệ. Các biến thể của cùng ảnh gốc nằm trong cùng một tập dữ liệu. Tỷ lệ bỏ sót đọc mã được tính trên kiện có mặt và thuộc phạm vi quan sát theo nhãn đối chiếu; trường hợp ngoài phạm vi quan sát được báo riêng.

So sánh cách đọc từng ảnh riêng với cách tổng hợp nhiều ảnh bằng độ chính xác đọc mã, bỏ sót, ghi nhận trùng, đúng vị trí và thời gian xử lý. Chưa đặt ngưỡng chất lượng số học trước khi có dữ liệu nền.

## Công nghệ đã chọn

Python, OpenCV và NumPy cho xử lý ảnh; FastAPI cho backend; React/JavaScript/CSS với Vite cho frontend; SQLite và thư mục ảnh cho lưu trữ. YOLO chỉ được bổ sung nếu thử nghiệm cho thấy cần cải thiện phát hiện vùng mã. MQTT chưa cần trong phạm vi đã chốt.
