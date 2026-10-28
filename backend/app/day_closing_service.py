"""Tổng hợp và chốt ngày kiểm kê trong một giao dịch."""

from datetime import datetime, timezone

from backend.app.database import get_connection


def _build_report(connection, day_id):
    day = connection.execute("""
        SELECT *
        FROM inventory_days
        WHERE day_id = ?
    """, (day_id,)).fetchone()

    if day is None:
        raise ValueError("Không tìm thấy ngày kiểm kê.")

    sessions = connection.execute("""
        SELECT
            ds.session_id,
            ds.session_order,
            ds.finalized_at,
            s.status
        FROM inventory_day_sessions AS ds
        JOIN inventory_sessions AS s
            ON s.session_id = ds.session_id
        WHERE ds.day_id = ?
        ORDER BY ds.session_order
    """, (day_id,)).fetchall()

    if day["status"] == "closed":
        # Ngày đã chốt: đọc báo cáo cố định đã lưu.
        rows = connection.execute("""
            SELECT
                marker_id,
                product_code,
                product_name,
                opening_quantity,
                total_increase,
                total_decrease,
                closing_quantity
            FROM inventory_day_closing_items
            WHERE day_id = ?
            ORDER BY marker_id
        """, (day_id,)).fetchall()

        if not rows:
            raise ValueError(
                "Ngày đã chốt nhưng thiếu báo cáo đã lưu."
            )

        items = [dict(row) for row in rows]

    else:
        if not sessions:
            raise ValueError(
                "Cần ít nhất một phiên đã chốt để tổng hợp ngày."
            )

        if any(
            session["status"] != "closed"
            or session["finalized_at"] is None
            for session in sessions
        ):
            raise ValueError(
                "Còn phiên chưa chốt. Hãy hoàn tất phiên trước."
            )

        orders = [
            session["session_order"] for session in sessions
        ]

        if orders != list(range(1, len(sessions) + 1)):
            raise ValueError(
                "Thứ tự phiên không liên tục, cần kiểm tra dữ liệu."
            )

        opening = connection.execute("""
            SELECT
                marker_id,
                product_code,
                product_name,
                quantity
            FROM inventory_day_opening_items
            WHERE day_id = ?
        """, (day_id,)).fetchall()

        if not opening:
            raise ValueError("Thiếu danh sách tồn đầu ngày.")

        summary = {
            row["marker_id"]: {
                "marker_id": row["marker_id"],
                "product_code": row["product_code"],
                "product_name": row["product_name"],
                "opening_quantity": row["quantity"],
                "total_increase": 0,
                "total_decrease": 0,
                "closing_quantity": row["quantity"],
            }
            for row in opening
        }

        previous = {
            row["marker_id"]: row["quantity"]
            for row in opening
        }

        # Lần lượt so sánh:
        # tồn đầu -> phiên 1 -> phiên 2 -> ... -> phiên cuối.
        for session in sessions:
            stock = connection.execute("""
                SELECT
                    marker_id,
                    product_code,
                    product_name,
                    quantity
                FROM inventory_session_stock
                WHERE session_id = ?
            """, (session["session_id"],)).fetchall()

            current = {
                row["marker_id"]: row["quantity"]
                for row in stock
            }

            if not stock or set(previous) - set(current):
                raise ValueError(
                    "Một phiên đã chốt thiếu số liệu tồn. "
                    "Không tự coi dữ liệu thiếu là số lượng 0."
                )

            for row in stock:
                marker_id = row["marker_id"]

                if marker_id not in summary:
                    summary[marker_id] = {
                        "marker_id": marker_id,
                        "product_code": row["product_code"],
                        "product_name": row["product_name"],
                        "opening_quantity": 0,
                        "total_increase": 0,
                        "total_decrease": 0,
                        "closing_quantity": 0,
                    }

                item = summary[marker_id]
                difference = (
                    row["quantity"] - previous.get(marker_id, 0)
                )

                item["total_increase"] += max(difference, 0)
                item["total_decrease"] += max(-difference, 0)
                item["closing_quantity"] = row["quantity"]
                item["product_code"] = row["product_code"]
                item["product_name"] = row["product_name"]

            previous = current

        items = [
            summary[marker_id]
            for marker_id in sorted(summary)
        ]

        for item in items:
            expected_closing = (
                item["opening_quantity"]
                + item["total_increase"]
                - item["total_decrease"]
            )

            if item["closing_quantity"] != expected_closing:
                raise ValueError(
                    "Tổng hợp tăng/giảm không khớp tồn cuối."
                )

    return {
        "day_id": day_id,
        "business_date": day["business_date"],
        "status": day["status"],
        "closed_at": day["closed_at"],
        "session_count": len(sessions),
        "opening_total_boxes": sum(
            item["opening_quantity"] for item in items
        ),
        "total_increase": sum(
            item["total_increase"] for item in items
        ),
        "total_decrease": sum(
            item["total_decrease"] for item in items
        ),
        "closing_total_boxes": sum(
            item["closing_quantity"] for item in items
        ),
        "items": items,
        "message": (
            "Báo cáo đã chốt và lưu cố định."
            if day["status"] == "closed"
            else "Bản xem trước từ các phiên đã chốt. "
                 "Ngày vẫn đang mở."
        ),
        "note": (
            "Tăng/giảm qua kiểm kê, "
            "không phải tổng nhập/xuất thực tế."
        ),
    }


def get_day_report(day_id):
    """Xem báo cáo, không khóa ngày và không thay đổi dữ liệu."""
    with get_connection() as connection:
        connection.execute("BEGIN")
        return _build_report(connection, day_id)


def finalize_day(
    day_id,
    *,
    confirm_close,
    expected_session_count,
):
    """Lưu báo cáo và khóa ngày trong cùng một giao dịch."""
    if confirm_close is not True:
        raise ValueError("Chưa xác nhận chốt cuối ngày.")

    if (
        type(expected_session_count) is not int
        or expected_session_count < 1
    ):
        raise ValueError(
            "Số phiên dự kiến phải là số nguyên lớn hơn 0."
        )

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        report = _build_report(connection, day_id)

        # Gửi lại yêu cầu chỉ trả báo cáo cũ.
        if report["status"] == "closed":
            report["already_closed"] = True
            return report

        if report["session_count"] != expected_session_count:
            raise ValueError(
                "Danh sách phiên đã thay đổi. "
                "Hãy tải lại báo cáo trước khi chốt."
            )

        existing = connection.execute("""
            SELECT COUNT(*)
            FROM inventory_day_closing_items
            WHERE day_id = ?
        """, (day_id,)).fetchone()[0]

        if existing:
            raise ValueError(
                "Ngày đang mở nhưng đã có báo cáo cố định, "
                "cần kiểm tra."
            )

        connection.executemany("""
            INSERT INTO inventory_day_closing_items (
                day_id,
                marker_id,
                product_code,
                product_name,
                opening_quantity,
                total_increase,
                total_decrease,
                closing_quantity
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                day_id,
                item["marker_id"],
                item["product_code"],
                item["product_name"],
                item["opening_quantity"],
                item["total_increase"],
                item["total_decrease"],
                item["closing_quantity"],
            )
            for item in report["items"]
        ])

        closed_at = datetime.now(timezone.utc).isoformat()

        connection.execute("""
            UPDATE inventory_days
            SET status = 'closed',
                closed_at = ?
            WHERE day_id = ?
        """, (closed_at, day_id))

        report.update({
            "status": "closed",
            "closed_at": closed_at,
            "already_closed": False,
            "message": (
                "Đã chốt cuối ngày. Không được tạo thêm phiên "
                "hoặc ghi thêm thùng."
            ),
        })

        return report