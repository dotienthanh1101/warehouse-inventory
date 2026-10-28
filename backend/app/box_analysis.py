"""Marker-first proposals for arbitrary labeled packages; human review owns boundaries."""
import threading
from collections import Counter
import cv2
import numpy as np
import torch
from PIL import Image
from torchvision.ops import nms
from transformers import AutoConfig, AutoModelForZeroShotObjectDetection, AutoProcessor

MODEL_ID = 'IDEA-Research/grounding-dino-tiny'
# Shape-neutral proposals. No requirement that an item be a cardboard carton.
PROMPT = 'a package. a bag. a pouch. a box. a bottle. a container.'
_model = None
_processor = None
_inference_lock = threading.Lock()


def _propose(frame, diagnostics=None):
    global _model, _processor
    diagnostics = diagnostics if diagnostics is not None else []
    if _model is None:
        config = AutoConfig.from_pretrained(MODEL_ID)
        config.disable_custom_kernels = True
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        _processor = AutoProcessor.from_pretrained(MODEL_ID)
        _model = AutoModelForZeroShotObjectDetection.from_pretrained(
            MODEL_ID, config=config, use_safetensors=True).to(device).eval()
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    attempts = [
        (PROMPT, .35),
        ('a cardboard box. a paper box.', .25),
        ('a plastic bag. a paper bag. a pouch. a parcel.', .25),
        ('a bottle. a container.', .25),
    ]
    proposals, confidence = [], []
    for number, (prompt, threshold) in enumerate(attempts):
        inputs = _processor(images=image, text=prompt,
                            size={'shortest_edge': 640, 'longest_edge': 960},
                            return_tensors='pt').to(next(_model.parameters()).device)
        with torch.inference_mode():
            out = _model(**inputs)
        result = _processor.post_process_grounded_object_detection(
            out, inputs.input_ids, threshold=threshold, text_threshold=.20,
            target_sizes=[(image.height, image.width)])[0]
        boxes = result['boxes'].detach().float().cpu()
        scores = result['scores'].detach().float().cpu()
        boxes[:, 0::2] = boxes[:, 0::2].clamp(0, image.width)
        boxes[:, 1::2] = boxes[:, 1::2].clamp(0, image.height)
        valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
        boxes, scores = boxes[valid], scores[valid]
        diagnostics.append({'prompt': prompt, 'threshold': threshold,
                            'candidate_count': len(boxes),
                            'highest_model_score': round(float(out.logits.sigmoid().max().item()), 4)})
        proposals.extend(boxes.tolist())
        confidence.extend(scores.tolist())
        # Normal pass succeeds: retain existing fast path. If it finds nothing,
        # run all specialized fallback prompts so fallback is not box-only.
        if number == 0 and proposals:
            break
    if not proposals:
        return []
    boxes = torch.tensor(proposals, dtype=torch.float32)
    scores = torch.tensor(confidence, dtype=torch.float32)
    keep = nms(boxes, scores, .50)
    return [(boxes[i].tolist(), float(scores[i])) for i in keep]


def _contains(bounds, points):
    a, b, c, d = bounds
    return bool(np.all(points[:, 0] >= a) and np.all(points[:, 0] <= c)
                and np.all(points[:, 1] >= b) and np.all(points[:, 1] <= d))


def _touches(bounds, points):
    a, b, c, d = bounds
    rect = np.float32([[a,b],[c,b],[c,d],[a,d]])
    area, _ = cv2.intersectConvexConvex(rect, cv2.convexHull(points))
    return area > 0


def analyze_frame(frame):
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
        raise ValueError('Cần ảnh BGR uint8 ba kênh.')
    height, width = frame.shape[:2]
    params = cv2.aruco.DetectorParameters()
    params.maxErroneousBitsInBorderRate = 0.0
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250), params)
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    markers = [{'detection_id': f'marker-{i+1}', 'marker_id': int(mid),
                'corners': corner.reshape(4, 2).tolist(), 'suggested_box_id': None,
                'status': 'unassigned', 'warnings': []}
               for i, (mid, corner) in enumerate(zip(ids.flatten(), corners))] if ids is not None else []
    warnings = []
    diagnostics = []
    detection_error = None
    with _inference_lock:
        try:
            proposals = _propose(frame, diagnostics)
        except (RuntimeError, OSError, ValueError) as error:
            proposals = []
            detection_error = f'{type(error).__name__}: {error}'
            print('[package-detection]', detection_error, flush=True)
            warnings.append('Chưa đề xuất được vùng vật thể; hãy khoanh đơn vị hàng trên ảnh.')
    boxes = [{'box_id': f'box-{i+1}', 'bounds': bounds, 'score': score,
              'marker_ids': [], 'suggested_marker_id': None,
              'status': 'unresolved', 'warnings': []}
             for i, (bounds, score) in enumerate(sorted(proposals, key=lambda p:p[0][0]))]
    blocked = set()
    for marker in markers:
        points = np.float32(marker['corners'])
        containing = [i for i,b in enumerate(boxes) if _contains(b['bounds'], points)]
        touching = [i for i,b in enumerate(boxes) if _touches(b['bounds'], points)]
        if len(containing) == 1:
            owner = containing[0]
            boxes[owner]['marker_ids'].append(marker['marker_id'])
            marker['suggested_box_id'] = boxes[owner]['box_id']
            marker['status'] = 'assigned'
            if len(touching) > 1:
                message = 'Marker giao với vùng vật thể khác; kiểm tra đơn vị hàng sở hữu marker.'
                boxes[owner]['warnings'].append(message)
                marker['warnings'].append(message)
                marker['status'] = 'assigned_with_warning'
        else:
            blocked.update(touching)
            marker['warnings'].append('Chưa xác định ranh giới đơn vị hàng mang marker; hãy khoanh/gán thủ công.')
    for i, box in enumerate(boxes):
        if box['score'] < .35:
            box['warnings'].append('Vùng được tìm ở ngưỡng mở rộng; kiểm tra khung đề xuất.')
        mids = set(box['marker_ids'])
        if len(mids) == 1 and i not in blocked:
            box['suggested_marker_id'] = next(iter(mids))
            box['status'] = 'proposed_with_warning' if box['warnings'] else 'proposed'
        else:
            box['warnings'].append('Cần xác định mã và ranh giới của đơn vị hàng.')
    pending = sum(b['status'] != 'proposed' for b in boxes)
    if not boxes:
        warnings.append('Chưa có vùng đơn vị hàng. Khoanh toàn bộ từng vật, không chỉ riêng marker.')
    if pending:
        warnings.append(f'Có {pending} vùng đơn vị hàng cần kiểm tra.')
    if any(m['suggested_box_id'] is None for m in markers):
        warnings.append('Có marker chưa được gán vào đơn vị hàng.')
    counts = Counter(b['suggested_marker_id'] for b in boxes if b['suggested_marker_id'] is not None)
    return {'analysis_version': 'marker-package-v3',
            'detection_diagnostics': diagnostics, 'detection_error': detection_error, 'width': width, 'height': height,
            'review_status': 'draft', 'requires_attention': bool(warnings),
            'boxes': boxes, 'markers': markers, 'warnings': warnings,
            'summary': {'detected_boxes': len(boxes), 'detected_markers': len(markers),
                        'clear_proposals': sum(b['status']=='proposed' for b in boxes),
                        'warning_proposals': sum(b['status']=='proposed_with_warning' for b in boxes),
                        'unresolved_boxes': sum(b['status']=='unresolved' for b in boxes),
                        'proposed_counts_by_marker_id': dict(sorted(counts.items()))},
            'inventory_recorded': False}
