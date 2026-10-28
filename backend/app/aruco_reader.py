"""Nhận dạng ArUco và xác nhận qua ba khung hình."""

from collections import defaultdict
from itertools import combinations

import cv2
import numpy as np


# Ngưỡng ban đầu để đối chiếu chuyển động giữa các khung hình.
# Cần đánh giá lại khi camera di chuyển thực tế.
MAX_CENTER_SHIFT = 1.5
MIN_SIZE_RATIO = 0.5


def create_detector():
    dictionary = cv2.aruco.getPredefinedDictionary(
        cv2.aruco.DICT_4X4_250
    )

    parameters = cv2.aruco.DetectorParameters()
    parameters.maxErroneousBitsInBorderRate = 0.0

    return cv2.aruco.ArucoDetector(dictionary, parameters)


def detect_frame(detector, frame, frame_index):
    """Đọc mọi marker trong một ảnh màu BGR."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None:
        return []

    height, width = frame.shape[:2]
    detections = []

    for marker_corners, marker_id in zip(corners, ids.flatten()):
        points = marker_corners.reshape(4, 2).astype(float)

        # Chuẩn hóa tọa độ theo kích thước ảnh.
        normalized = points / np.array([width, height], dtype=float)
        center = normalized.mean(axis=0)

        diagonal = float(
            (
                np.linalg.norm(normalized[0] - normalized[2])
                + np.linalg.norm(normalized[1] - normalized[3])
            ) / 2
        )

        detections.append({
            "id": int(marker_id),
            "frame_index": frame_index,
            "corners": points.tolist(),
            "center": center.tolist(),
            "diagonal": diagonal,
        })

    return detections


def positions_match(first, second):
    """Kiểm tra hai vùng cùng ID có phù hợp để đối chiếu không."""
    if first["frame_index"] == second["frame_index"]:
        return False

    first_size = first["diagonal"]
    second_size = second["diagonal"]
    largest = max(first_size, second_size)

    if largest <= 0:
        return False

    size_ratio = min(first_size, second_size) / largest

    distance = float(
        np.linalg.norm(
            np.array(first["center"]) - np.array(second["center"])
        )
    )

    return (
        size_ratio >= MIN_SIZE_RATIO
        and distance <= MAX_CENTER_SHIFT * largest
    )


def scan_three_frames(frames):
    """
    Nhận đúng ba khung hình màu BGR.

    Bên gọi phải bảo đảm đây là ba khung hình mới,
    cùng một phiên kết nối camera.
    """
    if len(frames) != 3:
        raise ValueError("Cần đúng ba khung hình.")

    for frame in frames:
        if (
            frame is None
            or frame.size == 0
            or frame.ndim != 3
            or frame.shape[2] != 3
        ):
            raise ValueError("Khung hình phải là ảnh màu BGR hợp lệ.")

    if any(frame.shape != frames[0].shape for frame in frames):
        raise ValueError("Ba khung hình phải có cùng kích thước.")

    detector = create_detector()
    frame_results = []
    observations = defaultdict(list)
    duplicate_ids = set()

    for frame_index, frame in enumerate(frames):
        detections = detect_frame(detector, frame, frame_index)
        frame_results.append(detections)

        counts = defaultdict(int)

        for detection in detections:
            marker_id = detection["id"]
            counts[marker_id] += 1
            observations[marker_id].append(detection)

        duplicate_ids.update(
            marker_id for marker_id, count in counts.items()
            if count > 1
        )

    confirmed = []
    pending = []

    for marker_id, detections in sorted(observations.items()):
        seen_in_frames = sorted({
            detection["frame_index"] for detection in detections
        })

        result = {
            "id": marker_id,
            "seen_in_frames": seen_in_frames,
            "seen_count": len(seen_in_frames),
        }

        if marker_id in duplicate_ids:
            pending.append({
                **result,
                "reason": "Một khung hình có nhiều vùng cùng ID.",
            })
            continue

        # Tìm nhóm lớn nhất có vị trí phù hợp giữa mọi cặp.
        matched_group = None

        for size in range(len(detections), 1, -1):
            for group in combinations(detections, size):
                if all(
                    positions_match(first, second)
                    for first, second in combinations(group, 2)
                ):
                    matched_group = group
                    break

            if matched_group is not None:
                break

        if matched_group is None:
            pending.append({
                **result,
                "reason": (
                    "Mới đọc được trong một khung hình."
                    if len(seen_in_frames) == 1
                    else "Vị trí hoặc kích thước thay đổi quá lớn."
                ),
            })
            continue

        # Chọn vùng mã lớn nhất trong nhóm đã đối chiếu làm minh chứng.
        # Đây không phải phép đo độ nét.
        evidence = max(
            matched_group,
            key=lambda detection: detection["diagonal"],
        )

        confirmed.append({
            **result,
            "matched_count": len(matched_group),
            "matched_frames": sorted(
                detection["frame_index"] for detection in matched_group
            ),
            "evidence_frame_index": evidence["frame_index"],
            "evidence_corners": evidence["corners"],
        })

    return {
        "frame_count": 3,
        "confirmed": confirmed,
        "pending": pending,
        "detections_by_frame": frame_results,
    }