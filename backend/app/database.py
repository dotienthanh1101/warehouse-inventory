"""Khởi tạo dữ liệu kiểm kê và bổ sung quản lý ngày kiểm kê."""

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
from backend.app.warehouse_scope import ScopedPath
DATA_DIR = ScopedPath("data")
DATABASE_PATH = DATA_DIR / "warehouse.db"
CODE_MIGRATION = 'catalog_product_codes_match_marker_v1'

# marker_id là mã loại hàng trong luồng kiểm kê mới.
SAMPLE_PACKAGES = [
    (23, "KIEN-023", "SP-023", "Điện trở"),
    (24, "KIEN-024", "SP-024", "Tụ điện"),
    (25, "KIEN-025", "SP-025", "Vi mạch"),
]
SAMPLE_PACKAGES += [
    (marker_id, f"KIEN-{marker_id:03d}",
     f"SP-{marker_id:03d}",
     f"Loại hàng {marker_id:02d}")
    for marker_id in range(1, 31)
    if marker_id not in (23, 24, 25)
]


def product_code_for(marker_id):
    if type(marker_id) is not int or not 0 <= marker_id <= 249:
        raise ValueError('ID marker không hợp lệ.')
    return f'SP-{marker_id:03d}'


def _table_exists(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _backup_before_alignment():
    """Copy the committed SQLite database before the one-time metadata migration."""
    if not DATABASE_PATH.is_file():
        return None
    with sqlite3.connect(str(DATABASE_PATH), timeout=10) as source:
        if not _table_exists(source, 'packages'):
            return None
        if (_table_exists(source, 'schema_migrations') and source.execute(
                'SELECT 1 FROM schema_migrations WHERE name=?', (CODE_MIGRATION,)).fetchone()):
            return None
        folder = DATA_DIR / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        path = folder / f'warehouse_before_code_alignment_{stamp}_{uuid4().hex[:8]}.db'
        try:
            with sqlite3.connect(str(path)) as target:
                source.backup(target)
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Không xác minh được bản sao lưu; dừng chuyển đổi mã.')
        except Exception:
            path.unlink(missing_ok=True)
            raise
    print(f'Đã sao lưu trước khi đổi mã: {path}')
    return str(path)


def _aligned_payload(value, old_codes, owner=None):
    """Update only product_code fields, with explicit marker IDs taking priority."""
    if isinstance(value, list):
        return [_aligned_payload(item, old_codes) for item in value]
    if not isinstance(value, dict):
        return value
    marker = value.get('marker_id')
    if type(marker) is int and 0 <= marker <= 249:
        owner = marker
    elif ('package' in value or 'registration_status' in value) and type(value.get('id')) is int:
        owner = value['id']
    result = {}
    for key, item in value.items():
        if key == 'product_code' and isinstance(item, str):
            if owner is not None:
                result[key] = product_code_for(owner)
            else:
                result[key] = old_codes.get(item.strip().upper(), item)
        else:
            # Only the nested product/package of this object inherits its ID.
            # Other nested objects or lists can describe unrelated products.
            next_owner = owner if key in ('package', 'product') else None
            result[key] = _aligned_payload(item, old_codes, next_owner)
    return result


def _align_product_codes(connection, backup_path):
    if connection.execute('SELECT 1 FROM schema_migrations WHERE name=?', (CODE_MIGRATION,)).fetchone():
        return
    rows = connection.execute('SELECT marker_id, product_code FROM packages ORDER BY marker_id').fetchall()
    changes = [{'marker_id': row['marker_id'], 'old_code': row['product_code'],
                'new_code': product_code_for(row['marker_id'])} for row in rows
               if row['product_code'] != product_code_for(row['marker_id'])]
    old_codes = {row['product_code'].strip().upper(): product_code_for(row['marker_id']) for row in rows}
    # Vacate all changing codes first, so swaps cannot hit the unique index.
    prefix = '__ALIGN_' + uuid4().hex + '_'
    for change in changes:
        connection.execute('UPDATE packages SET product_code=? WHERE marker_id=?',
                           (prefix + str(change['marker_id']), change['marker_id']))
    for change in changes:
        connection.execute('UPDATE packages SET product_code=?, catalog_revision=catalog_revision+1 WHERE marker_id=?',
                           (change['new_code'], change['marker_id']))
    history_tables = ('inventory_box_instances', 'inventory_day_opening_items',
                      'inventory_session_stock', 'inventory_day_closing_items')
    for table in history_tables:
        if _table_exists(connection, table):
            connection.execute(f"UPDATE {table} SET product_code=printf('SP-%03d', marker_id) WHERE product_code<>printf('SP-%03d', marker_id)")

    connection.execute('''CREATE TABLE IF NOT EXISTS catalog_code_original_receipts (
        review_id TEXT PRIMARY KEY, original_payload_json TEXT NOT NULL
    )''')
    updated_receipts = 0
    if _table_exists(connection, 'inventory_review_receipts'):
        receipts = connection.execute('SELECT review_id, reviewed_payload_json FROM inventory_review_receipts').fetchall()
        for receipt in receipts:
            try:
                original = json.loads(receipt['reviewed_payload_json'])
                if not isinstance(original, dict):
                    raise ValueError('Không phải bản lưu JSON dạng đối tượng.')
                aligned = _aligned_payload(original, old_codes)
            except (ValueError, TypeError) as error:
                raise ValueError(f"Bản lưu {receipt['review_id']} lỗi; đã hủy chuyển đổi mã.") from error
            if aligned != original:
                connection.execute('INSERT INTO catalog_code_original_receipts VALUES (?, ?)',
                                   (receipt['review_id'], receipt['reviewed_payload_json']))
                connection.execute('UPDATE inventory_review_receipts SET reviewed_payload_json=? WHERE review_id=?',
                                   (json.dumps(aligned, ensure_ascii=False, allow_nan=False), receipt['review_id']))
                updated_receipts += 1

    connection.execute('''CREATE TABLE IF NOT EXISTS catalog_code_migration_log (
        migration TEXT PRIMARY KEY, performed_at TEXT NOT NULL, backup_path TEXT,
        changes_json TEXT NOT NULL, updated_receipts INTEGER NOT NULL
    )''')
    connection.execute('INSERT INTO catalog_code_migration_log VALUES (?, ?, ?, ?, ?)',
                       (CODE_MIGRATION, datetime.now(timezone.utc).isoformat(), backup_path,
                        json.dumps(changes, ensure_ascii=False), updated_receipts))
    connection.execute('INSERT INTO schema_migrations(name) VALUES (?)', (CODE_MIGRATION,))


def _upgrade_catalog(connection, backup_path):
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(packages)')}
    if 'is_active' not in columns:
        connection.execute('ALTER TABLE packages ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))')
    if 'catalog_revision' not in columns:
        connection.execute('ALTER TABLE packages ADD COLUMN catalog_revision INTEGER NOT NULL DEFAULT 1 CHECK (catalog_revision >= 1)')
    duplicates = connection.execute('''
        SELECT upper(trim(product_code)) AS code FROM packages
        GROUP BY upper(trim(product_code)) HAVING COUNT(*) > 1
    ''').fetchall()
    if duplicates:
        raise ValueError('Danh mục cũ có mã sản phẩm trùng: ' + ', '.join(row['code'] for row in duplicates))
    connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_product_code ON packages(upper(trim(product_code)))')
    connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)')
    _align_product_codes(connection, backup_path)
    # Guard the rule even for an old API client or direct SQL insert/update.
    connection.execute('''CREATE TRIGGER IF NOT EXISTS enforce_catalog_code_insert
        BEFORE INSERT ON packages
        WHEN NEW.product_code <> printf('SP-%03d', NEW.marker_id)
        BEGIN SELECT RAISE(ABORT, 'Product code must match marker ID'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS enforce_catalog_code_update
        BEFORE UPDATE OF product_code, marker_id ON packages
        WHEN NEW.product_code <> printf('SP-%03d', NEW.marker_id)
        BEGIN SELECT RAISE(ABORT, 'Product code must match marker ID'); END''')
    migration = 'catalog_defaults_01_30_v1'
    if connection.execute('SELECT 1 FROM schema_migrations WHERE name=?', (migration,)).fetchone():
        return
    for marker_id, package_code, product_code, product_name in SAMPLE_PACKAGES:
        # Existing identity and names always take precedence over defaults.
        if connection.execute('SELECT 1 FROM packages WHERE marker_id=?', (marker_id,)).fetchone():
            continue
        try:
            connection.execute('''
                INSERT INTO packages(marker_id, package_code, product_code, product_name)
                VALUES (?, ?, ?, ?)
            ''', (marker_id, package_code, product_code, product_name))
        except sqlite3.IntegrityError as error:
            raise ValueError(f'Không bổ sung được ID {marker_id:02d}: mã {product_code} hoặc {package_code} đã được dùng. Chưa cập nhật dữ liệu.') from error
    connection.execute('INSERT INTO schema_migrations(name) VALUES (?)', (migration,))


@contextmanager
def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        str(DATABASE_PATH),
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    """Bổ sung cấu trúc trong một giao dịch, giữ dữ liệu hiện có."""
    backup_path = _backup_before_alignment()
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")

        # ---------------------------------------------------------
        # 1. Danh mục và các bảng đang sử dụng
        # ---------------------------------------------------------

        connection.execute("""
            CREATE TABLE IF NOT EXISTS packages (
                marker_id INTEGER PRIMARY KEY
                    CHECK (marker_id BETWEEN 0 AND 249),
                package_code TEXT NOT NULL UNIQUE,
                product_code TEXT NOT NULL,
                product_name TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_sessions (
                session_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed')),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                expected_configured INTEGER NOT NULL DEFAULT 0
                    CHECK (expected_configured IN (0, 1)),
                CHECK (
                    (status = 'open' AND ended_at IS NULL)
                    OR
                    (status = 'closed' AND ended_at IS NOT NULL)
                )
            )
        """)

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(inventory_sessions)"
            ).fetchall()
        }

        if "expected_configured" not in columns:
            connection.execute("""
                ALTER TABLE inventory_sessions
                ADD COLUMN expected_configured
                    INTEGER NOT NULL DEFAULT 0
                    CHECK (expected_configured IN (0, 1))
            """)

        # Cơ chế cũ: một marker_id tương ứng một dòng trong phiên.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_items (
                session_id TEXT NOT NULL,
                marker_id INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_capture_id TEXT NOT NULL,
                evidence_image_url TEXT NOT NULL,

                PRIMARY KEY (session_id, marker_id),

                FOREIGN KEY (session_id)
                    REFERENCES inventory_sessions(session_id),

                FOREIGN KEY (marker_id)
                    REFERENCES packages(marker_id)
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_expected_items (
                session_id TEXT NOT NULL,
                marker_id INTEGER NOT NULL,

                PRIMARY KEY (session_id, marker_id),

                FOREIGN KEY (session_id)
                    REFERENCES inventory_sessions(session_id),

                FOREIGN KEY (marker_id)
                    REFERENCES packages(marker_id)
            )
        """)

        # Một bản nháp chỉ được ghi nhận một lần.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_review_receipts (
                review_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                review_revision INTEGER NOT NULL
                    CHECK (review_revision >= 1),
                recorded_at TEXT NOT NULL,
                box_count INTEGER NOT NULL
                    CHECK (box_count >= 0),
                image_url TEXT NOT NULL,
                image_width INTEGER NOT NULL
                    CHECK (image_width > 0),
                image_height INTEGER NOT NULL
                    CHECK (image_height > 0),
                reviewed_payload_json TEXT NOT NULL,

                FOREIGN KEY (session_id)
                    REFERENCES inventory_sessions(session_id)
            )
        """)

        # Mỗi dòng tương ứng một thùng trong bản nháp đã ghi nhận.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_box_instances (
                instance_id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL,
                source_box_id TEXT NOT NULL,
                marker_id INTEGER NOT NULL
                    CHECK (marker_id BETWEEN 0 AND 249),

                x1 REAL NOT NULL,
                y1 REAL NOT NULL,
                x2 REAL NOT NULL,
                y2 REAL NOT NULL,

                product_code TEXT NOT NULL,
                product_name TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',

                UNIQUE (review_id, source_box_id),

                CHECK (
                    x1 >= 0 AND y1 >= 0
                    AND x2 > x1 AND y2 > y1
                ),

                FOREIGN KEY (review_id)
                    REFERENCES inventory_review_receipts(review_id),

                FOREIGN KEY (marker_id)
                    REFERENCES packages(marker_id)
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_review_receipts_session
            ON inventory_review_receipts(session_id)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_box_instances_marker
            ON inventory_box_instances(marker_id)
        """)

        _upgrade_catalog(connection, backup_path)

        # ---------------------------------------------------------
        # 2. Ngày kiểm kê
        # ---------------------------------------------------------

        # business_date: ngày tại Việt Nam, định dạng YYYY-MM-DD.
        # opened_at / closed_at: thời điểm đầy đủ, lưu UTC.
        #
        # source_day_id:
        # - NULL: tồn đầu được nhập khi khởi tạo.
        # - Có giá trị: tồn đầu lấy từ ngày đã chốt trước đó.
        #
        # Việc kiểm tra ngày hợp lệ, nguồn đã chốt và quyền sửa
        # sẽ được thực hiện trong service ở bước tiếp theo.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_days (
                day_id TEXT PRIMARY KEY,
                business_date TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed')),

                opened_at TEXT NOT NULL,
                closed_at TEXT,
                source_day_id TEXT,
                note TEXT NOT NULL DEFAULT '',

                CHECK (
                    (status = 'open' AND closed_at IS NULL)
                    OR
                    (status = 'closed' AND closed_at IS NOT NULL)
                ),

                CHECK (
                    source_day_id IS NULL
                    OR source_day_id <> day_id
                ),

                FOREIGN KEY (source_day_id)
                    REFERENCES inventory_days(day_id)
            )
        """)

        # Với phạm vi một kho: chỉ cho một ngày đang mở.
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_inventory_day
            ON inventory_days(status)
            WHERE status = 'open'
        """)

        # ---------------------------------------------------------
        # 3. Tồn đầu ngày
        # ---------------------------------------------------------

        # Lưu bản sao tên/mã sản phẩm để báo cáo lịch sử không
        # phụ thuộc việc sửa tên danh mục sau này.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_day_opening_items (
                day_id TEXT NOT NULL,
                marker_id INTEGER NOT NULL,
                product_code TEXT NOT NULL,
                product_name TEXT NOT NULL,

                quantity INTEGER NOT NULL
                    CHECK (
                        typeof(quantity) = 'integer'
                        AND quantity >= 0
                    ),

                PRIMARY KEY (day_id, marker_id),

                FOREIGN KEY (day_id)
                    REFERENCES inventory_days(day_id),

                FOREIGN KEY (marker_id)
                    REFERENCES packages(marker_id)
            )
        """)

        # ---------------------------------------------------------
        # 4. Liên kết phiên với ngày và thứ tự kiểm kê
        # ---------------------------------------------------------

        # session_order bắt đầu từ 1 trong mỗi ngày.
        # finalized_at chỉ được ghi khi đã xác nhận kiểm kê đủ
        # và đã lưu số lượng cuối cùng của phiên.
        #
        # Không tự gắn các phiên cũ vào bảng này.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_day_sessions (
                session_id TEXT PRIMARY KEY,
                day_id TEXT NOT NULL,

                session_order INTEGER NOT NULL
                    CHECK (
                        typeof(session_order) = 'integer'
                        AND session_order >= 1
                    ),

                finalized_at TEXT,

                UNIQUE (day_id, session_order),

                FOREIGN KEY (session_id)
                    REFERENCES inventory_sessions(session_id),

                FOREIGN KEY (day_id)
                    REFERENCES inventory_days(day_id)
            )
        """)

        # Chỉ một phiên chưa chốt trong mỗi ngày.
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pending_session_per_day
            ON inventory_day_sessions(day_id)
            WHERE finalized_at IS NULL
        """)

        # ---------------------------------------------------------
        # 5. Số lượng đã chốt của từng phiên
        # ---------------------------------------------------------

        # Đây là ảnh chụp dữ liệu tồn kho tại thời điểm chốt phiên.
        # Không cộng số lượng của các phiên để tính tồn cuối ngày.
        #
        # Khi chốt phiên, service sẽ ghi cả quantity = 0 cho loại
        # hàng thuộc mốc so sánh nhưng không còn trong kho,
        # sau khi người giám sát xác nhận đã kiểm kê đầy đủ.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_session_stock (
                session_id TEXT NOT NULL,
                marker_id INTEGER NOT NULL,
                product_code TEXT NOT NULL,
                product_name TEXT NOT NULL,

                quantity INTEGER NOT NULL
                    CHECK (
                        typeof(quantity) = 'integer'
                        AND quantity >= 0
                    ),

                PRIMARY KEY (session_id, marker_id),

                FOREIGN KEY (session_id)
                    REFERENCES inventory_day_sessions(session_id),

                FOREIGN KEY (marker_id)
                    REFERENCES packages(marker_id)
            )
        """)

        # ---------------------------------------------------------
        # 6. Báo cáo đã chốt cuối ngày
        # ---------------------------------------------------------

        # total_increase / total_decrease:
        # cộng các chênh lệch tăng / giảm giữa phiên liên tiếp.
        # Đây không phải tổng nhập / xuất thực tế.
        #
        # Service sẽ ghi báo cáo và đổi trạng thái ngày sang closed
        # trong cùng một giao dịch.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS inventory_day_closing_items (
                day_id TEXT NOT NULL,
                marker_id INTEGER NOT NULL,
                product_code TEXT NOT NULL,
                product_name TEXT NOT NULL,

                opening_quantity INTEGER NOT NULL
                    CHECK (
                        typeof(opening_quantity) = 'integer'
                        AND opening_quantity >= 0
                    ),

                total_increase INTEGER NOT NULL
                    CHECK (
                        typeof(total_increase) = 'integer'
                        AND total_increase >= 0
                    ),

                total_decrease INTEGER NOT NULL
                    CHECK (
                        typeof(total_decrease) = 'integer'
                        AND total_decrease >= 0
                    ),

                closing_quantity INTEGER NOT NULL
                    CHECK (
                        typeof(closing_quantity) = 'integer'
                        AND closing_quantity >= 0
                    ),

                PRIMARY KEY (day_id, marker_id),

                CHECK (
                    closing_quantity =
                        opening_quantity
                        + total_increase
                        - total_decrease
                ),

                FOREIGN KEY (day_id)
                    REFERENCES inventory_days(day_id),

                FOREIGN KEY (marker_id)
                    REFERENCES packages(marker_id)
            )
        """)


def print_database_summary():
    with get_connection() as connection:
        connection.execute("BEGIN")

        products = connection.execute("""
            SELECT marker_id, product_code, product_name, is_active
            FROM packages
            ORDER BY marker_id
        """).fetchall()

        sessions = connection.execute("""
            SELECT
                s.session_id,
                s.name,
                s.status,
                (
                    SELECT COUNT(*)
                    FROM inventory_items AS i
                    WHERE i.session_id = s.session_id
                ) AS legacy_count,
                (
                    SELECT COUNT(*)
                    FROM inventory_review_receipts AS r
                    WHERE r.session_id = s.session_id
                ) AS receipt_count,
                (
                    SELECT COUNT(*)
                    FROM inventory_box_instances AS b
                    JOIN inventory_review_receipts AS r
                        ON r.review_id = b.review_id
                    WHERE r.session_id = s.session_id
                ) AS box_count,
                d.business_date,
                ds.session_order
            FROM inventory_sessions AS s
            LEFT JOIN inventory_day_sessions AS ds
                ON ds.session_id = s.session_id
            LEFT JOIN inventory_days AS d
                ON d.day_id = ds.day_id
            ORDER BY s.started_at, s.session_id
        """).fetchall()

        days = connection.execute("""
            SELECT
                d.day_id,
                d.business_date,
                d.status,
                (
                    SELECT COUNT(*)
                    FROM inventory_day_sessions AS ds
                    WHERE ds.day_id = d.day_id
                ) AS session_count
            FROM inventory_days AS d
            ORDER BY d.business_date
        """).fetchall()

        total_receipts = connection.execute("""
            SELECT COUNT(*) FROM inventory_review_receipts
        """).fetchone()[0]

        total_boxes = connection.execute("""
            SELECT COUNT(*) FROM inventory_box_instances
        """).fetchone()[0]

    print(f"Cơ sở dữ liệu: {DATABASE_PATH}")
    print(f"Số loại hàng trong danh mục: {len(products)}")

    for product in products:
        print(
            f"  ID {product['marker_id']:02d} | "
            f"{product['product_code']} | "
            f"{product['product_name']} | "
            f"{'Đang dùng' if product['is_active'] else 'Ngừng sử dụng'}"
        )

    print(f"\nSố phiên: {len(sessions)}")

    for session in sessions:
        print(f"\nPhiên: {session['name']}")
        print(f"  ID: {session['session_id']}")
        print(f"  Trạng thái: {session['status']}")
        print(
            "  Số dòng ghi nhận theo cơ chế cũ: "
            f"{session['legacy_count']}"
        )
        print(
            "  Số bản nháp đã ghi nhận: "
            f"{session['receipt_count']}"
        )
        print(
            "  Số thùng theo cơ chế mới: "
            f"{session['box_count']}"
        )

        if session["business_date"] is None:
            print("  Ngày kiểm kê: Chưa liên kết")
        else:
            print(
                f"  Ngày kiểm kê: {session['business_date']} | "
                f"Phiên thứ {session['session_order']}"
            )

    print(f"\nTổng bản nháp đã ghi nhận: {total_receipts}")
    print(f"Tổng thùng đã ghi nhận theo cơ chế mới: {total_boxes}")

    print(f"\nSố ngày kiểm kê: {len(days)}")

    for day in days:
        print(
            f"  {day['business_date']} | "
            f"{day['status']} | "
            f"{day['session_count']} phiên"
        )

    print("\nĐã sẵn sàng 5 bảng quản lý kiểm kê theo ngày:")
    print("  inventory_days")
    print("  inventory_day_opening_items")
    print("  inventory_day_sessions")
    print("  inventory_session_stock")
    print("  inventory_day_closing_items")

    print("\nDữ liệu cũ được giữ nguyên.")
    print("Chưa tự liên kết các phiên cũ vào ngày kiểm kê.")
    print("Danh mục mặc định ID 01–30 chỉ được bổ sung một lần.")
    print("Không tạo lại sản phẩm đã xóa khi khởi động lại backend.")
    print("Mã sản phẩm cố định theo ID: 01 → SP-001; 23 → SP-023.")


if __name__ == "__main__":
    initialize_database()
    print_database_summary()
