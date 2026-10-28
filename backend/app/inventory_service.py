"""Quản lý phiên, chống ghi nhận trùng và đối soát kiện hàng."""

from datetime import datetime, timezone
from uuid import uuid4

from backend.app.database import get_connection


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_expected_ids(expected_ids):
    """
    None: chưa thiết lập danh sách dự kiến.
    []: đã thiết lập và dự kiến không có kiện nào.
    """
    if expected_ids is None:
        return None

    if not isinstance(expected_ids, list):
        raise ValueError("Danh sách dự kiến phải là một danh sách ID.")

    for marker_id in expected_ids:
        if (
            type(marker_id) is not int
            or marker_id < 0
            or marker_id > 249
        ):
            raise ValueError(
                "ID ArUco phải là số nguyên từ 0 đến 249."
            )

    return sorted(set(expected_ids))


def create_session(name, expected_ids=None):
    """Tạo phiên và lưu danh sách dự kiến trong cùng giao dịch."""
    name = name.strip()
    if not name:
        raise ValueError("Tên phiên kiểm kê không được để trống.")

    if len(name) > 120:
        raise ValueError("Tên phiên không được dài quá 120 ký tự.")

    expected_ids = validate_expected_ids(expected_ids)
    session_id = uuid4().hex
    started_at = utc_now()

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        if expected_ids is not None:
            registered_ids = {
                row["marker_id"]
                for row in connection.execute(
                    "SELECT marker_id FROM packages"
                ).fetchall()
            }

            unknown_ids = sorted(set(expected_ids) - registered_ids)
            if unknown_ids:
                raise ValueError(
                    "Các ID chưa đăng ký trong danh mục: "
                    + ", ".join(map(str, unknown_ids))
                )

        connection.execute("""
            INSERT INTO inventory_sessions (
                session_id,
                name,
                status,
                started_at,
                expected_configured
            )
            VALUES (?, ?, 'open', ?, ?)
        """, (
            session_id,
            name,
            started_at,
            int(expected_ids is not None),
        ))

        if expected_ids is not None:
            connection.executemany("""
                INSERT INTO inventory_expected_items (
                    session_id, marker_id
                )
                VALUES (?, ?)
            """, [
                (session_id, marker_id)
                for marker_id in expected_ids
            ])

    return get_session(session_id)


def build_reconciliation(session, items, expected_items):
    """So sánh danh sách dự kiến với các kiện đã ghi nhận."""
    if not session["expected_configured"]:
        return {
            "configured": False,
            "outcome": "not_configured",
            "message": "Chưa thiết lập danh sách kiện dự kiến.",
            "expected_count": None,
            "recorded_count": len(items),
            "matched_count": None,
            "not_seen_count": None,
            "unexpected_count": None,
            "matched_items": [],
            "not_seen_items": [],
            "unexpected_items": [],
        }

    recorded_by_id = {
        item["marker_id"]: item
        for item in items
    }
    expected_by_id = {
        item["marker_id"]: item
        for item in expected_items
    }

    recorded_ids = set(recorded_by_id)
    expected_ids = set(expected_by_id)

    matched_ids = sorted(recorded_ids & expected_ids)
    not_seen_ids = sorted(expected_ids - recorded_ids)
    unexpected_ids = sorted(recorded_ids - expected_ids)

    matches = not not_seen_ids and not unexpected_ids

    return {
        "configured": True,
        "outcome": "matched" if matches else "differences",
        "message": (
            "Danh sách đã ghi nhận khớp danh sách dự kiến."
            if matches
            else (
                f"Chưa ghi nhận {len(not_seen_ids)} kiện dự kiến; "
                f"có {len(unexpected_ids)} kiện ngoài dự kiến."
            )
        ),
        "expected_count": len(expected_ids),
        "recorded_count": len(recorded_ids),
        "matched_count": len(matched_ids),
        "not_seen_count": len(not_seen_ids),
        "unexpected_count": len(unexpected_ids),
        "matched_items": [
            recorded_by_id[marker_id]
            for marker_id in matched_ids
        ],
        "not_seen_items": [
            expected_by_id[marker_id]
            for marker_id in not_seen_ids
        ],
        "unexpected_items": [
            recorded_by_id[marker_id]
            for marker_id in unexpected_ids
        ],
    }


def get_session(session_id):
    """Đọc thông tin phiên và đối soát từ cùng một ảnh chụp dữ liệu."""
    with get_connection() as connection:
        connection.execute("BEGIN")

        row = connection.execute("""
            SELECT *
            FROM inventory_sessions
            WHERE session_id = ?
        """, (session_id,)).fetchone()

        if row is None:
            raise ValueError("Không tìm thấy phiên kiểm kê.")

        session = dict(row)
        session["expected_configured"] = bool(
            session["expected_configured"]
        )

        rows = connection.execute("""
            SELECT
                i.marker_id,
                p.package_code,
                p.product_code,
                p.product_name,
                i.first_seen_at,
                i.last_seen_at,
                i.last_capture_id,
                i.evidence_image_url
            FROM inventory_items AS i
            JOIN packages AS p ON p.marker_id = i.marker_id
            WHERE i.session_id = ?
            ORDER BY i.marker_id
        """, (session_id,)).fetchall()

        items = [dict(item) for item in rows]

        rows = connection.execute("""
            SELECT
                e.marker_id,
                p.package_code,
                p.product_code,
                p.product_name
            FROM inventory_expected_items AS e
            JOIN packages AS p ON p.marker_id = e.marker_id
            WHERE e.session_id = ?
            ORDER BY e.marker_id
        """, (session_id,)).fetchall()

        expected_items = [dict(item) for item in rows]

    return {
        **session,
        "item_count": len(items),
        "items": items,
        "expected_items": expected_items,
        "reconciliation": build_reconciliation(
            session, items, expected_items
        ),
    }


def record_scan(session_id, capture_id, scan):
    """Ghi ID đã xác nhận, đã đăng ký; mỗi kiện một lần trong phiên."""
    now = utc_now()
    new_ids = []
    repeated_ids = []
    unregistered_ids = []

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        session = connection.execute("""
            SELECT status
            FROM inventory_sessions
            WHERE session_id = ?
        """, (session_id,)).fetchone()

        if session is None:
            raise ValueError("Không tìm thấy phiên kiểm kê.")

        if session["status"] != "open":
            raise ValueError("Phiên đã kết thúc, không thể ghi thêm kiện.")

        processed_ids = set()

        for item in scan.get("confirmed", []):
            marker_id = int(item["id"])

            if marker_id in processed_ids:
                continue
            processed_ids.add(marker_id)

            package = connection.execute("""
                SELECT marker_id
                FROM packages
                WHERE marker_id = ?
            """, (marker_id,)).fetchone()

            if package is None:
                unregistered_ids.append(marker_id)
                continue

            evidence_url = item.get("evidence_image_url")
            if not evidence_url:
                raise ValueError(
                    f"ID {marker_id} chưa có ảnh minh chứng."
                )

            existing = connection.execute("""
                SELECT marker_id
                FROM inventory_items
                WHERE session_id = ? AND marker_id = ?
            """, (session_id, marker_id)).fetchone()

            if existing is None:
                connection.execute("""
                    INSERT INTO inventory_items (
                        session_id,
                        marker_id,
                        first_seen_at,
                        last_seen_at,
                        last_capture_id,
                        evidence_image_url
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    marker_id,
                    now,
                    now,
                    capture_id,
                    evidence_url,
                ))
                new_ids.append(marker_id)

            else:
                connection.execute("""
                    UPDATE inventory_items
                    SET last_seen_at = ?,
                        last_capture_id = ?,
                        evidence_image_url = ?
                    WHERE session_id = ? AND marker_id = ?
                """, (
                    now,
                    capture_id,
                    evidence_url,
                    session_id,
                    marker_id,
                ))
                repeated_ids.append(marker_id)

        total = connection.execute("""
            SELECT COUNT(*)
            FROM inventory_items
            WHERE session_id = ?
        """, (session_id,)).fetchone()[0]

    return {
        "session_id": session_id,
        "new_ids": new_ids,
        "repeated_ids": repeated_ids,
        "unregistered_ids": unregistered_ids,
        "total_packages": total,
    }


def close_session(session_id):
    """Kết thúc phiên và giữ nguyên dữ liệu."""
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        session = connection.execute("""
            SELECT status
            FROM inventory_sessions
            WHERE session_id = ?
        """, (session_id,)).fetchone()

        if session is None:
            raise ValueError("Không tìm thấy phiên kiểm kê.")

        if session["status"] == "open":
            connection.execute("""
                UPDATE inventory_sessions
                SET status = 'closed', ended_at = ?
                WHERE session_id = ?
            """, (utc_now(), session_id))

    return get_session(session_id)