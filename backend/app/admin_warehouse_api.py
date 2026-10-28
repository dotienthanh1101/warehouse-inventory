"""FR-33: audited, read-only browsing of committed warehouse records.

No user impersonation and no inventory writes. SQLite is opened in mode=ro;
images are selected only through a receipt belonging to the requested warehouse.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse
from psycopg import Error as PostgresError

from backend.app.auth_api import admin_identity
from backend.app.day_closing_service import _build_report
from backend.app.warehouse_scope import STORAGE_ROOT

router = APIRouter(prefix='/api/admin/warehouses', tags=['Admin warehouse read-only'])
Id = Annotated[str, Path(pattern='^[0-9a-f]{32}$')]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def access(request, warehouse_id, action):
    actor = admin_identity(request)
    store = request.app.state.auth_store
    try:
        with store.pool.connection() as c:
            warehouse = c.execute('''SELECT w.warehouse_id,w.name,u.username,u.is_active
                FROM auth_warehouses w JOIN auth_users u ON u.user_id=w.owner_id
                WHERE w.warehouse_id=%s''', (warehouse_id,)).fetchone()
            if warehouse is None:
                raise HTTPException(404, 'Không tìm thấy kho.')
            # Fail closed if the access event cannot be recorded.
            store.audit(c, actor['user_id'], warehouse_id, 'admin_read:' + action)
    except PostgresError as error:
        raise HTTPException(503, 'Chưa ghi được nhật ký truy cập kho. Hãy thử lại.') from error
    return dict(warehouse)


def warehouse_root(warehouse_id):
    if not re.fullmatch('[0-9a-f]{32}', warehouse_id):
        raise HTTPException(404, 'Không tìm thấy kho.')
    root = STORAGE_ROOT / warehouse_id
    if root.is_symlink() or not root.is_dir():
        raise HTTPException(409, 'Thư mục dữ liệu kho chưa sẵn sàng.')
    return root.resolve()


@contextmanager
def readonly_db(warehouse_id):
    root = warehouse_root(warehouse_id)
    path = root / 'data' / 'warehouse.db'
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise HTTPException(409, 'Chưa có cơ sở dữ liệu hợp lệ của kho. Không tự tạo dữ liệu thay thế.')
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        yield connection
    except sqlite3.Error as error:
        raise HTTPException(503, 'Chưa đọc được dữ liệu kho. Hãy thử lại hoặc kiểm tra cơ sở dữ liệu.') from error
    finally:
        if connection is not None:
            connection.close()


def require_row(c, table, key, value):
    # Table/key are internal literals, never request-controlled identifiers.
    row = c.execute(f'SELECT * FROM {table} WHERE {key}=?', (value,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Không tìm thấy dữ liệu trong kho này.')
    return row


@router.get('/{warehouse_id}/records')
def records(warehouse_id: Id, request: Request):
    warehouse = access(request, warehouse_id, 'records')
    with readonly_db(warehouse_id) as c:
        summary = {key: c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                   for key, table in [('product_count', 'packages'), ('day_count', 'inventory_days'),
                                      ('session_count', 'inventory_sessions'), ('receipt_count', 'inventory_review_receipts'),
                                      ('box_count', 'inventory_box_instances')]}
    return {'warehouse': warehouse, 'summary': summary, 'read_only': True}


@router.get('/{warehouse_id}/days')
def days(warehouse_id: Id, request: Request, limit: Limit = 20, offset: Offset = 0):
    access(request, warehouse_id, 'days')
    with readonly_db(warehouse_id) as c:
        total = c.execute('SELECT COUNT(*) FROM inventory_days').fetchone()[0]
        rows = c.execute('''SELECT day_id,business_date,status,opened_at,closed_at
            FROM inventory_days ORDER BY business_date DESC,day_id LIMIT ? OFFSET ?''', (limit, offset)).fetchall()
    return {'days': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset, 'read_only': True}


@router.get('/{warehouse_id}/days/{day_id}')
def day_detail(warehouse_id: Id, day_id: Id, request: Request):
    access(request, warehouse_id, 'day:' + day_id)
    with readonly_db(warehouse_id) as c:
        day = dict(require_row(c, 'inventory_days', 'day_id', day_id))
        opening = [dict(r) for r in c.execute('''SELECT marker_id,product_code,product_name,quantity
            FROM inventory_day_opening_items WHERE day_id=? ORDER BY marker_id''', (day_id,)).fetchall()]
        try:
            report, message = _build_report(c, day_id), None
        except ValueError as error:
            report, message = None, str(error)
    return {'day': day, 'opening_items': opening, 'report': report, 'report_message': message, 'read_only': True}


@router.get('/{warehouse_id}/sessions')
def sessions(warehouse_id: Id, request: Request, day_id: Annotated[str | None, Query(pattern='^[0-9a-f]{32}$')] = None,
             limit: Limit = 20, offset: Offset = 0):
    access(request, warehouse_id, 'sessions' + (':' + day_id if day_id else ''))
    with readonly_db(warehouse_id) as c:
        if day_id:
            require_row(c, 'inventory_days', 'day_id', day_id)
        condition, params = ('WHERE ds.day_id=?', (day_id,)) if day_id else ('', ())
        base = '''FROM inventory_sessions s LEFT JOIN inventory_day_sessions ds ON ds.session_id=s.session_id
            LEFT JOIN inventory_days d ON d.day_id=ds.day_id '''
        total = c.execute('SELECT COUNT(*) ' + base + condition, params).fetchone()[0]
        rows = c.execute('''SELECT s.session_id,s.name,s.status,s.started_at,s.ended_at,
            ds.day_id,ds.session_order,ds.finalized_at,d.business_date ''' + base + condition +
            ' ORDER BY s.started_at DESC,s.session_id LIMIT ? OFFSET ?', (*params, limit, offset)).fetchall()
    return {'sessions': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset, 'read_only': True}


@router.get('/{warehouse_id}/sessions/{session_id}')
def session_detail(warehouse_id: Id, session_id: Id, request: Request):
    access(request, warehouse_id, 'session:' + session_id)
    with readonly_db(warehouse_id) as c:
        session = dict(require_row(c, 'inventory_sessions', 'session_id', session_id))
        link = c.execute('''SELECT ds.day_id,ds.session_order,ds.finalized_at,d.business_date,d.status AS day_status
            FROM inventory_day_sessions ds JOIN inventory_days d ON d.day_id=ds.day_id
            WHERE ds.session_id=?''', (session_id,)).fetchone()
        receipt_count = c.execute('SELECT COUNT(*) FROM inventory_review_receipts WHERE session_id=?', (session_id,)).fetchone()[0]
        rows = c.execute('''SELECT b.marker_id,b.product_code,b.product_name,COUNT(*) AS quantity
            FROM inventory_box_instances b JOIN inventory_review_receipts r ON r.review_id=b.review_id
            WHERE r.session_id=? GROUP BY b.marker_id,b.product_code,b.product_name
            ORDER BY b.marker_id,b.product_code,b.product_name''', (session_id,)).fetchall()
        stock = c.execute('''SELECT marker_id,product_code,product_name,quantity
            FROM inventory_session_stock WHERE session_id=? ORDER BY marker_id''', (session_id,)).fetchall()
        legacy_count = c.execute('SELECT COUNT(*) FROM inventory_items WHERE session_id=?', (session_id,)).fetchone()[0]
    finalized = link is not None and link['finalized_at'] is not None
    stock_message = None
    if link is not None and finalized != (session['status'] == 'closed'):
        stock_message = 'Trạng thái chốt phiên chưa đồng bộ. Cần kiểm tra dữ liệu nguồn.'
    elif finalized and not stock:
        stock_message = 'Phiên đã chốt nhưng thiếu bản tồn đã lưu. Không tự coi dữ liệu thiếu là số lượng 0.'
    return {'session': {**session, **dict(link or {})}, 'finalized': finalized, 'receipt_count': receipt_count,
            'recorded_total_boxes': sum(r['quantity'] for r in rows), 'recorded_items': [dict(r) for r in rows],
            'stock_items': [dict(r) for r in stock] if finalized else None,
            'legacy_row_count': legacy_count, 'stock_message': stock_message, 'read_only': True,
            'note': 'Số đã ghi trong phiên đang mở chưa phải tồn cuối. Dòng theo cơ chế cũ không cộng vào số kiện mới.'}


@router.get('/{warehouse_id}/sessions/{session_id}/receipts')
def receipts(warehouse_id: Id, session_id: Id, request: Request, limit: Limit = 20, offset: Offset = 0):
    access(request, warehouse_id, 'receipts:' + session_id)
    with readonly_db(warehouse_id) as c:
        require_row(c, 'inventory_sessions', 'session_id', session_id)
        total = c.execute('SELECT COUNT(*) FROM inventory_review_receipts WHERE session_id=?', (session_id,)).fetchone()[0]
        rows = c.execute('''SELECT review_id,session_id,review_revision,recorded_at,box_count
            FROM inventory_review_receipts WHERE session_id=? ORDER BY recorded_at DESC,review_id
            LIMIT ? OFFSET ?''', (session_id, limit, offset)).fetchall()
    return {'receipts': [dict(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset, 'read_only': True}


@router.get('/{warehouse_id}/receipts/{review_id}')
def receipt_detail(warehouse_id: Id, review_id: Id, request: Request, limit: Limit = 50, offset: Offset = 0):
    access(request, warehouse_id, 'receipt:' + review_id)
    with readonly_db(warehouse_id) as c:
        raw = require_row(c, 'inventory_review_receipts', 'review_id', review_id)
        receipt = {key: raw[key] for key in ('review_id', 'session_id', 'review_revision', 'recorded_at',
                                           'box_count', 'image_width', 'image_height')}
        total = c.execute('SELECT COUNT(*) FROM inventory_box_instances WHERE review_id=?', (review_id,)).fetchone()[0]
        rows = c.execute('''SELECT instance_id,source_box_id,marker_id,product_code,product_name,note,x1,y1,x2,y2
            FROM inventory_box_instances WHERE review_id=? ORDER BY source_box_id,instance_id
            LIMIT ? OFFSET ?''', (review_id, limit, offset)).fetchall()
        counts = c.execute('''SELECT marker_id,product_code,product_name,COUNT(*) AS quantity
            FROM inventory_box_instances WHERE review_id=? GROUP BY marker_id,product_code,product_name
            ORDER BY marker_id''', (review_id,)).fetchall()
        warning = None
        try:
            snapshot = json.loads(raw['reviewed_payload_json'])
            resolution = snapshot.get('duplicate_resolution') or {}
            saved = resolution.get('decisions', [])
            if not isinstance(saved, list) or any(not isinstance(d, dict) for d in saved):
                raise ValueError('Invalid decisions')
            if any(not isinstance(d.get('box_id'), str) or d.get('decision') not in ('new', 'duplicate')
                   or not isinstance(d.get('note', ''), str)
                   or d.get('reference_instance_id') is not None and not isinstance(d['reference_instance_id'], str)
                   for d in saved):
                raise ValueError('Invalid decision fields')
            decisions = [{k: d.get(k) for k in ('box_id', 'decision', 'reference_instance_id', 'note')} for d in saved]
        except (ValueError, TypeError, AttributeError):
            decisions = []
            warning = 'Chưa đọc được quyết định từ bản lưu. Số kiện lấy từ các dòng đã ghi, không suy ra từ bản lưu lỗi.'
    return {'receipt': receipt, 'instances': [dict(r) for r in rows], 'counts': [dict(r) for r in counts],
            'total': total, 'limit': limit, 'offset': offset, 'read_only': True,
            'decisions': decisions[offset:offset + limit], 'decision_total': len(decisions),
            'duplicate_count': sum(d['decision'] == 'duplicate' for d in decisions), 'snapshot_warning': warning,
            'image_url': f'/api/admin/warehouses/{warehouse_id}/receipts/{review_id}/image'}


def evidence_path(warehouse_id, image_url):
    prefix = '/api/captures/'
    if not isinstance(image_url, str) or not image_url.startswith(prefix):
        raise HTTPException(404, 'Ảnh bằng chứng không hợp lệ.')
    filename = image_url[len(prefix):]
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(jpg|jpeg|png)', filename):
        raise HTTPException(404, 'Ảnh bằng chứng không hợp lệ.')
    root = warehouse_root(warehouse_id)
    folder = root / 'captures'
    path = folder / filename
    if (folder.is_symlink() or path.is_symlink() or not path.is_file()
            or not folder.resolve().is_relative_to(root) or not path.resolve().is_relative_to(folder.resolve())):
        raise HTTPException(404, 'Ảnh bằng chứng không còn sẵn có. Số liệu đã ghi vẫn được giữ.')
    return path


@router.get('/{warehouse_id}/receipts/{review_id}/image')
def receipt_image(warehouse_id: Id, review_id: Id, request: Request):
    access(request, warehouse_id, 'image:' + review_id)
    with readonly_db(warehouse_id) as c:
        row = require_row(c, 'inventory_review_receipts', 'review_id', review_id)
        path = evidence_path(warehouse_id, row['image_url'])
    return FileResponse(path, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
