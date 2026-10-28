"""Tạo phiên kiểm kê thuộc một ngày đã mở."""

from datetime import datetime, timezone
from uuid import uuid4

from backend.app.database import get_connection
from backend.app.daily_inventory_service import VIETNAM_TIMEZONE


def create_daily_session(day_id, name):
    """
    Tạo phiên kiểm kê và liên kết với ngày trong cùng giao dịch.

    Phiên đầu: so sánh với tồn đầu ngày.
    Phiên sau: so sánh với số tồn của phiên đã chốt liền trước.

    Không sử dụng lại các phiên thử nghiệm chưa liên kết.
    """
    if not isinstance(day_id, str) or not day_id.strip():
        raise ValueError("Thiếu ID ngày kiểm kê.")

    if not isinstance(name, str):
        raise ValueError("Tên phiên phải là chuỗi.")

    name = name.strip()

    if not 1 <= len(name) <= 120:
        raise ValueError("Tên phiên phải từ 1 đến 120 ký tự.")

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        day = connection.execute("""
            SELECT day_id, business_date, status
            FROM inventory_days
            WHERE day_id = ?
        """, (day_id,)).fetchone()

        if day is None:
            raise ValueError("Không tìm thấy ngày kiểm kê.")

        if day["status"] != "open":
            raise ValueError(
                "Ngày đã chốt. Không thể tạo thêm phiên."
            )

        now = datetime.now(timezone.utc)
        today = now.astimezone(
            VIETNAM_TIMEZONE
        ).date().isoformat()

        if day["business_date"] != today:
            raise ValueError(
                f"Ngày đang chọn là {day['business_date']}, "
                f"nhưng hôm nay là {today}. "
                "Cần xử lý chốt ngày cũ trước khi tạo phiên ngày mới."
            )

        pending = connection.execute("""
            SELECT session_id
            FROM inventory_day_sessions
            WHERE day_id = ?
              AND finalized_at IS NULL
        """, (day_id,)).fetchone()

        if pending is not None:
            raise ValueError(
                "Ngày này đang có phiên chưa chốt: "
                f"{pending['session_id']}. "
                "Không tạo thêm phiên song song."
            )

        previous = connection.execute("""
            SELECT
                ds.session_id,
                ds.session_order,
                ds.finalized_at,
                s.status
            FROM inventory_day_sessions AS ds
            JOIN inventory_sessions AS s
                ON s.session_id = ds.session_id
            WHERE ds.day_id = ?
            ORDER BY ds.session_order DESC
            LIMIT 1
        """, (day_id,)).fetchone()

        if previous is None:
            session_order = 1
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
            """, (day_id,)).fetchall()

        else:
            if (
                previous["finalized_at"] is None
                or previous["status"] != "closed"
            ):
                raise ValueError(
                    "Phiên trước chưa được chốt đầy đủ."
                )

            session_order = previous["session_order"] + 1
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
            raise ValueError(
                "Chưa có danh sách tồn làm mốc so sánh. "
                "Không tạo phiên khi thiếu dữ liệu này."
            )

        session_id = uuid4().hex
        started_at = now.isoformat()

        connection.execute("""
            INSERT INTO inventory_sessions (
                session_id,
                name,
                status,
                started_at,
                ended_at,
                expected_configured
            )
            VALUES (?, ?, 'open', ?, NULL, 0)
        """, (
            session_id,
            name,
            started_at,
        ))

        connection.execute("""
            INSERT INTO inventory_day_sessions (
                session_id,
                day_id,
                session_order,
                finalized_at
            )
            VALUES (?, ?, ?, NULL)
        """, (
            session_id,
            day_id,
            session_order,
        ))

        return {
            "session_id": session_id,
            "name": name,
            "status": "open",
            "day_id": day_id,
            "business_date": day["business_date"],
            "session_order": session_order,
            "started_at": started_at,
            "reference_type": reference_type,
            "reference_session_id": reference_session_id,
            "reference_items": [
                dict(row) for row in reference_rows
            ],
            "reference_total_boxes": sum(
                row["quantity"] for row in reference_rows
            ),
            "recorded_total_boxes": 0,
            "message": (
                "Đã tạo phiên. Chưa ghi nhận thùng; "
                "chưa kết luận tăng hoặc giảm."
            ),
        }