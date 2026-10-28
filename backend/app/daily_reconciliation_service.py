"""Đọc bảng đối soát theo số lượng thùng của phiên kiểm kê."""

from backend.app.database import get_connection


def get_session_reconciliation(session_id):
    """Chỉ đọc dữ liệu, không chốt phiên và không sửa số lượng."""
    with get_connection() as connection:
        connection.execute("BEGIN")

        session = connection.execute("""
            SELECT
                s.session_id,
                s.name,
                s.status,
                ds.day_id,
                ds.session_order,
                ds.finalized_at,
                d.business_date,
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

        finalized = session["finalized_at"] is not None

        if finalized != (session["status"] == "closed"):
            raise ValueError(
                "Trạng thái phiên chưa đồng bộ với dữ liệu chốt. "
                "Không sử dụng chức năng đóng phiên cũ."
            )

        # Xác định mốc so sánh.
        if session["session_order"] == 1:
            reference_type = "opening_stock"
            reference_session_id = None

            reference_rows = connection.execute("""
                SELECT
                    marker_id,
                    product_code,
                    product_name,
                    quantity
                FROM inventory_day_opening_items
                WHERE day_id = ?
                ORDER BY marker_id
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
                raise ValueError(
                    "Chưa có dữ liệu đã chốt của phiên liền trước."
                )

            reference_type = "previous_session"
            reference_session_id = previous["session_id"]

            reference_rows = connection.execute("""
                SELECT
                    marker_id,
                    product_code,
                    product_name,
                    quantity
                FROM inventory_session_stock
                WHERE session_id = ?
                ORDER BY marker_id
            """, (reference_session_id,)).fetchall()

        if not reference_rows:
            raise ValueError("Thiếu danh sách tồn làm mốc so sánh.")

        reference = {
            row["marker_id"]: dict(row)
            for row in reference_rows
        }

        if finalized:
            # Phiên đã chốt: đọc bản số liệu cố định đã lưu.
            current_rows = connection.execute("""
                SELECT
                    marker_id,
                    product_code,
                    product_name,
                    quantity
                FROM inventory_session_stock
                WHERE session_id = ?
                ORDER BY marker_id
            """, (session_id,)).fetchall()

            if not current_rows:
                raise ValueError(
                    "Phiên đã đóng nhưng thiếu số liệu tồn đã chốt."
                )

            current = {
                row["marker_id"]: dict(row)
                for row in current_rows
            }

            if set(reference) - set(current):
                raise ValueError(
                    "Số liệu chốt thiếu loại hàng thuộc mốc so sánh."
                )

        else:
            # Phiên đang mở: chỉ đếm thùng đã ghi nhận.
            quantity_rows = connection.execute("""
                SELECT
                    b.marker_id,
                    COUNT(*) AS quantity
                FROM inventory_box_instances AS b
                JOIN inventory_review_receipts AS r
                    ON r.review_id = b.review_id
                WHERE r.session_id = ?
                GROUP BY b.marker_id
                ORDER BY b.marker_id
            """, (session_id,)).fetchall()

            catalog = {
                row["marker_id"]: dict(row)
                for row in connection.execute("""
                    SELECT marker_id, product_code, product_name
                    FROM packages
                """).fetchall()
            }

            current = {}

            for row in quantity_rows:
                marker_id = row["marker_id"]

                if marker_id not in catalog:
                    raise ValueError(
                        f"ID {marker_id} không còn trong danh mục."
                    )

                current[marker_id] = {
                    **catalog[marker_id],
                    "quantity": row["quantity"],
                }

        receipt_count = connection.execute("""
            SELECT COUNT(*)
            FROM inventory_review_receipts
            WHERE session_id = ?
        """, (session_id,)).fetchone()[0]

        items = []

        # Bao gồm cả loại mới xuất hiện và loại không còn thấy.
        for marker_id in sorted(set(reference) | set(current)):
            before = reference.get(marker_id)
            after = current.get(marker_id)

            reference_quantity = before["quantity"] if before else 0
            recorded_quantity = after["quantity"] if after else 0
            product = after if after else before

            if finalized:
                difference = recorded_quantity - reference_quantity
                increase = max(difference, 0)
                decrease = max(-difference, 0)
                state = "finalized"
            else:
                # Không suy diễn chưa chụp được thành hàng đã giảm.
                difference = None
                increase = None
                decrease = None
                state = (
                    "recorded_so_far"
                    if recorded_quantity > 0
                    else "not_recorded_yet"
                )

            items.append({
                "marker_id": marker_id,
                "product_code": product["product_code"],
                "product_name": product["product_name"],
                "reference_quantity": reference_quantity,
                "recorded_quantity": recorded_quantity,
                "difference": difference,
                "increase": increase,
                "decrease": decrease,
                "state": state,
            })

        return {
            "session_id": session["session_id"],
            "name": session["name"],
            "status": session["status"],
            "day_id": session["day_id"],
            "business_date": session["business_date"],
            "day_status": session["day_status"],
            "session_order": session["session_order"],
            "finalized": finalized,
            "finalized_at": session["finalized_at"],
            "reference_type": reference_type,
            "reference_session_id": reference_session_id,
            "receipt_count": receipt_count,
            "reference_total_boxes": sum(
                item["reference_quantity"] for item in items
            ),
            "recorded_total_boxes": sum(
                item["recorded_quantity"] for item in items
            ),
            "total_increase": (
                sum(item["increase"] for item in items)
                if finalized else None
            ),
            "total_decrease": (
                sum(item["decrease"] for item in items)
                if finalized else None
            ),
            "items": items,
            "cross_image_deduplication": False,
            "message": (
                "Số liệu đã chốt; tăng/giảm so với mốc trước."
                if finalized
                else "Phiên đang mở. Số đã ghi nhận chưa phải tồn cuối; "
                     "chưa kết luận tăng hoặc giảm."
            ),
        }