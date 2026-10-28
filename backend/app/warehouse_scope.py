"""Request-scoped storage; no user-supplied path is ever a warehouse key."""
import os
import re
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_ROOT = Path(os.environ.get('WAREHOUSE_STORAGE_ROOT', PROJECT_ROOT / 'data' / 'warehouses'))
_warehouse = ContextVar('warehouse_id', default=None)

def current_warehouse_id():
    value = _warehouse.get()
    if value is None:
        raise RuntimeError('Không có ngữ cảnh kho đã xác thực.')
    return value

@contextmanager
def warehouse_scope(warehouse_id):
    if not isinstance(warehouse_id, str) or not re.fullmatch('[0-9a-f]{32}', warehouse_id):
        raise ValueError('Mã kho không hợp lệ.')
    token = _warehouse.set(warehouse_id)
    try:
        yield
    finally:
        _warehouse.reset(token)

class ScopedPath(os.PathLike):
    """Keep imported path references dynamic across async/threadpool requests.

    Without a request scope, existing offline database CLI tools use legacy data.
    All HTTP inventory routes are behind the authentication middleware.
    """
    def __init__(self, *parts):
        self.parts = parts

    def path(self):
        tenant = _warehouse.get()
        root = STORAGE_ROOT / tenant if tenant else PROJECT_ROOT
        return root.joinpath(*self.parts)

    def __truediv__(self, other):
        return ScopedPath(*self.parts, other)

    def __fspath__(self):
        return str(self.path())

    def __str__(self):
        return str(self.path())

    def __repr__(self):
        return repr(self.path())

    def __getattr__(self, name):
        return getattr(self.path(), name)
