"""Quản lý mã loại hàng; không sửa số lượng hoặc báo cáo kiểm kê."""
import json
import sqlite3

from backend.app.database import DATA_DIR, get_connection, product_code_for


REFERENCE_TABLES = (
    'inventory_items', 'inventory_expected_items', 'inventory_box_instances',
    'inventory_day_opening_items', 'inventory_session_stock',
    'inventory_day_closing_items',
)


def _product(row):
    item = dict(row)
    item['marker_label'] = f"{item['marker_id']:02d}"
    item['is_active'] = bool(item['is_active'])
    return item


def _read(connection, marker_id, revision=None):
    row = connection.execute('SELECT * FROM packages WHERE marker_id=?', (marker_id,)).fetchone()
    if row is None:
        raise ValueError('Không tìm thấy ID loại hàng.')
    if revision is not None and row['catalog_revision'] != revision:
        raise ValueError('Thông tin hàng đã thay đổi. Hãy tải lại danh mục.')
    return row


def _used(connection, marker_id):
    for table in REFERENCE_TABLES:
        if connection.execute(f'SELECT 1 FROM {table} WHERE marker_id=? LIMIT 1', (marker_id,)).fetchone():
            return True
    # Saved drafts may exist without an inventory receipt yet.
    for path in (DATA_DIR / 'reviews').glob('*.json'):
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
            human = document.get('human_review') or {}
            analysis = document.get('analysis') or {}
            objects = human.get('boxes', []) + analysis.get('markers', [])
            if any(item.get('marker_id') == marker_id for item in objects):
                return True
            if any(box.get('suggested_marker_id') == marker_id for box in analysis.get('boxes', [])):
                return True
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise ValueError(f'Chưa kiểm tra được lịch sử bản nháp {path.name}; chưa xóa hoặc đổi mã hàng.') from error
    return False


def _fields(marker_id, product_code, product_name):
    code = product_code_for(marker_id)
    name = product_name.strip()
    if product_code is not None and product_code.strip().upper() != code:
        raise ValueError(f'ID {marker_id:02d} luôn đi cùng {code}. Hãy tải lại giao diện để dùng mã tự động.')
    if not name or len(name) > 120:
        raise ValueError('Tên hàng cần từ 1 đến 120 ký tự.')
    return code, name


def list_products():
    with get_connection() as connection:
        rows = connection.execute('SELECT * FROM packages ORDER BY marker_id').fetchall()
    occupied = {row['marker_id'] for row in rows}
    available = [marker_id for marker_id in range(1, 250) if marker_id not in occupied]
    return {
        'products': [_product(row) for row in rows],
        'total': len(rows),
        'active_count': sum(bool(row['is_active']) for row in rows),
        'available_marker_ids': available,
        'suggested_marker_id': available[0] if available else None,
        'dictionary': 'DICT_4X4_250',
    }


def get_product(marker_id):
    with get_connection() as connection:
        return _product(_read(connection, marker_id))


def create_product(marker_id, product_code, product_name):
    if type(marker_id) is not int or not 1 <= marker_id <= 249:
        raise ValueError('ID mới phải từ 01 đến 249.')
    code, name = _fields(marker_id, product_code, product_name)
    try:
        with get_connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM packages WHERE marker_id=?', (marker_id,)).fetchone():
                raise ValueError('ID đã được đăng ký, kể cả loại hàng đã ngừng sử dụng.')
            connection.execute('''
                INSERT INTO packages(marker_id, package_code, product_code, product_name)
                VALUES (?, ?, ?, ?)
            ''', (marker_id, f'KIEN-{marker_id:03d}', code, name))
            return _product(_read(connection, marker_id))
    except sqlite3.IntegrityError as error:
        raise ValueError('ID hoặc mã sản phẩm đã được sử dụng.') from error


def update_product(marker_id, revision, product_code, product_name):
    code, name = _fields(marker_id, product_code, product_name)
    try:
        with get_connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            _read(connection, marker_id, revision)
            connection.execute('''
                UPDATE packages SET product_code=?, product_name=?, catalog_revision=catalog_revision+1
                WHERE marker_id=?
            ''', (code, name, marker_id))
            return _product(_read(connection, marker_id))
    except sqlite3.IntegrityError as error:
        raise ValueError('Mã sản phẩm đã được dùng cho loại hàng khác.') from error


def delete_product(marker_id, revision):
    with get_connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = _read(connection, marker_id, revision)
        if _used(connection, marker_id):
            connection.execute('''
                UPDATE packages SET is_active=0, catalog_revision=catalog_revision+1
                WHERE marker_id=?
            ''', (marker_id,))
            return {'action': 'deactivated', 'product': _product(_read(connection, marker_id)),
                    'message': 'Đã ngừng sử dụng. Giữ ID và dữ liệu lịch sử; không cộng thêm thùng mới mang ID này.'}
        connection.execute('DELETE FROM packages WHERE marker_id=?', (marker_id,))
        return {'action': 'deleted', 'product': _product(row),
                'message': 'Đã xóa loại hàng chưa có dữ liệu tham chiếu.'}


def activate_product(marker_id, revision):
    with get_connection() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _read(connection, marker_id, revision)
        connection.execute('UPDATE packages SET is_active=1, catalog_revision=catalog_revision+1 WHERE marker_id=?', (marker_id,))
        return _product(_read(connection, marker_id))
