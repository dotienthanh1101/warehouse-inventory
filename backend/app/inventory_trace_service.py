"""Read immutable SQLite receipts, never mutable review JSON, to trace report rows."""
import json
import re
from urllib.parse import urlsplit

from backend.app.database import get_connection
from backend.app.day_closing_service import _build_report
from backend.app.warehouse_scope import ScopedPath, current_warehouse_id

ID = re.compile(r'^[0-9a-f]{32}$')
ACTOR_KEYS = ('user_id', 'username', 'display_name', 'warehouse_id')


def actor(value):
    if not isinstance(value, dict) or not value.get('user_id'):
        return None
    if value.get('warehouse_id') != current_warehouse_id():
        raise ValueError('Danh tính trong biên nhận không thuộc kho hiện tại.')
    return {key: value.get(key) for key in ACTOR_KEYS}


def image(value):
    # A stored URL is not a redirect/link to arbitrary external content.
    if not isinstance(value, str):
        raise ValueError('Biên nhận thiếu ảnh bằng chứng.')
    p = urlsplit(value)
    name = p.path.removeprefix('/api/captures/')
    if (p.scheme or p.netloc or p.query or p.fragment or not p.path.startswith('/api/captures/')
            or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:jpg|jpeg|png)', name)):
        raise ValueError('Đường dẫn ảnh bằng chứng không hợp lệ.')
    return {'image_url': p.path, 'image_available': (ScopedPath('captures') / name).is_file()}


def get_day_trace(day_id, marker_id, session_id=None, limit=20, offset=0):
    if not isinstance(day_id, str) or not ID.fullmatch(day_id):
        raise ValueError('Mã ngày không hợp lệ.')
    if type(marker_id) is not int or not 0 <= marker_id <= 249:
        raise ValueError('ID loại hàng không hợp lệ.')
    if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
        raise ValueError('Phân trang không hợp lệ.')
    with get_connection() as c:
        c.execute('BEGIN')
        if c.execute('SELECT 1 FROM inventory_days WHERE day_id=?', (day_id,)).fetchone() is None:
            raise LookupError('Không tìm thấy ngày trong kho của bạn.')
        report = _build_report(c, day_id)
        row = next((x for x in report['items'] if x['marker_id'] == marker_id), None)
        if row is None:
            raise LookupError('Loại hàng không thuộc báo cáo này.')
        sessions = [dict(s) for s in c.execute('''SELECT s.session_id,s.name,ds.session_order,ds.finalized_at,
            st.quantity AS quantity FROM inventory_day_sessions ds
            JOIN inventory_sessions s ON s.session_id=ds.session_id
            LEFT JOIN inventory_session_stock st ON st.session_id=s.session_id AND st.marker_id=?
            WHERE ds.day_id=? AND ds.finalized_at IS NOT NULL AND s.status='closed'
            ORDER BY ds.session_order''', (marker_id, day_id)).fetchall()]
        previous = row['opening_quantity']
        for s in sessions:
            s['reference_quantity'] = previous
            s['difference'] = s['quantity'] - previous if s['quantity'] is not None and previous is not None else None
            previous = s['quantity']
        if not sessions:
            raise ValueError('Chưa có phiên đã chốt để truy vết.')
        selected = next((s for s in sessions if s['session_id'] == (session_id or sessions[-1]['session_id'])), None)
        if selected is None:
            raise LookupError('Phiên không thuộc ngày đã chọn hoặc chưa chốt.')
        if selected['quantity'] is None:
            raise ValueError('Loại hàng chưa có trong bản tồn phiên này hoặc snapshot bị thiếu. Chọn phiên khác; không tự coi là 0.')
        receipts = c.execute('''SELECT * FROM inventory_review_receipts WHERE session_id=?
            ORDER BY recorded_at,review_id''', (selected['session_id'],)).fetchall()
        result = []
        for receipt in receipts:
            try:
                doc = json.loads(receipt['reviewed_payload_json'])
                if (doc.get('review_id') != receipt['review_id']
                        or doc.get('revision') != receipt['review_revision']):
                    raise ValueError('Bản lưu không khớp biên nhận.')
                review = doc.get('human_review') or {}
                boxes = review.get('boxes', [])
                if not isinstance(boxes, list) or any(not isinstance(b, dict) for b in boxes):
                    raise ValueError('Không đọc được vùng hàng trong bản lưu.')
                matching = {b['box_id']: b for b in boxes if b.get('marker_id') == marker_id}
                if not matching:
                    if c.execute('SELECT COUNT(*) FROM inventory_box_instances WHERE review_id=? AND marker_id=?', (receipt['review_id'], marker_id)).fetchone()[0]:
                        raise ValueError('Bản lưu thiếu vùng hàng đã được ghi nhận.')
                    continue
                resolution = doc.get('duplicate_resolution') or {}
                decisions = resolution.get('decisions', [])
                if not isinstance(decisions, list) or any(not isinstance(d, dict) for d in decisions):
                    raise ValueError('Không đọc được quyết định đối chiếu.')
                details = []
                for decision in decisions:
                    if decision.get('box_id') not in matching:
                        continue
                    if decision.get('decision') not in ('new', 'duplicate'):
                        raise ValueError('Quyết định trong biên nhận không hợp lệ.')
                    d = {key: decision.get(key) for key in ('box_id', 'decision', 'reference_instance_id', 'note')}
                    d['bounds'] = matching[d['box_id']].get('bounds')
                    d['reference'] = None
                    if d['decision'] == 'duplicate':
                        ref = c.execute('''SELECT b.instance_id,b.source_box_id,b.marker_id,r.review_id,
                            r.image_url,r.review_revision,r.session_id FROM inventory_box_instances b
                            JOIN inventory_review_receipts r ON r.review_id=b.review_id
                            WHERE b.instance_id=? AND r.session_id=?''',
                            (d['reference_instance_id'], selected['session_id'])).fetchone()
                        if ref is not None and ref['marker_id'] == marker_id:
                            d['reference'] = {**dict(ref), **image(ref['image_url'])}
                    details.append(d)
                history = doc.get('correction_history', [])
                if not isinstance(history, list) or any(not isinstance(h, dict) for h in history):
                    raise ValueError('Không đọc được lịch sử hiệu chỉnh.')
                confirmation = doc.get('confirmation') or {}
                result.append({'review_id': receipt['review_id'], 'revision': receipt['review_revision'],
                    'recorded_at': receipt['recorded_at'], **image(receipt['image_url']),
                    'confirmed_by': actor(confirmation.get('confirmed_by')),
                    'confirmed_at': confirmation.get('confirmed_at'),
                    'decisions': details, 'check_id': resolution.get('check_id'),
                    'checked_at': resolution.get('checked_at'),
                    'new_count': c.execute('''SELECT COUNT(*) FROM inventory_box_instances
                        WHERE review_id=? AND marker_id=?''', (receipt['review_id'], marker_id)).fetchone()[0],
                    'correction_history': [{'revision': h.get('revision'), 'saved_at': h.get('saved_at'),
                        'reviewed_by': actor(h.get('reviewed_by')), 'boxes': [
                            {k: b.get(k) for k in ('box_id', 'marker_id', 'bounds', 'reviewed', 'note')}
                            for b in h.get('boxes', []) if isinstance(b, dict) and b.get('marker_id') == marker_id]}
                        for h in history]})
            except (TypeError, KeyError, AttributeError, json.JSONDecodeError) as error:
                raise ValueError('Không đọc được bản lưu biên nhận; không suy ra không có hàng.') from error
        counted = c.execute('''SELECT COUNT(*) FROM inventory_box_instances b
            JOIN inventory_review_receipts r ON r.review_id=b.review_id
            WHERE r.session_id=? AND b.marker_id=?''', (selected['session_id'], marker_id)).fetchone()[0]
        if counted != selected['quantity']:
            raise ValueError('Nguồn biên nhận không khớp số tồn đã chốt; cần kiểm tra dữ liệu.')
    return {'day_id': day_id, 'warehouse_id': current_warehouse_id(), 'business_date': report['business_date'],
        'marker_id': marker_id, 'product_name': row['product_name'], 'product_code': row['product_code'],
        'report_row': row, 'sessions': sessions, 'selected_session_id': selected['session_id'],
        'receipts': result[offset:offset + limit], 'total': len(result), 'limit': limit, 'offset': offset,
        'read_only': True, 'note': 'Mỗi phiên đếm lại toàn bộ tồn. Không cộng các phiên thành tồn cuối. '
            'Tồn đầu là số khởi tạo/kế thừa. Biên nhận cũ chưa lưu người duyệt sẽ hiển thị thiếu thông tin.'}
