"""Chốt số lượng của một phiên kiểm kê theo ngày."""

from datetime import datetime, timezone

from backend.app.database import get_connection
from backend.app.daily_reconciliation_service import (
    get_session_reconciliation,
)


def finalize_session(
    session_id,
    *,
    confirm_full_coverage,
    expected_receipt_count,
    confirm_empty=False,
):
    """
    Chốt phiên sau khi người giám sát xác nhận kiểm kê đầy đủ.

    expected_receipt_count:
        Số bản nháp đã ghi nhận tại thời điểm xem bảng xác nhận.
        Nếu số này thay đổi, yêu cầu tải lại trước khi chốt.

    confirm_empty:
        Phải xác nhận riêng nếu chốt toàn bộ kho bằng 0 thùng.

    Không tự nhận diện trùng thùng giữa các ảnh.
    """
    if confirm_full_coverage is not True:
        raise ValueError(
            "Chưa xác nhận đã kiểm kê đầy đủ phạm vi kho."
        )

    if (
        type(expected_receipt_count) is not int
        or expected_receipt_count < 0
    ):
        raise ValueError(
            "Số bản nháp dự kiến phải là số nguyên không âm."
        )

    if type(confirm_empty) is not bool:
        raise ValueError("Xác nhận kho rỗng phải là true hoặc false.")

    already_finalized = False

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        session = connection.execute("""
            SELECT
                s.session_id,
                s.status,
                ds.day_id,
                ds.session_order,
                ds.finalized_at,
                d.status AS day_status
            FROM inventory_sessions AS s
            JOIN inventory_day_sessions AS ds
                ON ds.session_id = s.session_id
            JOIN inventory_days AS d
                ON d.day_id = ds.day_id
            WHERE s.session_id = ?
        """, (session_id,)).fetchone()

        if session is None:
            raise ValueError(
                "Không tìm thấy phiên thuộc ngày kiểm kê."
            )

        if session["finalized_at"] is not None:
            if session["status"] != "closed":
                raise ValueError("Trạng thái chốt phiên không đồng bộ.")

            # Gửi lại yêu cầu: đọc kết quả cũ, không chốt lần nữa.
            already_finalized = True

        else:
            if session["status"] != "open":
                raise ValueError(
                    "Phiên đã đóng nhưng chưa có dữ liệu chốt theo ngày."
                )

            if session["day_status"] != "open":
                raise ValueError(
                    "Ngày đã chốt, không được thay đổi số lượng."
                )

            legacy_count = connection.execute("""
                SELECT COUNT(*)
                FROM inventory_items
                WHERE session_id = ?
            """, (session_id,)).fetchone()[0]

            legacy_expected_count = connection.execute("""
                SELECT COUNT(*)
                FROM inventory_expected_items
                WHERE session_id = ?
            """, (session_id,)).fetchone()[0]

            if legacy_count or legacy_expected_count:
                raise ValueError(
                    "Phiên chứa dữ liệu theo cơ chế cũ, không thể chốt."
                )

            receipt_count = connection.execute("""
                SELECT COUNT(*)
                FROM inventory_review_receipts
                WHERE session_id = ?
            """, (session_id,)).fetchone()[0]

            if receipt_count != expected_receipt_count:
                raise ValueError(
                    "Dữ liệu phiên đã thay đổi sau khi xem tổng hợp. "
                    "Hãy tải lại và kiểm tra trước khi chốt."
                )

            # Đọc mốc so sánh.
            if session["session_order"] == 1:
                reference_rows = connection.execute("""
                    SELECT
                        marker_id,
                        product_code,
                        product_name,
                        quantity
                    FROM inventory_day_opening_items
                    WHERE day_id = ?
                """, (session["day_id"],)).fetchall()

            else:
                previous = connection.execute("""
                    SELECT
                        ds.session_id,
                        ds.finalized_at,
                        s.status
                    FROM inventory_day_sessions AS ds
                    JOIN inventory_sessions AS s
                        ON s.session_id = ds.session_id
                    WHERE ds.day_id = ?
                      AND ds.session_order = ?
                """, (
                    session["day_id"],
                    session["session_order"] - 1,
                )).fetchone()

                if (
                    previous is None
                    or previous["finalized_at"] is None
                    or previous["status"] != "closed"
                ):
                    raise ValueError("Phiên liền trước chưa chốt đầy đủ.")

                reference_rows = connection.execute("""
                    SELECT
                        marker_id,
                        product_code,
                        product_name,
                        quantity
                    FROM inventory_session_stock
                    WHERE session_id = ?
                """, (previous["session_id"],)).fetchall()

            if not reference_rows:
                raise ValueError("Thiếu danh sách tồn làm mốc so sánh.")

            reference = {
                row["marker_id"]: dict(row)
                for row in reference_rows
            }

            quantities = {
                row["marker_id"]: row["quantity"]
                for row in connection.execute("""
                    SELECT b.marker_id, COUNT(*) AS quantity
                    FROM inventory_box_instances AS b
                    JOIN inventory_review_receipts AS r
                        ON r.review_id = b.review_id
                    WHERE r.session_id = ?
                    GROUP BY b.marker_id
                """, (session_id,)).fetchall()
            }

            total_boxes = sum(quantities.values())

            declared_boxes = connection.execute("""
                SELECT COALESCE(SUM(box_count), 0)
                FROM inventory_review_receipts
                WHERE session_id = ?
            """, (session_id,)).fetchone()[0]

            if declared_boxes != total_boxes:
                raise ValueError(
                    "Số thùng trong biên nhận không khớp dữ liệu chi tiết."
                )

            if total_boxes == 0 and not confirm_empty:
                raise ValueError(
                    "Phiên chưa ghi nhận thùng nào. "
                    "Muốn chốt tồn bằng 0 phải xác nhận riêng kho rỗng."
                )

            existing_stock = connection.execute("""
                SELECT COUNT(*)
                FROM inventory_session_stock
                WHERE session_id = ?
            """, (session_id,)).fetchone()[0]

            if existing_stock:
                raise ValueError(
                    "Phiên chưa chốt nhưng đã có dữ liệu tồn cố định. "
                    "Cần kiểm tra dữ liệu trước khi tiếp tục."
                )

            catalog = {
                row["marker_id"]: dict(row)
                for row in connection.execute("""
                    SELECT marker_id, product_code, product_name
                    FROM packages
                """).fetchall()
            }

            if set(quantities) - set(catalog):
                raise ValueError(
                    "Có loại hàng đã ghi nhận nhưng không có trong danh mục."
                )

            stock_rows = []

            # Giữ cả loại hàng ở mốc trước nhưng hiện không còn.
            # Danh mục mới chưa ghi nhận được lưu số lượng 0.
            marker_ids = set(reference) | set(catalog) | set(quantities)

            for marker_id in sorted(marker_ids):
                product = catalog.get(marker_id) or reference[marker_id]

                stock_rows.append((
                    session_id,
                    marker_id,
                    product["product_code"],
                    product["product_name"],
                    quantities.get(marker_id, 0),
                ))

            connection.executemany("""
                INSERT INTO inventory_session_stock (
                    session_id,
                    marker_id,
                    product_code,
                    product_name,
                    quantity
                )
                VALUES (?, ?, ?, ?, ?)
            """, stock_rows)

            finalized_at = datetime.now(timezone.utc).isoformat()

            connection.execute("""
                UPDATE inventory_sessions
                SET status = 'closed',
                    ended_at = ?
                WHERE session_id = ?
            """, (finalized_at, session_id))

            connection.execute("""
                UPDATE inventory_day_sessions
                SET finalized_at = ?
                WHERE session_id = ?
            """, (finalized_at, session_id))

            # Số tồn và trạng thái đóng cùng được commit.
            # Nếu lỗi, toàn bộ giao dịch được rollback.

    result = get_session_reconciliation(session_id)
    result["already_finalized"] = already_finalized
    return result