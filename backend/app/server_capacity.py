"""Storage-volume budget: actual free bytes minus unspent tenant quotas.

This does not reserve filesystem blocks or measure remote PostgreSQL volumes.
One backend process is required, matching the existing GPU/camera architecture.
"""
import shutil
import threading
from pathlib import Path

from fastapi import HTTPException
from backend.app.warehouse_scope import STORAGE_ROOT

GIB = 1024 ** 3
RESERVE_BYTES = 10 * GIB
capacity_lock = threading.RLock()


def disk_snapshot():
    # Registration can run before the first warehouse directory exists.
    root = Path(STORAGE_ROOT)
    while not root.exists() and root != root.parent:
        root = root.parent
    value = shutil.disk_usage(root)
    return {'total_bytes': value.total, 'free_bytes': value.free,
            'used_bytes': value.used}


def _disk():
    try:
        result = disk_snapshot()
        if (any(type(result.get(k)) is not int or result[k] < 0
                for k in ('total_bytes', 'free_bytes', 'used_bytes'))
                or result['total_bytes'] <= 0
                or result['free_bytes'] > result['total_bytes']):
            raise ValueError('Invalid disk reading')
        return result
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, 'Chưa đo được dung lượng ổ lưu dữ liệu. Chưa nhận thêm ảnh hoặc cấp thêm dung lượng.')


def require_disk_space(additional_bytes=0):
    result = _disk()
    if result['free_bytes'] - additional_bytes < RESERVE_BYTES:
        raise HTTPException(507, 'Ổ lưu dữ liệu gần đầy; cần giữ 10 GiB dự phòng. Admin cần bổ sung dung lượng trước khi lưu ảnh mới.')
    return result


def capacity_status(store):
    from backend.app.storage_quota import usage, DEFAULT_LIMIT
    with capacity_lock:
        with store.pool.connection() as c:
            # Locked users keep their data and their unspent allocation.
            # Legacy admin data occupies disk but does not reserve a user quota.
            rows = c.execute("""SELECT w.warehouse_id FROM auth_warehouses w
                JOIN auth_users u ON u.user_id=w.owner_id
                WHERE u.role='user' ORDER BY w.warehouse_id""").fetchall()
        limits = used = unspent = 0
        for row in rows:
            value = usage(row['warehouse_id'])
            limits += value['limit_bytes']
            used += value['used_bytes']
            unspent += value['remaining_bytes']
        disk = _disk()
        headroom = disk['free_bytes'] - unspent - RESERVE_BYTES
        physical_percent = 100 * disk['used_bytes'] / disk['total_bytes']
        state = ('critical' if disk['free_bytes'] < RESERVE_BYTES or headroom < 0
                 else 'warning' if physical_percent >= 80 or headroom < DEFAULT_LIMIT
                 or disk['free_bytes'] < 2 * RESERVE_BYTES else 'normal')
        return {**disk, 'warehouse_count': len(rows), 'allocated_limit_bytes': limits,
                'warehouse_used_bytes': used, 'unspent_quota_bytes': unspent,
                'reserve_bytes': RESERVE_BYTES, 'headroom_bytes': headroom,
                'state': state, 'physical_used_percent': round(physical_percent, 2),
                'can_allocate_default_warehouse': headroom >= DEFAULT_LIMIT,
                'capture_allowed_by_disk': disk['free_bytes'] >= RESERVE_BYTES,
                'default_limit_bytes': DEFAULT_LIMIT,
                'scope': 'warehouse_storage_volume',
                'note': 'Số liệu của ổ chứa dữ liệu kho; gồm mọi file đang chiếm ổ. Không đo ổ PostgreSQL từ xa. Hạn mức là ngân sách logic, không đặt trước block trên đĩa.'}


def require_quota_increase(store, warehouse_id, proposed_limit, current_usage):
    if proposed_limit <= current_usage['limit_bytes']:
        return
    # Caller holds capacity_lock through the policy write and audit transaction.
    status = capacity_status(store)
    previous_remaining = current_usage['remaining_bytes']
    next_remaining = max(0, proposed_limit - current_usage['used_bytes'])
    if status['headroom_bytes'] - (next_remaining - previous_remaining) < 0:
        raise HTTPException(507, 'Không đủ ngân sách ổ lưu dữ liệu để tăng hạn mức này sau khi tính các kho khác và 10 GiB dự phòng. Chọn mức thấp hơn hoặc bổ sung dung lượng.')


def require_new_warehouse(store):
    from backend.app.storage_quota import DEFAULT_LIMIT
    status = capacity_status(store)
    if status['headroom_bytes'] < DEFAULT_LIMIT:
        raise HTTPException(507, 'Hệ thống chưa đủ ngân sách lưu trữ để tạo thêm kho. Vui lòng liên hệ admin.')
