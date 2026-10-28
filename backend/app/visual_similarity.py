"""DINOv2 appearance + full-scene LoFTR, projected onto counted regions.

No inventory writes. Two models are held on CPU between calls; only one is
placed on CUDA at a time. Region boundaries come from the saved human review.
They are rectangles, not the hand-labelled silhouettes in the research trials.
"""
import logging
import math
import threading
from contextlib import contextmanager

import cv2
import numpy as np
from PIL import Image, ImageOps

LOG = logging.getLogger(__name__)
ENGINE_VERSION = 'dinov2-loftr-scene-regions-v2'
LOW_SIM = 0.40
HIGH_SIM = 0.75
EDGE = 640
MODEL_ID = 'facebook/dinov2-small'
_LOCK = threading.Lock()
_DINO = _PROCESSOR = _LOFTR = None


def choose_outcome(original_similarity, masked_similarity, geometry,
                   quality_ok=True, error=None):
    """Fixed trial policy, with a quality/error gate. Cosine is not probability."""
    if error or geometry is None or not quality_ok:
        return 'not_evaluable', 'Chưa đủ dữ liệu đáng tin cậy; cần kiểm tra.'
    if not all(type(v) in (int, float) and math.isfinite(v)
               for v in (original_similarity, masked_similarity)):
        return 'not_evaluable', 'Thiếu đặc trưng ảnh hợp lệ.'
    if geometry['outcome'] == 'suspected_duplicate':
        return 'suspected_duplicate', 'Có đặc điểm tương ứng trên vật; kiểm tra hai ảnh.'
    if min(original_similarity, masked_similarity) >= HIGH_SIM:
        return 'suspected_duplicate', 'Ngoại quan giống nhau; cần xác nhận có cùng kiện hay không.'
    if geometry['inliers'] >= 12:
        return 'not_evaluable', 'Có điểm tương ứng trên vật; chưa tự tính kiện mới.'
    if max(original_similarity, masked_similarity) <= LOW_SIM:
        return 'new_candidate', 'Ngoại quan khác rõ theo ngưỡng thử; tự chọn kiện mới.'
    return 'not_evaluable', 'Chưa phân biệt chắc chắn; chọn cùng kiện hoặc kiện mới.'


def bounds(value, width, height):
    if isinstance(value, dict):
        value = value.get('bounds')
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or not all(type(v) in (int, float) and math.isfinite(v) for v in value)):
        raise ValueError('Tọa độ vùng ảnh không hợp lệ.')
    x1, y1, x2, y2 = value
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError('Vùng ảnh nằm ngoài ảnh gốc.')
    return x1, y1, x2, y2


def prepare_scene(image, markers, exclusions):
    """Mask camera overlay in both branches, markers only in masked branch."""
    h, w = image.shape[:2]
    valid = np.full((h, w), 255, np.uint8)
    valid[:max(1, math.ceil(h * 70 / 1080)), :max(1, math.ceil(w * 330 / 1920))] = 0
    masked_valid = valid.copy()
    auto = manual = 0

    def erase(rect, pad):
        x1, y1, x2, y2 = bounds(rect, w, h)
        left, top = max(0, math.floor(x1-pad)), max(0, math.floor(y1-pad))
        right, bottom = min(w, math.ceil(x2+pad)), min(h, math.ceil(y2+pad))
        masked_valid[top:bottom, left:right] = 0

    for marker in markers:
        pts = np.asarray(marker.get('corners'), dtype=float)
        if pts.shape != (4, 2) or not np.isfinite(pts).all():
            raise ValueError('Tọa độ marker không hợp lệ.')
        xs, ys = pts[:, 0], pts[:, 1]
        erase([float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
              max(8, .15 * max(float(np.ptp(xs)), float(np.ptp(ys)))))
        auto += 1
    for exclusion in exclusions:
        erase(exclusion, 8)
        manual += 1

    scaled_w = EDGE
    scaled_h = max(8, int(h * EDGE / w) // 8 * 8)
    small = cv2.resize(image, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)
    scenes = {}
    for name, mask in [('original', valid), ('masked', masked_valid)]:
        small_mask = cv2.resize(mask, (scaled_w, scaled_h), interpolation=cv2.INTER_NEAREST)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray[small_mask == 0] = 127
        scenes[name] = {'gray': gray, 'valid': small_mask}
    return {'image': image, 'valid': valid, 'masked_valid': masked_valid,
            'scenes': scenes, 'scale': np.array([scaled_w/w, scaled_h/h], np.float32),
            'auto_mask_count': auto, 'manual_mask_count': manual}


def appearance_views(scene, box):
    image = scene['image']
    h, w = image.shape[:2]
    x1, y1, x2, y2 = bounds(box, w, h)
    left, top, right, bottom = math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2)
    if min(right-left, bottom-top) < 20:
        raise ValueError('Vùng đơn vị hàng quá nhỏ để đối chiếu.')
    crop = image[top:bottom, left:right].copy()
    orig_mask = scene['valid'][top:bottom, left:right]
    mask = scene['masked_valid'][top:bottom, left:right].copy()
    border = max(2, round(min(mask.shape) * .03))
    mask[:border, :] = mask[-border:, :] = 0
    mask[:, :border] = mask[:, -border:] = 0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    visible = gray[mask > 0]
    fraction = float(np.count_nonzero(mask) / mask.size)
    # Conservative gates; not calibrated camera-quality probabilities.
    quality_ok = (len(visible) >= 100 and fraction >= .20
                  and float(np.std(visible)) >= 8
                  and float(np.mean(visible < 8)) < .85
                  and float(np.mean(visible > 247)) < .85)
    scale = min(1.0, 320/max(gray.shape))
    qgray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    qmask = cv2.resize(mask, (qgray.shape[1], qgray.shape[0]), interpolation=cv2.INTER_NEAREST)
    lap = cv2.Laplacian(qgray, cv2.CV_32F)
    if np.count_nonzero(qmask):
        quality_ok = quality_ok and float(np.var(lap[qmask > 0])) >= 20
    else:
        quality_ok = False
    original = crop.copy()
    original[orig_mask == 0] = 127
    masked = crop.copy()
    masked[mask == 0] = 127
    views = {}
    for branch, bgr in [('original', original), ('masked', masked)]:
        rgb = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        views[branch] = ImageOps.pad(rgb, (224, 224), method=Image.Resampling.BICUBIC,
                                    color=(127, 127, 127))
    if len(visible) < 100 or fraction < .20:
        reason = 'Sau khi che mã, còn quá ít diện tích bề mặt để đối chiếu.'
    elif float(np.std(visible)) < 8:
        reason = 'Sau khi che mã, bề mặt gần như trống hoặc không có đủ chi tiết.'
    elif float(np.mean(visible < 8)) >= .85 or float(np.mean(visible > 247)) >= .85:
        reason = 'Bề mặt quá tối hoặc quá sáng để đối chiếu.'
    elif not quality_ok:
        reason = 'Bề mặt thiếu chi tiết rõ nét để đối chiếu.'
    else:
        reason = ''
    return views, {'quality_ok': bool(quality_ok), 'visible_fraction': round(fraction, 3),
                   'reason': reason}


def _dependencies():
    try:
        import truststore
        truststore.inject_into_ssl()
        import torch
        return torch
    except ImportError as exc:
        raise ValueError('Thiếu thư viện so sánh. Chạy kiểm tra môi trường trong gói cập nhật.') from exc


def _model(kind):
    global _DINO, _PROCESSOR, _LOFTR
    if kind == 'dino':
        if _DINO is None:
            from transformers import AutoImageProcessor, AutoModel
            LOG.info('Loading DINOv2-Small for duplicate checks')
            processor = AutoImageProcessor.from_pretrained(MODEL_ID, use_fast=False)
            model = AutoModel.from_pretrained(MODEL_ID, use_safetensors=True).eval()
            _PROCESSOR, _DINO = processor, model
        return _DINO
    if _LOFTR is None:
        from kornia.feature import LoFTR
        LOG.info('Loading LoFTR indoor for duplicate checks')
        _LOFTR = LoFTR(pretrained='indoor').eval()
    return _LOFTR


class SimilarityEngine:
    """Used while holding comparison_engine(). No GPU model retained between calls."""
    def __init__(self):
        self.torch = _dependencies()
        self.devices = set()

    def _infer(self, kind, operation):
        torch = self.torch
        try:
            model = _model(kind)
            free = torch.cuda.mem_get_info()[0] if torch.cuda.is_available() else 0
            device = 'cuda' if free >= (1300 if kind == 'loftr' else 450)*1024**2 else 'cpu'
            try:
                model.to(device)
                with torch.inference_mode():
                    result = operation(model, device)
                self.devices.add(device)
                return result
            except torch.cuda.OutOfMemoryError:
                LOG.warning('GPU full during %s; retrying on CPU', kind)
                model.to('cpu')
                torch.cuda.empty_cache()
                with torch.inference_mode():
                    result = operation(model, 'cpu')
                self.devices.add('cpu')
                return result
            finally:
                model.to('cpu')
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
        except Exception as exc:
            LOG.exception('Visual comparison model failed: %s', kind)
            raise ValueError(f'Không chạy được {kind}; chưa tự tính kiện mới. Xem cửa sổ backend.') from exc

    def embed(self, scene, boxes):
        prepared = {box['box_id']: appearance_views(scene, box) for box in boxes}
        torch = self.torch

        def run(model, device):
            features = {}
            for box_id, (views, quality) in prepared.items():
                features[box_id] = {'quality': quality}
                for branch, image in views.items():
                    inputs = _PROCESSOR(images=image, do_resize=False, do_center_crop=False,
                                        return_tensors='pt')
                    inputs = {k: v.to(device) for k, v in inputs.items()}
                    result = model(**inputs)
                    vector = result.last_hidden_state[:, 0, :].float()
                    norm = vector.norm(dim=-1, keepdim=True)
                    if not torch.isfinite(vector).all().item() or norm.item() <= 1e-8:
                        raise ValueError('Đặc trưng DINOv2 không hợp lệ.')
                    features[box_id][branch] = (vector/norm).cpu().numpy()[0]
                    del inputs, result, vector
            return features
        return self._infer('dino', run)

    def match_scenes(self, new, old):
        torch = self.torch

        def run(model, device):
            matches = {}
            for branch in ('original', 'masked'):
                a, b = new['scenes'][branch], old['scenes'][branch]
                tensors = {f'image{i}': torch.from_numpy(d['gray'].astype(np.float32)/255)[None, None].to(device)
                           for i, d in enumerate((a, b))}
                result = model(tensors)
                p = result['keypoints0'].cpu().numpy().copy()
                q = result['keypoints1'].cpu().numpy().copy()
                confidence = result['confidence'].cpu().numpy()
                good = (confidence >= .5) & inside(p, a['valid']) & inside(q, b['valid'])
                matches[branch] = (p[good], q[good])
                del tensors, result
            return matches
        return self._infer('loftr', run)


@contextmanager
def comparison_engine():
    with _LOCK:
        yield SimilarityEngine()


def inside(points, mask):
    if not len(points):
        return np.zeros(0, bool)
    xy = np.rint(points).astype(int)
    valid = (xy[:, 0] >= 0) & (xy[:, 0] < mask.shape[1]) & (xy[:, 1] >= 0) & (xy[:, 1] < mask.shape[0])
    found = np.zeros(len(points), bool)
    found[valid] = mask[xy[valid, 1], xy[valid, 0]] > 0
    return found


def region_mask(scene, box, branch):
    h, w = scene['image'].shape[:2]
    x1, y1, x2, y2 = bounds(box, w, h)
    mask = np.zeros((h, w), np.uint8)
    mask[math.ceil(y1):math.floor(y2), math.ceil(x1):math.floor(x2)] = 255
    mask &= scene['valid'] if branch == 'original' else scene['masked_valid']
    small = scene['scenes'][branch]['gray']
    mask = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    return cv2.erode(mask, np.ones((5, 5), np.uint8))


def geometric_evidence(p, q, mask_a, mask_b):
    keep = inside(p, mask_a) & inside(q, mask_b)
    p, q = p[keep], q[keep]
    flags = np.zeros(len(p), bool)
    if len(p) >= 4:
        cv2.setRNGSeed(123)
        try:
            matrix, status = cv2.findHomography(p, q, cv2.RANSAC, 3.0)
            if matrix is not None and np.isfinite(matrix).all() and status is not None:
                flags = status.ravel().astype(bool)
        except cv2.error:
            pass
    count = int(flags.sum())
    ratio = count / max(1, len(p))
    coverage = []
    for pts, mask in ((p[flags], mask_a), (q[flags], mask_b)):
        area = np.zeros_like(mask)
        if len(pts) >= 3:
            cv2.fillConvexPoly(area, cv2.convexHull(pts.astype(np.float32)).astype(np.int32), 255)
        coverage.append(float(np.count_nonzero(area & mask) / max(1, np.count_nonzero(mask))))
    suspected = count >= 12 and ratio >= .5 and min(coverage) >= .08
    # Endpoints are returned in scene coordinates for inspection on the original image.
    return {'outcome': 'suspected_duplicate' if suspected else 'insufficient_evidence',
            'mutual_matches': len(p), 'inliers': count, 'inlier_ratio': round(ratio, 4),
            'coverage_new': round(coverage[0], 4), 'coverage_recorded': round(coverage[1], 4),
            'comparison_performed': True, 'method': 'LoFTR',
            'matched_points_new': p[flags][:60].tolist(),
            'matched_points_recorded': q[flags][:60].tolist()}


def compare_regions(new, old, box, reference, new_features, old_features, matches):
    branches = {branch: geometric_evidence(*matches[branch],
                 region_mask(new, box, branch), region_mask(old, reference, branch))
                for branch in ('original', 'masked')}
    original = float(np.clip(np.dot(new_features['original'], old_features['original']), -1, 1))
    masked = float(np.clip(np.dot(new_features['masked'], old_features['masked']), -1, 1))
    quality_ok = new_features['quality']['quality_ok'] and old_features['quality']['quality_ok']
    outcome, note = choose_outcome(original, masked, branches['masked'], quality_ok=quality_ok)
    if not quality_ok:
        failed = []
        for label, feature in [('Ảnh mới', new_features), ('Ảnh trước', old_features)]:
            if not feature['quality']['quality_ok']:
                failed.append(label + ': ' + feature['quality'].get('reason', 'Không đủ bề mặt để đối chiếu.'))
        note = ' '.join(failed) + ' Đây là thiếu dữ liệu, không phải kết luận nghi trùng.'
    # Original matches on a shared type marker must NOT veto every new package.
    # Only original-image endpoints outside all known labels can block auto-new.
    original_surface = geometric_evidence(*matches['original'],
        region_mask(new, box, 'masked'), region_mask(old, reference, 'masked'))
    if outcome == 'new_candidate' and original_surface['inliers'] >= 12:
        outcome, note = 'not_evaluable', 'Ảnh gốc có điểm khớp; cần kiểm tra ảnh đã che marker.'
    return {**branches['masked'], 'outcome': outcome, 'note': note,
            'branches': branches, 'original_nonlabel_geometry': original_surface,
            'evaluation_status': 'usable' if quality_ok else 'insufficient_surface',
            'appearance': {'original': original, 'masked': masked},
            'quality': {'new': new_features['quality'], 'recorded': old_features['quality']},
            'scene_matches': {key: len(value[0]) for key, value in matches.items()},
            'matching_policy': 'full_scene_then_region_endpoint_filter',
            'mask_warning': None if new['auto_mask_count']+new['manual_mask_count'] and old['auto_mask_count']+old['manual_mask_count']
                            else 'Có ảnh chưa phát hiện marker; nhãn chưa đọc được cần che thủ công nếu có.',
            'point_coordinate_scale': {'new': new['scale'].tolist(), 'recorded': old['scale'].tolist()}}


def main():
    """Preload/check dependencies in an independent process, without a database."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-models', action='store_true', required=True)
    parser.parse_args()
    with comparison_engine() as engine:
        print(f'PyTorch: {engine.torch.__version__}', flush=True)
        for kind in ('dino', 'loftr'):
            print(f'Loading {kind} on CPU for readiness check...', flush=True)
            _model(kind)
            print(f'{kind}: ready', flush=True)
    print('Models ready. No inventory data read or written.', flush=True)


if __name__ == '__main__':
    main()
