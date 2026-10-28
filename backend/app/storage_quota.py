"""NFR-14: persistent warehouse usage and admission for new capture images.

Metadata edits, reconciliation and closing remain available above the limit.
Temporary lossless queue inputs are excluded and cleaned by the existing worker.
Single backend process, as required by the camera/worker architecture.
"""
import json
import os
import re
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from backend.app.warehouse_scope import STORAGE_ROOT, current_warehouse_id
from backend.app.server_capacity import capacity_lock, require_disk_space, require_quota_increase

MIB = 1024 ** 2
DEFAULT_LIMIT = 512 * MIB
MAX_LIMIT = 102400 * MIB
_locks = {}
_locks_guard = threading.Lock()


def current_id():
    try:
        return current_warehouse_id()
    except RuntimeError:
        # Existing offline tools run outside any user's authenticated scope.
        return None


def root_for(warehouse_id):
    if not isinstance(warehouse_id, str) or not re.fullmatch('[0-9a-f]{32}', warehouse_id):
        raise HTTPException(404, 'Không tìm thấy kho.')
    return Path(STORAGE_ROOT) / warehouse_id


@contextmanager
def storage_lock(warehouse_id):
    root_for(warehouse_id)
    with _locks_guard:
        lock = _locks.setdefault(warehouse_id, threading.RLock())
    with capacity_lock, lock:
        yield


def _reject_link(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Không đo/lưu dung lượng qua liên kết thư mục.')
    return info


def policy_path(warehouse_id):
    return root_for(warehouse_id) / 'data' / 'storage_policy.json'


def _policy(warehouse_id):
    path = policy_path(warehouse_id)
    if not path.exists():
        return {'version': 0, 'limit_bytes': DEFAULT_LIMIT}
    info = _reject_link(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise ValueError('Cấu hình dung lượng không hợp lệ.')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] < 1 or type(value.get('limit_bytes')) is not int
            or not MIB <= value['limit_bytes'] <= MAX_LIMIT):
        raise ValueError('Cấu hình dung lượng không hợp lệ.')
    return value


def _measure(warehouse_id):
    root = root_for(warehouse_id)
    _reject_link(Path(STORAGE_ROOT))
    _reject_link(root)
    used = temporary = files = 0
    pending = [root]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                path = folder / entry.name
                try:
                    info = _reject_link(path)
                except FileNotFoundError:
                    # Queue cleanup and atomic JSON replacements may remove an
                    # entry between scandir and stat. It no longer uses space.
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    if path.is_relative_to(root / 'data' / 'task_inputs'):
                        temporary += info.st_size
                    else:
                        used += info.st_size
                        files += 1
                else:
                    raise ValueError('Có file đặc biệt trong kho; chưa đo được đầy đủ.')
    return used, temporary, files


def usage(warehouse_id):
    with storage_lock(warehouse_id):
        try:
            used, temporary, files = _measure(warehouse_id)
            policy = _policy(warehouse_id)
        except (OSError, ValueError, TypeError):
            raise HTTPException(503, 'Chưa đọc được đầy đủ dung lượng kho. Liên hệ admin; chưa lưu ảnh mới.')
        limit = policy['limit_bytes']
        state = 'full' if used >= limit else 'warning' if used * 5 >= limit * 4 else 'normal'
        return {'warehouse_id': warehouse_id, 'used_bytes': used, 'limit_bytes': limit,
                'remaining_bytes': max(0, limit - used), 'temporary_bytes': temporary,
                'file_count': files, 'version': policy['version'], 'state': state,
                'percent': round(100 * used / limit, 2), 'warning_percent': 80,
                'capture_allowed': used < limit, 'quota_scope': 'warehouse_persistent_files',
                'metadata_writes_allowed': True, 'automatic_deletion': False}


def require_capture_capacity(additional_bytes=0, warehouse_id=None):
    warehouse_id = warehouse_id or current_id()
    if warehouse_id is None:
        return None
    status = usage(warehouse_id)
    if not status['capture_allowed'] or status['used_bytes'] + additional_bytes > status['limit_bytes']:
        raise HTTPException(507, 'Kho đã hết dung lượng để lưu ảnh mới. Liên hệ admin tăng hạn mức; dữ liệu đã lưu vẫn dùng được.')
    require_disk_space(additional_bytes)
    return status


def write_capture(path, encoded):
    """Check the actual JPEG byte count while holding the per-warehouse lock."""
    warehouse_id = current_id()
    if warehouse_id is None:
        encoded.tofile(str(path))
        return
    with storage_lock(warehouse_id):
        require_capture_capacity(int(encoded.nbytes), warehouse_id)
        # Capture names are unique; do not overwrite an existing evidence image.
        with Path(str(path)).open('xb') as stream:
            stream.write(encoded.tobytes())


def atomic_policy(path, payload):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def set_limit(store, actor, warehouse_id, limit_mib, expected_version):
    if type(limit_mib) is not int or not 1 <= limit_mib <= 102400:
        raise HTTPException(422, 'Hạn mức phải từ 1 đến 102400 MiB.')
    with storage_lock(warehouse_id):
        status = usage(warehouse_id)
        if status['version'] != expected_version:
            raise HTTPException(409, 'Hạn mức đã thay đổi. Tải lại trước khi lưu.')
        path = policy_path(warehouse_id)
        previous = path.read_bytes() if path.exists() else None
        payload = json.dumps({'version': expected_version + 1, 'limit_bytes': limit_mib * MIB},
                             ensure_ascii=False, indent=2).encode('utf-8')
        final_used = status['used_bytes'] - len(previous or b'') + len(payload)
        if final_used > limit_mib * MIB:
            raise HTTPException(409, 'Hạn mức mới nhỏ hơn dung lượng kho đang dùng. Hãy chọn mức cao hơn.')
        require_quota_increase(store, warehouse_id, limit_mib * MIB, status)
        written = False
        try:
            with store.pool.connection() as c:
                allowed = c.execute("SELECT 1 FROM auth_users WHERE user_id=%s AND role='admin' AND is_active=TRUE AND account_version=%s FOR SHARE",
                                    (actor['user_id'], actor['account_version'])).fetchone()
                if not allowed:
                    raise HTTPException(401, 'Phiên admin không còn hiệu lực.')
                exists = c.execute('SELECT 1 FROM auth_warehouses WHERE warehouse_id=%s FOR SHARE', (warehouse_id,)).fetchone()
                if not exists:
                    raise HTTPException(404, 'Không tìm thấy kho.')
                store.audit(c, actor['user_id'], warehouse_id,
                            f"storage_limit:{status['limit_bytes']}->{limit_mib * MIB}")
                atomic_policy(path, payload)
                written = True
        except BaseException:
            if written:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_policy(path, previous)
            raise
        return {**usage(warehouse_id), 'message': 'Đã cập nhật hạn mức lưu trữ.'}
