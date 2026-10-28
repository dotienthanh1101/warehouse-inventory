"""Tạo ngày kiểm kê và đọc danh sách tồn đầu ngày."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.app.database import get_connection


VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


def _read_day(connection, day_id):
    day = connection.execute("""
        SELECT
            day_id,
            business_date,
            status,
            opened_at,
            closed_at,
            source_day_id,
            note
        FROM inventory_days
        WHERE day_id = ?
    """, (day_id,)).fetchone()

    if day is None:
        raise ValueError("Không tìm thấy ngày kiểm kê.")

    opening_items = connection.execute("""
        SELECT
            marker_id,
            product_code,
            product_name,
            quantity
        FROM inventory_day_opening_items
        WHERE day_id = ?
        ORDER BY marker_id
    """, (day_id,)).fetchall()

    result = dict(day)
    result["opening_items"] = [
        dict(item) for item in opening_items
    ]
    result["opening_total_boxes"] = sum(
        item["quantity"] for item in opening_items
    )

    return result


def get_day(day_id):
    """Đọc thông tin ngày và tồn đầu ngày."""
    with get_connection() as connection:
        connection.execute("BEGIN")
        return _read_day(connection, day_id)


def get_open_day():
    """Trả về ngày đang mở, hoặc None nếu chưa có."""
    with get_connection() as connection:
        connection.execute("BEGIN")

        row = connection.execute("""
            SELECT day_id
            FROM inventory_days
            WHERE status = 'open'
        """).fetchone()

        if row is None:
            return None

        return _read_day(connection, row["day_id"])


def create_day(opening_counts=None):
    """
    Tạo ngày kiểm kê theo ngày hiện tại tại Việt Nam.

    Ngày đầu tiên:
        Phải truyền tồn đầu cho toàn bộ loại hàng trong danh mục.
        Ví dụ: {23: 10, 24: 0, 25: 10}.

    Các ngày sau:
        Không nhập lại tồn đầu.
        Lấy từ báo cáo ngày đã chốt gần nhất.

    Không sửa hoặc tự liên kết các phiên thử nghiệm cũ.
    """
    if opening_counts is not None:
        if not isinstance(opening_counts, dict):
            raise ValueError(
                "Tồn đầu phải là dictionary: {marker_id: số lượng}."
            )

        for marker_id, quantity in opening_counts.items():
            if type(marker_id) is not int:
                raise ValueError("Marker ID phải là số nguyên.")

            if not 0 <= marker_id <= 249:
                raise ValueError("Marker ID phải từ 0 đến 249.")

            if type(quantity) is not int or quantity < 0:
                raise ValueError(
                    f"Số lượng của ID {marker_id} "
                    "phải là số nguyên không âm."
                )

    with get_connection() as connection:
        # Khóa giao dịch ghi để hai yêu cầu không tạo ngày cùng lúc.
        connection.execute("BEGIN IMMEDIATE")

        now = datetime.now(timezone.utc)
        business_date = now.astimezone(
            VIETNAM_TIMEZONE
        ).date().isoformat()

        existing = connection.execute("""
            SELECT day_id, status
            FROM inventory_days
            WHERE business_date = ?
        """, (business_date,)).fetchone()

        if existing is not None:
            if existing["status"] == "closed":
                raise ValueError(
                    f"Ngày {business_date} đã chốt, không được mở lại."
                )

            raise ValueError(
                f"Ngày {business_date} đã được tạo. "
                "Hãy đọc ngày đang mở, không tạo thêm."
            )

        open_day = connection.execute("""
            SELECT business_date
            FROM inventory_days
            WHERE status = 'open'
        """).fetchone()

        if open_day is not None:
            raise ValueError(
                f"Ngày {open_day['business_date']} chưa chốt. "
                "Cần chốt ngày đó trước khi mở ngày mới."
            )

        products = connection.execute("""
            SELECT marker_id, product_code, product_name
            FROM packages
            ORDER BY marker_id
        """).fetchall()

        if not products:
            raise ValueError("Danh mục loại hàng đang trống.")

        previous_day = connection.execute("""
            SELECT day_id, business_date
            FROM inventory_days
            ORDER BY business_date DESC
            LIMIT 1
        """).fetchone()

        source_day_id = None

        if previous_day is None:
            # Ngày đầu tiên phải khai báo rõ cả những loại tồn bằng 0.
            if opening_counts is None:
                raise ValueError(
                    "Ngày đầu tiên cần khai báo danh sách tồn đầu."
                )

            catalog_ids = {
                product["marker_id"] for product in products
            }
            supplied_ids = set(opening_counts)

            unknown_ids = supplied_ids - catalog_ids
            missing_ids = catalog_ids - supplied_ids

            if unknown_ids:
                raise ValueError(
                    "ID chưa có trong danh mục: "
                    f"{sorted(unknown_ids)}."
                )

            if missing_ids:
                raise ValueError(
                    "Chưa khai báo tồn đầu cho ID: "
                    f"{sorted(missing_ids)}. "
                    "Nhập 0 nếu không có hàng."
                )

            quantities = dict(opening_counts)
            note = "Khởi tạo tồn đầu ngày từ danh sách người dùng."

        else:
            if previous_day["business_date"] >= business_date:
                raise ValueError(
                    "Ngày mới phải sau ngày kiểm kê gần nhất. "
                    "Hãy kiểm tra ngày giờ trên máy."
                )

            if opening_counts is not None:
                raise ValueError(
                    "Từ ngày thứ hai, tồn đầu lấy từ ngày đã chốt "
                    "gần nhất; không nhập lại thủ công."
                )

            source_day_id = previous_day["day_id"]

            closing_items = connection.execute("""
                SELECT marker_id, closing_quantity
                FROM inventory_day_closing_items
                WHERE day_id = ?
            """, (source_day_id,)).fetchall()

            if not closing_items:
                raise ValueError(
                    "Ngày trước chưa có dữ liệu báo cáo chốt ngày."
                )

            quantities = {
                item["marker_id"]: item["closing_quantity"]
                for item in closing_items
            }

            # Loại hàng được đăng ký mới sau ngày trước có tồn đầu 0.
            for product in products:
                quantities.setdefault(product["marker_id"], 0)

            note = (
                "Tồn đầu lấy từ ngày đã chốt "
                f"{previous_day['business_date']}."
            )

        day_id = uuid4().hex

        connection.execute("""
            INSERT INTO inventory_days (
                day_id,
                business_date,
                status,
                opened_at,
                closed_at,
                source_day_id,
                note
            )
            VALUES (?, ?, 'open', ?, NULL, ?, ?)
        """, (
            day_id,
            business_date,
            now.isoformat(),
            source_day_id,
            note,
        ))

        connection.executemany("""
            INSERT INTO inventory_day_opening_items (
                day_id,
                marker_id,
                product_code,
                product_name,
                quantity
            )
            VALUES (?, ?, ?, ?, ?)
        """, [
            (
                day_id,
                product["marker_id"],
                product["product_code"],
                product["product_name"],
                quantities[product["marker_id"]],
            )
            for product in products
        ])

        return _read_day(connection, day_id)