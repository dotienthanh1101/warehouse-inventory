"""Read previously counted boxes and their immutable SQLite evidence."""
import json
import hashlib
from pathlib import Path
from backend.app.database import get_connection

from backend.app.warehouse_scope import ScopedPath
REVIEW_DIR = ScopedPath('data', 'reviews')


def draft_history(session_id, current):
    """All earlier capture documents bound to this session, no top-k cutoff."""
    documents, heads = [], []
    for path in sorted(REVIEW_DIR.glob('*.json')):
        try:
            raw = path.read_bytes()
            doc = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise ValueError(f'Không đọc được lịch sử {path.name}; chưa thể đối chiếu đủ phiên.') from exc
        if doc.get('session_id') != session_id or doc.get('review_id') == current['review_id']:
            continue
        if doc.get('created_at', '') > current.get('created_at', ''):
            continue
        heads.append({'review_id': doc['review_id'], 'sha256': hashlib.sha256(raw).hexdigest()})
        documents.append(doc)
    return documents, heads


def load_session_history(session_id, current):
    # Reuse open-session checks, but do not restrict history to the same marker ID.
    data = load_reference_boxes(session_id, range(250))
    documents, heads = draft_history(session_id, current)
    with get_connection() as connection:
        receipts = connection.execute(
            'SELECT * FROM inventory_review_receipts WHERE session_id = ?',
            (session_id,),
        ).fetchall()
    receipt_map = {r['review_id']: r for r in receipts}
    refs_by_box = {(r['review_id'], r['source_box_id']): r['instance_id']
                   for r in data['references']}
    docs = {d['review_id']: d for d in documents}
    # Recorded snapshots are authoritative, including observations confirmed as repeats.
    for r in receipts:
        doc = json.loads(r['reviewed_payload_json'])
        if doc['review_id'] != current['review_id'] and doc.get('created_at', '') <= current.get('created_at', ''):
            docs[doc['review_id']] = doc
    references, issues = [], []
    for doc in sorted(docs.values(), key=lambda d: (d.get('created_at', ''), d['review_id'])):
        human = doc.get('human_review') or {}
        analysis = doc.get('analysis') or {}
        boxes = human.get('boxes') if human else analysis.get('boxes', [])
        if not boxes:
            issues.append(f"Ảnh {doc['review_id'][:8]} chưa có vùng đơn vị hàng để so sánh.")
            continue
        decisions = {d['box_id']: d for d in (doc.get('duplicate_resolution') or {}).get('decisions', [])}
        for box in boxes:
            instance = refs_by_box.get((doc['review_id'], box['box_id']))
            if instance is None:
                instance = decisions.get(box['box_id'], {}).get('reference_instance_id')
            references.append({
                'instance_id': instance,
                'comparison_id': doc['review_id'] + ':' + box['box_id'],
                'review_id': doc['review_id'], 'source_box_id': box['box_id'],
                'marker_id': box.get('marker_id', box.get('suggested_marker_id')),
                'bounds': box['bounds'], 'image_url': doc['image_url'],
                'width': doc['width'], 'height': doc['height'],
                'recorded_at': receipt_map[doc['review_id']]['recorded_at'] if doc['review_id'] in receipt_map else doc.get('created_at'),
                'reference_state': 'recorded' if instance else 'draft',
                'image_markers': analysis.get('markers', []),
                'excluded_regions': human.get('excluded_regions', []),
            })
    return {**data, 'references': references, 'reference_count': len(references),
            'history_heads': heads, 'history_issues': issues, 'capture_count': len(docs),
            'history_documents': list(docs.values())}


def reference_rows(connection, session_id, marker_ids):
    requested = set(marker_ids)
    receipts = connection.execute("""
        SELECT review_id, review_revision, recorded_at, image_url,
               image_width, image_height, reviewed_payload_json
        FROM inventory_review_receipts WHERE session_id = ?
        ORDER BY recorded_at, review_id
    """, (session_id,)).fetchall()
    instances = connection.execute("""
        SELECT b.instance_id, b.review_id, b.source_box_id, b.marker_id,
               b.x1, b.y1, b.x2, b.y2, b.product_code, b.product_name
        FROM inventory_box_instances b
        JOIN inventory_review_receipts r ON r.review_id = b.review_id
        WHERE r.session_id = ? ORDER BY r.recorded_at, b.review_id, b.instance_id
    """, (session_id,)).fetchall()
    receipt_by_id = {r['review_id']: r for r in receipts}
    snapshots = {}
    references = []
    for box in instances:
        if box['marker_id'] not in requested:
            continue
        r = receipt_by_id[box['review_id']]
        if r['review_id'] not in snapshots:
            try:
                snapshot = json.loads(r['reviewed_payload_json'])
                markers = snapshot['analysis']['markers']
                exclusions = (snapshot.get('human_review') or {}).get('excluded_regions', [])
                if not isinstance(markers, list) or not isinstance(exclusions, list):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError(f"Dữ liệu đối chiếu {r['review_id']} không hợp lệ.") from exc
            snapshots[r['review_id']] = (markers, exclusions)
        markers, exclusions = snapshots[r['review_id']]
        references.append({
            'instance_id': box['instance_id'], 'review_id': box['review_id'],
            'source_box_id': box['source_box_id'], 'marker_id': box['marker_id'],
            'product_code': box['product_code'], 'product_name': box['product_name'],
            'bounds': [box[k] for k in ('x1', 'y1', 'x2', 'y2')],
            'image_url': r['image_url'], 'width': r['image_width'],
            'height': r['image_height'], 'recorded_at': r['recorded_at'],
            'image_markers': markers, 'excluded_regions': exclusions,
        })
    receipt_heads = [{'review_id': r['review_id'], 'revision': r['review_revision']} for r in receipts]
    return references, receipt_heads


def load_reference_boxes(session_id, marker_ids):
    if not isinstance(session_id, str) or not session_id:
        raise ValueError('Thiếu mã phiên kiểm kê.')
    requested = set(marker_ids)
    if any(type(marker_id) is not int or not 0 <= marker_id <= 249 for marker_id in requested):
        raise ValueError('Danh sách mã loại hàng không hợp lệ.')
    with get_connection() as connection:
        connection.execute('BEGIN')
        session = connection.execute("""
            SELECT s.session_id, s.name, s.status, ds.day_id, ds.finalized_at,
                   d.status AS day_status
            FROM inventory_sessions s LEFT JOIN inventory_day_sessions ds
              ON ds.session_id = s.session_id
            LEFT JOIN inventory_days d ON d.day_id = ds.day_id
            WHERE s.session_id = ?
        """, (session_id,)).fetchone()
        if session is None:
            raise ValueError('Không tìm thấy phiên kiểm kê.')
        if (session['status'] != 'open' or session['day_id'] is None
                or session['day_status'] != 'open' or session['finalized_at'] is not None):
            raise ValueError('Cần chọn phiên theo ngày đang mở.')
        references, heads = reference_rows(connection, session_id, requested)
    return {'session_id': session_id, 'session_name': session['name'],
            'reference_count': len(references), 'references': references,
            'reference_receipts': heads, 'receipt_count': len(heads),
            'inventory_modified': False}
