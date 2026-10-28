"""Cross-image inventory review: appearance + geometry, no automatic merging."""
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import cv2
import numpy as np
from backend.app.duplicate_repository import load_session_history, draft_history
from backend.app.box_inventory_service import validate_review
from backend.app.visual_similarity import (
    ENGINE_VERSION, LOW_SIM, HIGH_SIM, MODEL_ID,
    comparison_engine, prepare_scene, compare_regions,
)

ROOT = Path(__file__).resolve().parents[2]
from backend.app.warehouse_scope import ScopedPath
CAPTURE_DIR = ScopedPath('captures')
CHECK_DIR = ScopedPath('data', 'duplicate_checks')

def _canonical(data):

    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def review_fingerprint(document):

    keys = ('review_id', 'revision', 'session_id', 'image_url', 'width', 'height', 'analysis', 'human_review')

    return hashlib.sha256(_canonical({k: document.get(k) for k in keys}).encode()).hexdigest()

def _image_path(url):

    if not isinstance(url, str) or not url.startswith('/api/captures/'):

        raise ValueError('Đường dẫn ảnh đối chiếu không hợp lệ.')

    name = url[len('/api/captures/'):]

    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png)', name):

        raise ValueError('Tên ảnh đối chiếu không hợp lệ.')

    return CAPTURE_DIR / name

def _load(url, width, height):

    path = _image_path(url)

    try:

        raw = np.fromfile(str(path), dtype=np.uint8)

        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)

    except (OSError, cv2.error, ValueError) as exc:

        raise ValueError(f'Không đọc được ảnh {path.name}.') from exc

    if image is None or image.shape[:2] != (height, width):

        raise ValueError(f'Ảnh {path.name} bị thiếu hoặc sai kích thước.')

    return image

def _check_path(check_id):

    if not isinstance(check_id, str) or re.fullmatch(r'[0-9a-f]{32}', check_id) is None:

        raise ValueError('Mã lượt đối chiếu không hợp lệ.')

    return CHECK_DIR / f'{check_id}.json'

def save_check(report):

    CHECK_DIR.mkdir(parents=True, exist_ok=True)

    target = _check_path(report['check_id'])

    temp = target.with_suffix(f'.{uuid4().hex}.tmp')

    try:

        with temp.open('w', encoding='utf-8') as stream:

            json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)

            stream.flush(); os.fsync(stream.fileno())

        os.replace(temp, target)

    finally:

        temp.unlink(missing_ok=True)

def load_check(check_id):

    try:

        return json.loads(_check_path(check_id).read_text(encoding='utf-8'))

    except (OSError, json.JSONDecodeError) as exc:

        raise ValueError('Không tìm thấy lượt đối chiếu. Hãy đối chiếu lại.') from exc

def _failed(note):
    return {'outcome': 'not_evaluable', 'note': note,
            'comparison_performed': False, 'inliers': 0, 'mutual_matches': 0,
            'coverage_new': 0.0, 'coverage_recorded': 0.0, 'branches': {},
            'appearance': None, 'evaluation_status': 'comparison_error'}

def _base(ref, box):
    return {
        'comparison_id': ref['comparison_id'],
        'reference_instance_id': ref['instance_id'],
        'reference_review_id': ref['review_id'],
        'reference_box_id': ref['source_box_id'],
        'reference_marker_id': ref['marker_id'],
        'reference_state': ref['reference_state'],
        'reference_recorded_at': ref['recorded_at'],
        'reference_image_url': ref['image_url'],
        'reference_width': ref['width'], 'reference_height': ref['height'],
        'reference_bounds': ref['bounds'],
        'can_mark_duplicate': bool(ref['instance_id'] and ref['marker_id'] == box['marker_id']),
    }

def _comparison_rank(c):
    appearance = c.get('appearance') or {}
    return (c['outcome'] == 'suspected_duplicate',
            c['outcome'] == 'not_evaluable',
            max(appearance.values(), default=-1), c.get('inliers', 0),
            c.get('reference_recorded_at') or '', c['comparison_id'])

def create_check(session_id, document):
    validate_review(document)
    if document.get('session_id') not in (None, session_id):
        raise ValueError('Bản nháp thuộc phiên khác.')
    references = load_session_history(session_id, document)
    allrefs = list(references['references'])
    boxes = document['human_review']['boxes']
    comparisons = {box['box_id']: [] for box in boxes}
    # Do not truncate references or use top-k: every relevant image in the session is visited.
    groups = defaultdict(list)
    for ref in allrefs:
        groups[(ref['review_id'], ref['image_url'])].append(ref)
    devices = []
    if allrefs and boxes:
        original = _load(document['image_url'], document['width'], document['height'])
        current = prepare_scene(original, document['analysis']['markers'],
                                document['human_review'].get('excluded_regions', []))
        with comparison_engine() as engine:
            features = engine.embed(current, boxes)
            for group in groups.values():
                try:
                    ref0 = group[0]
                    image = _load(ref0['image_url'], ref0['width'], ref0['height'])
                    old = prepare_scene(image, ref0['image_markers'], ref0['excluded_regions'])
                    old_boxes = [{'box_id': r['comparison_id'], 'bounds': r['bounds']} for r in group]
                    old_features = engine.embed(old, old_boxes)
                    matches = engine.match_scenes(current, old)
                    for box in boxes:
                        for ref in group:
                            try:
                                measured = compare_regions(current, old, box, ref,
                                    features[box['box_id']], old_features[ref['comparison_id']], matches)
                                if measured['outcome'] == 'suspected_duplicate' and ref['marker_id'] != box['marker_id']:
                                    measured['note'] += ' Mã loại hàng khác; kiểm tra mã trước khi chọn cùng kiện.'
                            except ValueError as exc:
                                measured = _failed(str(exc))
                            comparisons[box['box_id']].append({**_base(ref, box), **measured})
                except ValueError as exc:
                    # Missing images, unavailable models and unusable crops must never mean new.
                    for box in boxes:
                        comparisons[box['box_id']].extend({**_base(r, box), **_failed(str(exc))} for r in group)
            devices = sorted(engine.devices)
    results = []
    for box in boxes:
        measured = comparisons[box['box_id']]
        measured.sort(key=_comparison_rank, reverse=True)
        complete = len(measured) == len(allrefs) and not references['history_issues']
        attention = not complete or any(c['outcome'] != 'new_candidate' for c in measured)
        results.append({'box_id': box['box_id'], 'marker_id': box['marker_id'],
            'bounds': box['bounds'], 'comparisons': measured,
            'requires_human_decision': attention, 'default_decision': 'pending' if attention else 'new',
            'history_complete': complete,
            'decision_reason': 'Cần chọn cách tính.' if attention else
                ('Chưa có ảnh trước trong phiên.' if not measured else 'Đã xét toàn bộ ảnh trước; đủ điều kiện đề xuất mới.')})
    report = {
        'check_id': uuid4().hex, 'session_id': session_id,
        'review_id': document['review_id'], 'review_revision': document['revision'],
        'review_fingerprint': review_fingerprint(document),
        'reference_receipts': references['reference_receipts'],
        'image_url': document['image_url'], 'image_width': document['width'], 'image_height': document['height'],
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'comparison_version': ENGINE_VERSION,
        'history_heads': references['history_heads'], 'history_issues': references['history_issues'],
        'capture_count': references['capture_count'], 'reference_image_count': len(groups),
        'image_policy': 'one_representative_per_capture',
        'reference_count': len(allrefs), 'reference_receipt_count': references['receipt_count'],
        'thresholds': {'low': LOW_SIM, 'high': HIGH_SIM},
        'model_id': MODEL_ID, 'devices_used': devices,
        'matching_policy': 'original_and_masked_full_scene_then_region_endpoint_filter',
        'appearance_policy': 'saved_rectangular_region_original_and_marker_masked',
        'results': results, 'inventory_modified': False,
        'note': 'Tự chọn mới khi đủ điều kiện; nghi trùng hoặc chưa rõ cần giám sát. Không tự gộp kiện.'}
    save_check(report)
    return report

def current_reference_heads(connection, session_id):

    return [{'review_id': r['review_id'], 'revision': r['review_revision']}

            for r in connection.execute("""

                SELECT review_id, review_revision FROM inventory_review_receipts

                WHERE session_id = ? ORDER BY recorded_at, review_id

            """, (session_id,)).fetchall()]

def validate_decisions(connection, session_id, document, check_id, decisions):

    report = load_check(check_id)

    if report.get('session_id') != session_id or report.get('review_id') != document['review_id']:

        raise ValueError('Lượt đối chiếu thuộc bản nháp hoặc phiên khác.')

    if report.get('review_fingerprint') != review_fingerprint(document):

        raise ValueError('Bản nháp đã đổi sau khi đối chiếu. Hãy đối chiếu lại.')

    if report.get('reference_receipts') != current_reference_heads(connection, session_id):

        raise ValueError('Phiên đã ghi thêm thùng sau lần đối chiếu. Hãy đối chiếu lại.')

    if report.get('comparison_version') != ENGINE_VERSION:

        raise ValueError('Quy tắc đối chiếu đã đổi. Hãy đối chiếu lại.')

    if report.get('history_heads') != draft_history(session_id, document)[1]:

        raise ValueError('Lịch sử bản nháp đã đổi. Hãy đối chiếu lại.')

    by_box = {r['box_id']: r for r in report['results']}

    if not isinstance(decisions, list) or len(decisions) != len(by_box):

        raise ValueError('Cần quyết định cho từng thùng trước khi ghi.')

    checked = {}

    used_old = set()

    for decision in decisions:

        if not isinstance(decision, dict):

            raise ValueError('Quyết định thùng không hợp lệ.')

        box_id = decision.get('box_id')

        kind = decision.get('decision')

        ref_id = decision.get('reference_instance_id')

        if box_id not in by_box or box_id in checked or kind not in ('new', 'duplicate'):

            raise ValueError('Quyết định thùng thiếu, lặp hoặc chưa hoàn tất.')

        candidates = {c['reference_instance_id'] for c in by_box[box_id]['comparisons']

                      if c.get('can_mark_duplicate')}

        if kind == 'duplicate':

            if ref_id not in candidates or ref_id in used_old:

                raise ValueError('Thùng đối chiếu không hợp lệ hoặc được dùng hai lần.')

            used_old.add(ref_id)

        elif ref_id is not None:

            raise ValueError('Thùng mới không được gán với thùng cũ.')

        note = decision.get('note', '')

        if not isinstance(note, str) or len(note) > 500:

            raise ValueError('Ghi chú đối chiếu không hợp lệ.')

        if kind == 'new' and by_box[box_id]['requires_human_decision'] and not note.strip():

            raise ValueError('Ảnh cần kiểm tra: ghi lý do xác nhận đây là kiện mới.')

        if kind == 'duplicate' and not note.strip():

            raise ValueError('Ghi lý do xác nhận là cùng thùng đã ghi.')

        checked[box_id] = {'box_id': box_id, 'decision': kind,

                           'reference_instance_id': ref_id if kind == 'duplicate' else None,

                           'note': note.strip()}

    if set(checked) != set(by_box):

        raise ValueError('Còn thùng chưa quyết định.')

    return report, [checked[box['box_id']] for box in document['human_review']['boxes']]
