"""API chụp, hiệu chỉnh và ghi nhận từng thùng vào phiên kiểm kê."""

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4
from backend.app.duplicate_check import create_check, review_fingerprint, current_reference_heads
from backend.app.database import get_connection

from fastapi import HTTPException, Request, Response
from backend.app.inventory_provenance import actor_snapshot
from pydantic import BaseModel, ConfigDict, Field

from backend.app.main import (
    app, camera, capture_lock, CAPTURE_DIR, ROOT,
    CaptureOptions, require_session, save_jpeg,
)
from backend.app.box_analysis import analyze_frame
from backend.app.box_inventory_service import (
    get_review_receipt,
    record_review,
    get_box_session_summary,
)


logger = logging.getLogger(__name__)
from backend.app.warehouse_scope import ScopedPath
REVIEW_DIR = ScopedPath("data", "reviews")

# Chạy một tiến trình backend như lệnh hướng dẫn.
review_lock = threading.Lock()


class CorrectedBox(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    box_id: str = Field(min_length=1, max_length=100)
    bounds: tuple[float, float, float, float]
    marker_id: int | None = Field(
        default=None, ge=0, le=249, strict=True
    )
    reviewed: bool = Field(default=False, strict=True)
    note: str = Field(default="", max_length=2000)


class ExcludedRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    region_id: str = Field(min_length=1, max_length=100)
    bounds: tuple[float, float, float, float]


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1, strict=True)
    boxes: list[CorrectedBox] = Field(max_length=500)
    assignments: dict[str, str]
    # Omitted by older clients: preserve stored exclusions. [] clears them.
    excluded_regions: list[ExcludedRegion] | None = Field(default=None, max_length=200)
    note: str = Field(default="", max_length=2000)


class DuplicateCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1, strict=True)


class BoxDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    box_id: str = Field(min_length=1, max_length=100)
    decision: str
    reference_instance_id: str | None = None
    note: str = Field(default="", max_length=500)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1, strict=True)

    # Người giám sát xác nhận đã xem kết quả đối chiếu và quyết định từng vùng.
    confirm_new_boxes: bool = Field(strict=True)
    check_id: str = Field(min_length=32, max_length=32)
    decisions: list[BoxDecision] = Field(max_length=500)


def review_path(review_id):
    if not re.fullmatch(r"[0-9a-f]{32}", review_id):
        raise HTTPException(404, "Không tìm thấy bản nháp.")
    return REVIEW_DIR / f"{review_id}.json"


def read_review(review_id):
    path = review_path(review_id)
    if not path.is_file():
        raise HTTPException(404, "Không tìm thấy bản nháp.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(500, "Không đọc được bản nháp.")


def save_review(document):
    destination = review_path(document["review_id"])
    temporary = destination.with_suffix(f".{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(document, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def with_receipt(document, receipt):
    """Trạng thái đã ghi nhận lấy từ SQLite, không phụ thuộc cờ JSON."""
    if receipt is not None:
        document["inventory_recorded"] = True
        document["review_status"] = "recorded"
        document["session_id"] = receipt["session_id"]
        document["receipt"] = receipt
        document["message"] = "Bản nháp đã được ghi vào phiên kiểm kê."
    return document


def validate_correction(document, correction):
    width, height = document["width"], document["height"]
    region_ids = set()
    for region in correction.excluded_regions or []:
        if region.region_id in region_ids:
            raise HTTPException(422, "Có hai vùng che trùng mã vùng.")
        region_ids.add(region.region_id)
        x1, y1, x2, y2 = region.bounds
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise HTTPException(422, "Tọa độ vùng che nằm ngoài ảnh hoặc đảo chiều.")

    boxes = {}

    for box in correction.boxes:
        if box.box_id in boxes:
            raise HTTPException(422, "Có hai vùng trùng box_id.")

        x1, y1, x2, y2 = box.bounds
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise HTTPException(
                422, f"Tọa độ {box.box_id} không hợp lệ."
            )

        if box.reviewed and box.marker_id is None:
            raise HTTPException(
                422, f"{box.box_id} đã kiểm tra nhưng chưa có ID."
            )

        boxes[box.box_id] = box

    markers = {
        marker["detection_id"]: marker
        for marker in document["analysis"]["markers"]
    }

    if set(correction.assignments) != set(markers):
        raise HTTPException(
            422, "Danh sách gán marker không khớp ảnh gốc."
        )

    for detection_id, owner in correction.assignments.items():
        if owner == "":
            continue
        if owner not in boxes:
            raise HTTPException(
                422, "Marker được gán vào vùng không tồn tại."
            )
        box = boxes[owner]
        if (
            box.reviewed
            and markers[detection_id]["marker_id"] != box.marker_id
        ):
            raise HTTPException(
                422, f"{owner}: ID đã duyệt khác marker đang gán."
            )


managed_routes = {
    ("/api/camera/capture", "POST"),
    ("/api/reviews/capture", "POST"),
    ("/api/reviews/{review_id}", "GET"),
    ("/api/reviews/{review_id}/correction", "PUT"),
    ("/api/reviews/{review_id}/confirm", "POST"),
    ("/api/reviews/{review_id}/duplicate-check", "POST"),
    ("/api/box-inventory/sessions/{session_id}", "GET"),
}

app.router.routes[:] = [
    route for route in app.router.routes
    if not any(
        (getattr(route, "path", None), method) in managed_routes
        for method in (getattr(route, "methods", None) or set())
    )
]


@app.post("/api/camera/capture", tags=["Review"])
def old_capture_disabled():
    raise HTTPException(
        409, "Hãy sử dụng API /api/reviews/capture."
    )


def collect_one_frame():
    """Copy exactly one fresh full-resolution frame after the capture request."""
    from backend.app.main import MAX_FRAME_AGE, BURST_TIMEOUT

    started = time.monotonic()
    with camera.lock:
        if (camera.state != "online" or camera.frame is None
                or started - camera.frame_time > MAX_FRAME_AGE):
            raise HTTPException(503, "Camera chưa sẵn sàng để chụp.")
        session = camera.revision
        last_sequence = camera.sequence

    while time.monotonic() - started < BURST_TIMEOUT:
        with camera.lock:
            if camera.revision != session:
                raise HTTPException(409, "Camera đã thay đổi trong lúc chụp. Hãy thử lại.")
            if camera.stop_event.is_set() or camera.state != "online":
                raise HTTPException(503, "Camera mất kết nối trong lúc chụp.")
            if (camera.frame is not None
                    and camera.sequence > last_sequence
                    and camera.frame_time >= started
                    and time.monotonic() - camera.frame_time <= MAX_FRAME_AGE):
                return camera.frame.copy(), {
                    "frame_index": 0,
                    "sequence": camera.sequence,
                    "received_at": camera.received_at,
                }, session
        camera.stop_event.wait(0.01)
    raise HTTPException(504, "Không nhận được khung hình mới. Hãy kiểm tra camera.")


@app.post("/api/reviews/capture", status_code=201, tags=["Review"])
def capture_for_review(
    response: Response,
    options: CaptureOptions | None = None,
):
    return capture_review_work(response, options)


def capture_review_work(response, options=None, snapshot=None, review_id_override=None):
    """Shared legacy/queued path. A snapshot is an already captured lossless frame."""
    session_id = options.session_id if options else None
    if session_id and require_session(session_id)["status"] != "open":
        raise HTTPException(409, "Phiên đã kết thúc.")

    if not capture_lock.acquire(blocking=False):
        raise HTTPException(409, "Đang xử lý lượt chụp khác.")

    created_paths = []
    committed = False
    started = time.perf_counter()

    try:
        from backend.app.storage_quota import require_capture_capacity
        require_capture_capacity()
        frame, frame_metadata, camera_session = snapshot if snapshot is not None else collect_one_frame()
        frames, metadata = [frame], [frame_metadata]
        selected_index = 0
        analysis = analyze_frame(frames[selected_index])

        if not camera.is_current(camera_session):
            raise HTTPException(409, "Camera đã thay đổi. Hãy chụp lại.")

        if session_id and require_session(session_id)["status"] != "open":
            raise HTTPException(409, "Phiên đã kết thúc trong lúc xử lý.")

        review_id = review_id_override or uuid4().hex
        evidence = []
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

        for index, frame in enumerate(frames):
            filename = f"review_{review_id}_frame{index + 1}.jpg"
            path = CAPTURE_DIR / filename
            if path.exists():
                raise HTTPException(409, "Ảnh bằng chứng đã tồn tại. Hãy tạo lượt chụp mới.")
            created_paths.append(path)
            save_jpeg(path, frame)
            evidence.append({
                **metadata[index],
                "image_url": f"/api/captures/{filename}",
            })

        document = {
            "status": "ok",
            "review_id": review_id,
            "revision": 1,
            "review_status": "draft",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "camera_session": camera_session,
            "selected_frame_index": selected_index,
            "capture_mode": "single_frame",
            "frame_count": 1,
            "image_url": evidence[selected_index]["image_url"],
            "width": analysis["width"],
            "height": analysis["height"],
            "evidence_images": evidence,
            "analysis": analysis,
            "human_review": None,
            "correction_history": [],
            "inventory_recorded": False,
            "processing_ms": round(
                (time.perf_counter() - started) * 1000
            ),
            "message": "Đã tạo bản nháp để kiểm tra và hiệu chỉnh.",
        }

        with review_lock:
            save_review(document)
        committed = True

        response.headers["Cache-Control"] = "no-store"
        return document

    except HTTPException:
        raise
    except Exception:
        logger.exception("Không tạo được bản nháp")
        raise HTTPException(
            500, "Không tạo được bản nháp. Xem terminal backend."
        )
    finally:
        if not committed:
            for path in created_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Không dọn được ảnh: %s", path)
        capture_lock.release()


@app.get("/api/reviews/{review_id}", tags=["Review"])
def get_review(review_id: str, response: Response):
    with review_lock:
        document = read_review(review_id)
        receipt = get_review_receipt(review_id)
        document = with_receipt(document, receipt)

    response.headers["Cache-Control"] = "no-store"
    return document


@app.put("/api/reviews/{review_id}/correction", tags=["Review"])
def save_correction(
    review_id: str,
    correction: CorrectionRequest,
    response: Response,
    http_request: Request,
):
    with review_lock:
        document = read_review(review_id)

        # Kiểm tra biên nhận trong SQLite kể cả khi JSON chưa cập nhật.
        if get_review_receipt(review_id) is not None:
            raise HTTPException(
                409, "Bản nháp đã ghi vào kiểm kê, không được sửa trực tiếp."
            )

        if correction.revision != document["revision"]:
            raise HTTPException(
                409, "Bản nháp đã thay đổi. Hãy tải lại trước khi lưu."
            )

        validate_correction(document, correction)

        counts = Counter(
            box.marker_id for box in correction.boxes if box.reviewed
        )
        now = datetime.now(timezone.utc).isoformat()
        next_revision = document["revision"] + 1

        human_review = {
            "reviewed_by": actor_snapshot(http_request.state.identity),
            "saved_at": now,
            "revision": next_revision,
            "boxes": [
                box.model_dump(mode="json") for box in correction.boxes
            ],
            "excluded_regions": (
                [region.model_dump(mode="json") for region in correction.excluded_regions]
                if correction.excluded_regions is not None
                else (document.get("human_review") or {}).get("excluded_regions", [])
            ),
            "assignments": correction.assignments,
            "note": correction.note,
            "summary": {
                "box_count": len(correction.boxes),
                "reviewed_box_count": sum(counts.values()),
                "pending_box_count": sum(
                    not box.reviewed for box in correction.boxes
                ),
                "reviewed_counts_by_marker_id": dict(sorted(counts.items())),
                "unassigned_marker_count": sum(
                    owner == "" for owner in correction.assignments.values()
                ),
            },
        }

        document.setdefault("correction_history", []).append(human_review)
        document["human_review"] = human_review
        document["revision"] = next_revision
        document["review_status"] = "corrected_draft"
        document["updated_at"] = now
        document["inventory_recorded"] = False
        document["message"] = "Đã lưu hiệu chỉnh, chưa ghi vào kiểm kê."
        save_review(document)

    response.headers["Cache-Control"] = "no-store"
    return document


@app.post("/api/reviews/{review_id}/duplicate-check", tags=["Review"])
def duplicate_check_review(review_id: str, request: DuplicateCheckRequest, response: Response):
    # Copy the saved document before expensive image processing.
    with review_lock:
        document = read_review(review_id)
        if get_review_receipt(review_id) is not None:
            raise HTTPException(409, "Bản nháp đã được ghi vào phiên.")
        if document.get('revision') != request.revision:
            raise HTTPException(409, "Bản nháp đã thay đổi. Tải lại rồi đối chiếu.")
        document = json.loads(json.dumps(document, allow_nan=False))
    if not capture_lock.acquire(blocking=False):
        raise HTTPException(409, 'Đang xử lý ảnh khác. Thử lại sau ít giây.')
    try:
        result = create_check(request.session_id, document)
    except ValueError as error:
        raise HTTPException(409, str(error))
    finally:
        capture_lock.release()
    # Fail closed if a correction or a new receipt happened during comparison.
    with review_lock:
        fresh = read_review(review_id)
        if get_review_receipt(review_id) is not None or review_fingerprint(fresh) != result['review_fingerprint']:
            raise HTTPException(409, "Bản nháp đổi trong lúc đối chiếu. Hãy thử lại.")
        with get_connection() as connection:
            connection.execute('BEGIN')
            if current_reference_heads(connection, request.session_id) != result['reference_receipts']:
                raise HTTPException(409, "Phiên có thêm thùng trong lúc đối chiếu. Hãy thử lại.")
    response.headers['Cache-Control'] = 'no-store'
    return result


@app.post("/api/reviews/{review_id}/confirm", tags=["Review"])
def confirm_review(
    review_id: str,
    request: ConfirmRequest,
    response: Response,
    http_request: Request,
):
    if not request.confirm_new_boxes:
        raise HTTPException(
            422,
            "Cần xác nhận đã kiểm tra các quyết định đối chiếu thùng.",
        )

    with review_lock:
        document = read_review(review_id)

        try:
            receipt = record_review(
                request.session_id,
                document,
                request.revision,
                request.check_id,
                [d.model_dump(mode='json') for d in request.decisions],
                confirmed_by=actor_snapshot(http_request.state.identity),
            )
        except ValueError as error:
            raise HTTPException(409, str(error))

        # record_review đã hoàn tất giao dịch SQLite.
        # Nếu cập nhật JSON lỗi, GET vẫn lấy trạng thái từ SQLite.
        document = with_receipt(document, receipt)

        cache_warning = None
        try:
            save_review(document)
        except OSError:
            logger.exception("Đã ghi SQLite nhưng chưa cập nhật JSON")
            cache_warning = (
                "Đã ghi nhận trong SQLite; chưa cập nhật được file JSON. "
                "Tải lại bản nháp để xem trạng thái."
            )

    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "ok",
        "review_id": review_id,
        "inventory_recorded": True,
        "receipt": receipt,
        "warning": cache_warning,
        "message": (
            "Bản nháp đã được ghi trước đó. Không cộng thêm thùng."
            if receipt["already_recorded"]
            else f"Đã ghi nhận {receipt['new_box_count']} thùng mới; "
                 f"{receipt['duplicate_box_count']} thùng cùng thùng đã ghi."

        ),
    }


@app.get(
    "/api/box-inventory/sessions/{session_id}",
    tags=["Box inventory"],
)
def box_session_summary(session_id: str, response: Response):
    try:
        result = get_box_session_summary(session_id)
    except ValueError as error:
        raise HTTPException(404, str(error))

    response.headers["Cache-Control"] = "no-store"
    return result


app.openapi_schema = None
