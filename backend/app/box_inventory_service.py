"""Ghi nhận từng thùng và kiểm tra trạng thái ngày kiểm kê.

SQLite là nguồn xác định bản nháp đã ghi nhận hay chưa.
Module này không tự sửa file JSON bản nháp.
"""

import json
import math
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from backend.app.database import get_connection


def validate_review(document):
    """Kiểm tra dữ liệu đã duyệt; không ghi cơ sở dữ liệu."""
    review_id = document.get("review_id")
    revision = document.get("revision")

    if not isinstance(review_id, str) or not review_id:
        raise ValueError("Bản nháp thiếu review_id.")

    if type(revision) is not int or revision < 1:
        raise ValueError("Phiên bản bản nháp không hợp lệ.")

    human = document.get("human_review")
    if not isinstance(human, dict):
        raise ValueError("Chưa lưu hiệu chỉnh về backend.")

    if human.get("revision") != revision:
        raise ValueError("Hiệu chỉnh không khớp phiên bản bản nháp.")

    width = document.get("width")
    height = document.get("height")

    if (
        type(width) is not int or width <= 0
        or type(height) is not int or height <= 0
    ):
        raise ValueError("Kích thước ảnh không hợp lệ.")

    image_url = document.get("image_url")
    if not isinstance(image_url, str) or not image_url:
        raise ValueError("Bản nháp thiếu ảnh minh chứng.")

    boxes = human.get("boxes")
    if not isinstance(boxes, list):
        raise ValueError("Danh sách thùng không hợp lệ.")

    # Phiên không còn hàng sẽ được xác nhận ở bước chốt phiên,
    # không tạo một biên nhận ảnh rỗng tại đây.
    if not boxes:
        raise ValueError("Bản nháp chưa có thùng để ghi nhận.")

    if len(boxes) > 500:
        raise ValueError("Một bản nháp không được vượt quá 500 thùng.")

    box_by_id = {}
    counts = Counter()

    for box in boxes:
        if not isinstance(box, dict):
            raise ValueError("Thông tin thùng không hợp lệ.")

        box_id = box.get("box_id")
        if (
            not isinstance(box_id, str)
            or not box_id
            or len(box_id) > 100
            or box_id in box_by_id
        ):
            raise ValueError("Mã vùng thùng bị thiếu hoặc trùng.")

        if box.get("reviewed") is not True:
            raise ValueError(
                f"{box_id} chưa được đánh dấu đã kiểm tra."
            )

        marker_id = box.get("marker_id")
        if type(marker_id) is not int or not 0 <= marker_id <= 249:
            raise ValueError(
                f"{box_id} chưa có ID loại hàng hợp lệ."
            )

        bounds = box.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError(f"{box_id} thiếu tọa độ.")

        if not all(
            type(value) in (int, float) and math.isfinite(value)
            for value in bounds
        ):
            raise ValueError(
                f"{box_id} có tọa độ không hợp lệ."
            )

        x1, y1, x2, y2 = bounds
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError(
                f"{box_id} có tọa độ vượt ảnh hoặc đảo chiều."
            )

        note = box.get("note", "")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError(
                f"{box_id} có ghi chú không hợp lệ."
            )

        box_by_id[box_id] = box
        counts[marker_id] += 1

    markers = {
        item["detection_id"]: item
        for item in document["analysis"]["markers"]
    }
    assignments = human.get("assignments")

    if (
        not isinstance(assignments, dict)
        or set(assignments) != set(markers)
    ):
        raise ValueError(
            "Danh sách gán marker không khớp ảnh gốc."
        )

    for detection_id, owner in assignments.items():
        # Người giám sát có thể chủ động bỏ gán marker đọc sai.
        if owner == "":
            continue

        if not isinstance(owner, str) or owner not in box_by_id:
            raise ValueError(
                "Marker được gán vào vùng không tồn tại."
            )

        if (
            markers[detection_id]["marker_id"]
            != box_by_id[owner]["marker_id"]
        ):
            raise ValueError(
                f"ID marker {detection_id} khác ID loại hàng của thùng."
            )

    return {
        "box_count": len(boxes),
        "counts_by_marker_id": dict(sorted(counts.items())),
    }


def _receipt_result(connection, receipt, already_recorded):
    rows = connection.execute("""
        SELECT marker_id, COUNT(*) AS quantity
        FROM inventory_box_instances
        WHERE review_id = ?
        GROUP BY marker_id
        ORDER BY marker_id
    """, (receipt["review_id"],)).fetchall()

    try:
        snapshot = json.loads(receipt['reviewed_payload_json'])
        resolution = snapshot.get('duplicate_resolution') or {}
        duplicate_count = sum(
            d.get('decision') == 'duplicate' for d in resolution.get('decisions', [])
        )
    except (ValueError, TypeError, AttributeError, KeyError):
        duplicate_count = 0
    return {
        'duplicate_box_count': duplicate_count,
        'reviewed_box_count': receipt['box_count'] + duplicate_count,
        "review_id": receipt["review_id"],
        "session_id": receipt["session_id"],
        "review_revision": receipt["review_revision"],
        "recorded_at": receipt["recorded_at"],
        "box_count": receipt["box_count"],
        "counts_by_marker_id": {
            row["marker_id"]: row["quantity"] for row in rows
        },
        "already_recorded": already_recorded,
        "new_box_count": (
            0 if already_recorded else receipt["box_count"]
        ),
        "inventory_recorded": True,
    }


def get_review_receipt(review_id):
    """SQLite là nguồn xác định bản nháp đã ghi nhận hay chưa."""
    with get_connection() as connection:
        connection.execute("BEGIN")

        receipt = connection.execute("""
            SELECT *
            FROM inventory_review_receipts
            WHERE review_id = ?
        """, (review_id,)).fetchone()

        if receipt is None:
            return None

        return _receipt_result(connection, receipt, True)


def record_review(session_id, document, expected_revision, check_id=None, decisions=None, *, confirmed_by=None):
    """Ghi thùng mới, giữ liên kết thùng trùng; all or nothing."""
    document = json.loads(
        json.dumps(document, allow_nan=False)
    )
    review_id = document.get("review_id")

    if not isinstance(review_id, str) or not review_id:
        raise ValueError("Thiếu mã bản nháp.")

    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("Phiên bản yêu cầu không hợp lệ.")

    with get_connection() as connection:
        # Kiểm tra trạng thái và ghi dữ liệu trong cùng giao dịch.
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute("""
            SELECT *
            FROM inventory_review_receipts
            WHERE review_id = ?
        """, (review_id,)).fetchone()

        if existing is not None:
            if existing["session_id"] != session_id:
                raise ValueError(
                    "Bản nháp đã được ghi nhận vào phiên khác."
                )

            if existing["review_revision"] != expected_revision:
                raise ValueError(
                    "Bản nháp đã được ghi nhận ở phiên bản khác."
                )

            # Chỉ đọc lại biên nhận, không ghi thêm dữ liệu.
            # Vì vậy vẫn cho phép khi phiên hoặc ngày đã đóng.
            return _receipt_result(connection, existing, True)

        if document.get("revision") != expected_revision:
            raise ValueError(
                "Bản nháp đã thay đổi. Hãy tải lại trước khi ghi."
            )

        bound_session = document.get("session_id")
        if bound_session is not None and bound_session != session_id:
            raise ValueError(
                "Bản nháp đang thuộc một phiên khác."
            )

        session = connection.execute("""
            SELECT
                s.status,
                ds.day_id,
                ds.finalized_at,
                d.status AS day_status
            FROM inventory_sessions AS s
            LEFT JOIN inventory_day_sessions AS ds
                ON ds.session_id = s.session_id
            LEFT JOIN inventory_days AS d
                ON d.day_id = ds.day_id
            WHERE s.session_id = ?
        """, (session_id,)).fetchone()

        if session is None:
            raise ValueError("Không tìm thấy phiên kiểm kê.")

        if session["status"] != "open":
            raise ValueError(
                "Phiên đã kết thúc, không thể ghi thêm."
            )

        if session["day_id"] is None:
            raise ValueError(
                "Phiên chưa thuộc ngày kiểm kê. "
                "Hãy chọn phiên theo ngày."
            )

        if session["day_status"] != "open":
            raise ValueError(
                "Ngày đã chốt hoặc không hợp lệ, "
                "không thể ghi thêm."
            )

        if session["finalized_at"] is not None:
            raise ValueError(
                "Phiên đã chốt số lượng, không thể ghi thêm."
            )

        legacy = connection.execute("""
            SELECT 1
            FROM inventory_items
            WHERE session_id = ?
            LIMIT 1
        """, (session_id,)).fetchone()

        legacy_expected = connection.execute("""
            SELECT 1
            FROM inventory_expected_items
            WHERE session_id = ?
            LIMIT 1
        """, (session_id,)).fetchone()

        if legacy or legacy_expected:
            raise ValueError(
                "Phiên chứa dữ liệu theo cơ chế cũ. "
                "Hãy tạo phiên mới để kiểm kê theo từng thùng."
            )

        summary = validate_review(document)
        boxes = document["human_review"]["boxes"]
        from backend.app.duplicate_check import validate_decisions
        report, checked = validate_decisions(
            connection, session_id, document, check_id, decisions
        )
        decisions_by_box = {d['box_id']: d for d in checked}
        new_boxes = [b for b in boxes if decisions_by_box[b['box_id']]['decision'] == 'new']
        duplicate_boxes = [b for b in boxes if decisions_by_box[b['box_id']]['decision'] == 'duplicate']
        # The receipt's box_count must always equal the number of instance rows;
        # day finalization checks this invariant.
        summary['box_count'] = len(new_boxes)


        catalog = {
            row["marker_id"]: dict(row)
            for row in connection.execute("""
                SELECT marker_id, product_code, product_name, is_active
                FROM packages
            """).fetchall()
        }

        unknown_ids = sorted(
            set(summary["counts_by_marker_id"]) - set(catalog)
        )
        if unknown_ids:
            raise ValueError(
                "ID chưa có trong danh mục: "
                + ", ".join(map(str, unknown_ids))
                + ". Chưa ghi nhận thùng nào."
            )

        inactive_ids = sorted({box['marker_id'] for box in new_boxes
                               if not catalog[box['marker_id']]['is_active']})
        if inactive_ids:
            raise ValueError('Loại hàng đã ngừng sử dụng: '
                             + ', '.join(f'{marker_id:02d}' for marker_id in inactive_ids)
                             + '. Kích hoạt lại trong danh mục trước khi ghi thùng mới.')

        recorded_at = datetime.now(timezone.utc).isoformat()

        document['duplicate_resolution'] = {
            'check_id': check_id,
            'checked_at': report['checked_at'],
            'reference_receipts': report['reference_receipts'],
            'results': report['results'],
            'decisions': checked,
            'new_box_count': len(new_boxes),
            'duplicate_box_count': len(duplicate_boxes),
            'method': 'human_reviewed_visual_comparison',
        }
        document['confirmation'] = {
            'confirmed_at': recorded_at,
            'confirmed_by': confirmed_by,
        }
        snapshot = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
        )

        connection.execute("""
            INSERT INTO inventory_review_receipts (
                review_id,
                session_id,
                review_revision,
                recorded_at,
                box_count,
                image_url,
                image_width,
                image_height,
                reviewed_payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            review_id,
            session_id,
            expected_revision,
            recorded_at,
            summary["box_count"],
            document["image_url"],
            document["width"],
            document["height"],
            snapshot,
        ))

        for box in new_boxes:
            product = catalog[box["marker_id"]]

            connection.execute("""
                INSERT INTO inventory_box_instances (
                    instance_id,
                    review_id,
                    source_box_id,
                    marker_id,
                    x1,
                    y1,
                    x2,
                    y2,
                    product_code,
                    product_name,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uuid4().hex,
                review_id,
                box["box_id"],
                box["marker_id"],
                *box["bounds"],
                product["product_code"],
                product["product_name"],
                box.get("note", ""),
            ))

        receipt = connection.execute("""
            SELECT *
            FROM inventory_review_receipts
            WHERE review_id = ?
        """, (review_id,)).fetchone()

        return _receipt_result(connection, receipt, False)


def get_box_session_summary(session_id):
    """Tổng hợp riêng các thùng ghi nhận theo cơ chế mới."""
    with get_connection() as connection:
        connection.execute("BEGIN")

        session = connection.execute("""
            SELECT session_id, name, status
            FROM inventory_sessions
            WHERE session_id = ?
        """, (session_id,)).fetchone()

        if session is None:
            raise ValueError("Không tìm thấy phiên.")

        rows = connection.execute("""
            SELECT b.marker_id, COUNT(*) AS quantity
            FROM inventory_box_instances AS b
            JOIN inventory_review_receipts AS r
                ON r.review_id = b.review_id
            WHERE r.session_id = ?
            GROUP BY b.marker_id
            ORDER BY b.marker_id
        """, (session_id,)).fetchall()

        receipt_count = connection.execute("""
            SELECT COUNT(*)
            FROM inventory_review_receipts
            WHERE session_id = ?
        """, (session_id,)).fetchone()[0]

    return {
        **dict(session),
        "receipt_count": receipt_count,
        "total_boxes": sum(row["quantity"] for row in rows),
        "counts_by_marker_id": {
            row["marker_id"]: row["quantity"] for row in rows
        },
        "cross_image_deduplication": "human_reviewed",
        "note": "Chỉ khử trùng các thùng đã được người giám sát xác nhận.",
    }


def main():
    """Kiểm tra bản nháp đã lưu, chưa ghi vào kiểm kê."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    review_id = "51478a0d5d7b4cb6844309166c9c2b2f"
    path = root / "data" / "reviews" / f"{review_id}.json"

    document = json.loads(path.read_text(encoding="utf-8"))
    summary = validate_review(document)

    print("Bản hiệu chỉnh hợp lệ.")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Đây là kiểm tra dữ liệu; chưa ghi vào phiên kiểm kê.")


if __name__ == "__main__":
    main()
