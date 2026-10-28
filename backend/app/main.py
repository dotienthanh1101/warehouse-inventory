"""Camera IP, nhận dạng ArUco và ghi nhận phiên kiểm kê."""

import asyncio
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from backend.app.aruco_reader import scan_three_frames
from backend.app.database import get_connection, initialize_database
from backend.app.inventory_service import (
    close_session,
    create_session,
    get_session,
    record_scan,
)


ROOT = Path(__file__).resolve().parents[2]
from backend.app.warehouse_scope import ScopedPath
CAPTURE_DIR = ScopedPath("captures")

MAX_FRAME_AGE = 2.0
PREVIEW_WIDTH = 1920
PREVIEW_JPEG_QUALITY = 90
PREVIEW_MAX_FPS = 60.0

BURST_GAP = 0.15
BURST_TIMEOUT = 8.0

capture_lock = threading.RLock()


class CameraConfig(BaseModel):
    address: str = Field(min_length=1, max_length=2048)
    username: str = Field(default="", max_length=128)
    password: SecretStr = Field(default_factory=lambda: SecretStr(""))


class SessionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CaptureOptions(BaseModel):
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )


def load_packages():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT marker_id, package_code, product_code, product_name
            FROM packages
            ORDER BY marker_id
        """).fetchall()

    return [dict(row) for row in rows]


def resolve_packages(scan):
    catalog = {
        row["marker_id"]: row
        for row in load_packages()
    }
    recognized = []
    unregistered_ids = []

    for item in scan["confirmed"]:
        marker_id = int(item["id"])
        package = catalog.get(marker_id)

        if package is None:
            item["registration_status"] = "unregistered"
            item["package"] = None
            unregistered_ids.append(marker_id)
            continue

        package_data = {
            "package_code": package["package_code"],
            "product_code": package["product_code"],
            "product_name": package["product_name"],
        }
        item["registration_status"] = "registered"
        item["package"] = package_data

        recognized.append({
            "marker_id": marker_id,
            **package_data,
            "matched_count": item["matched_count"],
            "evidence_image_url": item["evidence_image_url"],
        })

    return {
        "recognized_packages": recognized,
        "recognized_package_count": len(recognized),
        "unregistered_ids": unregistered_ids,
        "pending_ids": [
            int(item["id"]) for item in scan["pending"]
        ],
        "inventory_recorded": False,
    }


def require_session(session_id):
    try:
        return get_session(session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))


def build_camera_url(config):
    address = config.address.strip()

    if "://" not in address:
        address = "http://" + address

    try:
        parts = urlsplit(address)

        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError()

        if parts.username is not None or parts.password is not None:
            raise ValueError()

        if parts.query or parts.fragment:
            raise ValueError()

        if any(character.isspace() for character in address):
            raise ValueError()

        _ = parts.port

    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Địa chỉ không hợp lệ. Ví dụ: http://10.136.31.151:8081",
        )

    path = parts.path if parts.path not in {"", "/"} else "/live.flv"
    public_url = urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    username = config.username
    password = config.password.get_secret_value()

    if password and not username:
        raise HTTPException(
            status_code=422,
            detail="Hãy nhập tên đăng nhập.",
        )

    auth = ""
    if username:
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"

    private_url = urlunsplit(
        (parts.scheme, auth + parts.netloc, path, "", "")
    )
    return public_url, private_url


class CameraReader:
    """One decoder, one JPEG encoder, one latest-frame slot; no frame queue."""

    def __init__(self):
        self.lock = threading.Lock()
        self.updated = threading.Condition(self.lock)
        self.stop_event = threading.Event()
        self.thread = None
        self.encoder_thread = None
        self.revision = 0
        self.private_url = None
        self.public_url = ""
        self.state = "disconnected"
        self.message = "Chưa kết nối camera."
        self.sequence = 0
        self.source_fps = None
        self.read_times = deque(maxlen=120)
        self.encode_times = deque(maxlen=120)
        self.clear_frame()

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, name="camera-reader", daemon=True)
        self.encoder_thread = threading.Thread(target=self.encode_preview, name="camera-preview", daemon=True)
        self.thread.start()
        self.encoder_thread.start()

    def close(self):
        self.stop_event.set()
        with self.updated:
            self.updated.notify_all()
        for worker in (self.thread, self.encoder_thread):
            if worker is not None:
                worker.join(timeout=6)

    def clear_frame(self):
        # Called with lock held, except during initialization.
        self.frame = None
        self.frame_time = 0.0
        self.received_at = None
        self.preview_jpeg = None
        self.preview_sequence = -1
        self.preview_time = 0.0
        self.preview_width = 0
        self.preview_height = 0
        self.encode_ms = None
        self.preview_error = ""
        self.source_fps = None
        self.read_times.clear()
        self.encode_times.clear()

    def configure(self, public_url, private_url):
        with self.updated:
            self.revision += 1
            self.public_url = public_url
            self.private_url = private_url
            self.state = "connecting"
            self.message = "Đang kết nối camera..."
            self.clear_frame()
            self.updated.notify_all()

    def disconnect(self):
        with self.updated:
            self.revision += 1
            self.private_url = None
            self.public_url = ""
            self.state = "disconnected"
            self.message = "Đã ngắt kết nối camera."
            self.clear_frame()
            self.updated.notify_all()

    def is_current(self, revision):
        with self.lock:
            return not self.stop_event.is_set() and self.revision == revision

    def set_error(self, revision):
        with self.updated:
            if self.revision == revision:
                self.state = "error"
                self.message = "Không nhận được ảnh. Kiểm tra camera rồi kết nối lại."
                self.clear_frame()
                self.updated.notify_all()

    @staticmethod
    def measured_fps(samples):
        if len(samples) < 2 or time.monotonic() - samples[-1] > MAX_FRAME_AGE:
            return 0.0
        duration = samples[-1] - samples[0]
        return round((len(samples) - 1) / duration, 1) if duration > 0 else 0.0

    def status(self):
        with self.lock:
            now = time.monotonic()
            age = now - self.frame_time if self.frame is not None else None
            ready = self.state == "online" and age is not None and age <= MAX_FRAME_AGE
            state = self.state
            message = self.message
            if state == "online" and not ready:
                state, message = "stale", "Camera chưa gửi khung hình mới."
            return {
                "state": state, "message": message, "ready": ready,
                "address": self.public_url, "session": self.revision,
                "received_at": self.received_at,
                # Local time since decoding, NOT camera-to-screen latency.
                "frame_age_seconds": round(age, 3) if age is not None else None,
                "source_fps_reported": self.source_fps,
                "received_fps": self.measured_fps(self.read_times),
                "preview_fps": self.measured_fps(self.encode_times),
                "preview_width": self.preview_width,
                "preview_height": self.preview_height,
                "preview_encode_ms": self.encode_ms,
                "preview_error": self.preview_error,
                "preview_age_seconds": round(now - self.preview_time, 3) if self.preview_jpeg else None,
            }

    def preview_packet(self):
        with self.lock:
            fresh = (
                self.state == "online" and self.preview_jpeg is not None
                and time.monotonic() - self.preview_time <= MAX_FRAME_AGE
            )
            return self.revision, self.preview_sequence, self.preview_jpeg if fresh else None

    def collect_three_frames(self):
        started = time.monotonic()

        with self.lock:
            if (
                self.state != "online"
                or self.frame is None
                or started - self.frame_time > MAX_FRAME_AGE
            ):
                raise HTTPException(
                    status_code=503,
                    detail="Camera chưa sẵn sàng để chụp và đọc mã.",
                )

            session = self.revision
            last_sequence = self.sequence

        frames = []
        metadata = []
        next_frame_time = started

        while len(frames) < 3:
            if time.monotonic() - started >= BURST_TIMEOUT:
                raise HTTPException(
                    status_code=504,
                    detail="Không nhận đủ ba khung hình mới trong thời gian chờ.",
                )

            with self.lock:
                if self.revision != session:
                    raise HTTPException(
                        status_code=409,
                        detail="Camera đã thay đổi trong lúc chụp. Hãy thử lại.",
                    )

                if self.stop_event.is_set() or self.state != "online":
                    raise HTTPException(
                        status_code=503,
                        detail="Camera mất kết nối trong lúc chụp.",
                    )

                fresh = (
                    self.frame is not None
                    and time.monotonic() - self.frame_time <= MAX_FRAME_AGE
                )

                if (
                    fresh
                    and self.sequence > last_sequence
                    and self.frame_time >= next_frame_time
                ):
                    frames.append(self.frame.copy())
                    metadata.append({
                        "frame_index": len(frames) - 1,
                        "sequence": self.sequence,
                        "received_at": self.received_at,
                    })
                    last_sequence = self.sequence
                    next_frame_time = self.frame_time + BURST_GAP

            if len(frames) < 3:
                self.stop_event.wait(0.01)

        return frames, metadata, session

    def run(self):
        attempted_revision = -1
        while not self.stop_event.is_set():
            with self.updated:
                revision, url = self.revision, self.private_url
                if url is None or revision == attempted_revision:
                    self.updated.wait(timeout=0.1)
                    continue
            attempted_revision = revision
            cap = None
            try:
                cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
                ])
                if not cap.isOpened():
                    self.set_error(revision)
                    continue
                reported = cap.get(cv2.CAP_PROP_FPS)
                with self.lock:
                    if self.revision == revision:
                        self.source_fps = round(reported, 2) if np.isfinite(reported) and 1 <= reported <= 240 else None
                while self.is_current(revision):
                    success, frame = cap.read()
                    if not success or frame is None:
                        self.set_error(revision)
                        break
                    frame_time = time.monotonic()
                    received_at = datetime.now(timezone.utc).isoformat()
                    # Publish raw frame immediately. Never resize/encode in reader.
                    with self.updated:
                        if self.revision != revision or self.stop_event.is_set():
                            break
                        self.frame = frame
                        self.frame_time = frame_time
                        self.received_at = received_at
                        self.sequence += 1
                        self.read_times.append(frame_time)
                        self.state = "online"
                        self.message = "Đang nhận ảnh từ camera."
                        self.updated.notify_all()
            except Exception:
                # Do not print the private URL or password.
                self.set_error(revision)
            finally:
                if cap is not None:
                    cap.release()

    def encode_preview(self):
        last_key = None
        next_encode = 0.0
        while not self.stop_event.is_set():
            with self.updated:
                key = (self.revision, self.sequence)
                remaining = next_encode - time.monotonic()
                if (self.state != "online" or self.frame is None
                        or key == last_key or remaining > 0):
                    self.updated.wait(timeout=min(0.1, max(0.001, remaining)) if remaining > 0 else 0.1)
                    continue
                revision, sequence = key
                # A local reference keeps this immutable decoded array alive.
                frame = self.frame
                frame_time = self.frame_time
                target_fps = min(PREVIEW_MAX_FPS, self.source_fps or 30.0)
            last_key = key
            started = time.monotonic()
            next_encode = started + 1.0 / max(1.0, target_fps)
            if started - frame_time > MAX_FRAME_AGE:
                continue
            try:
                height, width = frame.shape[:2]
                preview = frame
                if width > PREVIEW_WIDTH:
                    preview = cv2.resize(frame, (PREVIEW_WIDTH, max(1, round(height * PREVIEW_WIDTH / width))), interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY])
                if not ok:
                    raise ValueError("JPEG encode failed")
                jpeg = encoded.tobytes()
                completed = time.monotonic()
                with self.lock:
                    if (self.revision != revision or self.state != "online"
                            or self.stop_event.is_set() or completed - frame_time > MAX_FRAME_AGE):
                        continue
                    self.preview_jpeg = jpeg
                    self.preview_sequence = sequence
                    self.preview_time = frame_time
                    self.preview_height, self.preview_width = preview.shape[:2]
                    self.encode_ms = round((completed - started) * 1000, 1)
                    self.encode_times.append(completed)
                    self.preview_error = ""
            except Exception:
                with self.lock:
                    if self.revision == revision:
                        self.preview_error = "Không mã hóa được ảnh xem trước."
                self.stop_event.wait(0.1)


from backend.app.warehouse_camera import WarehouseCamera
camera = WarehouseCamera(CameraReader)


def draw_scan_result(frame, frame_index, scan):
    marked = frame.copy()

    confirmed_here = {
        item["id"]
        for item in scan["confirmed"]
        if frame_index in item["matched_frames"]
    }

    for detection in scan["detections_by_frame"][frame_index]:
        marker_id = detection["id"]
        points = np.rint(detection["corners"]).astype(np.int32)

        confirmed = marker_id in confirmed_here
        color = (0, 180, 0) if confirmed else (0, 165, 255)
        label = f"ID {marker_id}: {'OK' if confirmed else 'CHECK'}"

        cv2.polylines(marked, [points], True, color, 3)

        font = cv2.FONT_HERSHEY_SIMPLEX
        (text_width, text_height), baseline = cv2.getTextSize(
            label, font, 0.65, 2
        )
        height, width = marked.shape[:2]
        x = max(0, min(int(points[:, 0].min()), width - text_width - 8))
        y = max(
            text_height + 8,
            min(int(points[:, 1].min()) - 8, height - baseline - 8),
        )

        cv2.rectangle(
            marked,
            (x, y - text_height - 6),
            (x + text_width + 8, y + baseline + 4),
            (255, 255, 255),
            -1,
        )
        cv2.putText(
            marked, label, (x + 4, y), font,
            0.65, color, 2, cv2.LINE_AA,
        )

    return marked


def save_jpeg(path, image):
    success, encoded = cv2.imencode(
        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    if not success:
        raise ValueError("Không mã hóa được ảnh.")
    from backend.app.storage_quota import write_capture
    write_capture(path, encoded)


@asynccontextmanager
async def lifespan(app):
    from backend.app.auth_settings import load_settings
    from backend.app.auth_store import AuthStore
    store = AuthStore(load_settings())
    await asyncio.to_thread(store.open)
    tasks = None
    try:
        await asyncio.to_thread(store.initialize)
        app.state.auth_store = store
        from backend.app.support_service import SupportService
        app.state.support_service = SupportService(store)
        await asyncio.to_thread(app.state.support_service.initialize)
        camera.settings = store.settings
        camera.store = store
        from backend.app.image_tasks import ImageTasks
        tasks = ImageTasks(store)
        tasks.app = app
        await tasks.start()
        app.state.image_tasks = tasks
        from backend.app.camera_gateway import CameraGateway
        gateway=CameraGateway(store,camera)
        await asyncio.to_thread(gateway.initialize)
        app.state.camera_gateway=gateway
        yield
    finally:
        try:
            if tasks is not None:
                await tasks.close()
        finally:
            try:
                await asyncio.to_thread(camera.close_all)
            finally:
                await asyncio.to_thread(store.close)


app = FastAPI(
    title="Warehouse Inventory API",
    description="Thu ảnh, đọc ArUco và ghi nhận phiên kiểm kê.",
    version="0.6.0",
    lifespan=lifespan,
)

from backend.app.auth_middleware import AccountMiddleware
from backend.app.auth_api import router as account_router, safe_validation_error
from fastapi.exceptions import RequestValidationError
app.add_middleware(AccountMiddleware)
app.include_router(account_router)
app.add_exception_handler(RequestValidationError, safe_validation_error)

@app.get('/api/captures/{filename}', tags=['Private images'])
def private_capture(filename: str):
    import re
    from fastapi.responses import FileResponse
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(jpg|jpeg|png)', filename):
        raise HTTPException(404, 'Không tìm thấy ảnh.')
    root = CAPTURE_DIR.resolve()
    path = (CAPTURE_DIR / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, 'Không tìm thấy ảnh.')
    return FileResponse(path, headers={'Cache-Control': 'no-store'})


@app.exception_handler(sqlite3.Error)
async def database_error_handler(request: Request, error: sqlite3.Error):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=503,
        content={
            "detail": "Không truy cập được cơ sở dữ liệu. Hãy thử lại."
        },
    )


@app.get("/api/health", tags=["System"])
def health(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "ok",
        "service": "warehouse-inventory",
        "accounts_enabled": True,
        "version": app.version,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/packages", tags=["Packages"])
def list_packages(response: Response):
    response.headers["Cache-Control"] = "no-store"
    packages = load_packages()
    return {
        "status": "ok",
        "total": len(packages),
        "packages": packages,
    }


@app.post("/api/inventory/sessions", tags=["Inventory"], status_code=201)
def create_inventory_session(config: SessionCreate):
    try:
        return create_session(config.name)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.get("/api/inventory/sessions", tags=["Inventory"])
def list_inventory_sessions(response: Response):
    response.headers["Cache-Control"] = "no-store"

    with get_connection() as connection:
        rows = connection.execute("""
            SELECT
                s.session_id,
                s.name,
                s.status,
                s.started_at,
                s.ended_at,
                (
                    SELECT COUNT(*)
                    FROM inventory_items AS i
                    WHERE i.session_id = s.session_id
                ) AS item_count
            FROM inventory_sessions AS s
            ORDER BY s.started_at DESC, s.session_id DESC
        """).fetchall()

    return {
        "sessions": [dict(row) for row in rows],
    }


@app.get("/api/inventory/sessions/{session_id}", tags=["Inventory"])
def inventory_session_detail(session_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return require_session(session_id)


@app.post("/api/inventory/sessions/{session_id}/close", tags=["Inventory"])
def finish_inventory_session(session_id: str):
    try:
        return close_session(session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))


@app.post("/api/camera/connect", tags=["Camera"], status_code=202)
def connect_camera(config: CameraConfig, response: Response):
    public_url, private_url = build_camera_url(config)
    camera.configure(public_url, private_url)
    response.headers["Cache-Control"] = "no-store"
    return camera.status()


@app.get("/api/camera/status", tags=["Camera"])
def camera_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return camera.status()


@app.post("/api/camera/disconnect", tags=["Camera"])
def disconnect_camera():
    camera.disconnect()
    return camera.status()


@app.get("/api/camera/preview.jpg", tags=["Camera"])
def preview_image(session: int, after: int = -1):
    """One latest JPEG per request; client requests next after display."""
    revision, sequence, jpeg = camera.preview_packet()
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "X-Frame-Sequence": str(sequence),
        "X-Camera-Session": str(revision),
    }
    if revision != session:
        raise HTTPException(status_code=409, detail="Camera đã thay đổi. Tải lại hình.")
    if jpeg is None:
        raise HTTPException(status_code=503, detail="Chưa có ảnh xem trước mới.")
    if sequence <= after:
        return Response(status_code=204, headers=headers)
    return Response(content=jpeg, media_type="image/jpeg", headers=headers)


@app.get("/api/camera/preview", tags=["Camera"])
async def preview_camera(request: Request):
    status = camera.status()
    if not status["ready"]:
        raise HTTPException(
            status_code=503,
            detail="Camera chưa sẵn sàng để xem trực tiếp.",
        )

    session = status["session"]

    async def frames():
        from backend.app.auth_store import COOKIE
        from backend.app.warehouse_scope import current_warehouse_id
        owner = current_warehouse_id()
        next_auth = 0.0
        last_sequence = -1
        while not await request.is_disconnected():
            if time.monotonic() >= next_auth:
                try:
                    identity = await asyncio.to_thread(request.app.state.auth_store.authenticate, request.cookies.get(COOKIE, ''))
                    if identity['warehouse_id'] != owner:
                        break
                except Exception:
                    break
                next_auth = time.monotonic() + 5
            revision, sequence, jpeg = camera.preview_packet()

            if revision != session or jpeg is None:
                break

            if sequence != last_sequence:
                last_sequence = sequence
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: "
                    + str(len(jpeg)).encode("ascii")
                    + b"\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )

            await asyncio.sleep(0.001)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/camera/capture", tags=["Camera"])
def capture_and_scan(options: CaptureOptions | None = None):
    inventory_session_id = options.session_id if options else None

    # Kiểm tra trước khi chụp để tránh chụp vào phiên không hợp lệ.
    if inventory_session_id:
        inventory_session = require_session(inventory_session_id)
        if inventory_session["status"] != "open":
            raise HTTPException(
                status_code=409,
                detail="Phiên kiểm kê đã kết thúc.",
            )

    if not capture_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Đang xử lý một lượt chụp. Vui lòng chờ.",
        )

    created_files = []
    completed = False
    inventory_committed = False
    started = time.monotonic()

    try:
        frames, metadata, camera_session = camera.collect_three_frames()
        scan = scan_three_frames(frames)

        if not camera.is_current(camera_session):
            raise HTTPException(
                status_code=409,
                detail="Camera đã thay đổi trong lúc xử lý. Hãy chụp lại.",
            )

        def frame_score(index):
            count = sum(
                index in item["matched_frames"]
                for item in scan["confirmed"]
            )
            return count, index

        primary_index = max(range(3), key=frame_score)

        evidence_indices = {primary_index}
        evidence_indices.update(
            item["evidence_frame_index"] for item in scan["confirmed"]
        )

        capture_id = uuid4().hex
        evidence_images = []

        for index in sorted(evidence_indices):
            original_name = f"{capture_id}_frame{index + 1}.jpg"
            marked_name = f"{capture_id}_frame{index + 1}_marked.jpg"

            original_path = CAPTURE_DIR / original_name
            marked_path = CAPTURE_DIR / marked_name
            created_files.extend([original_path, marked_path])

            save_jpeg(original_path, frames[index])
            marked = draw_scan_result(frames[index], index, scan)
            save_jpeg(marked_path, marked)

            evidence_images.append({
                "frame_index": index,
                "filename": original_name,
                "original_image_url": f"/api/captures/{original_name}",
                "image_url": f"/api/captures/{marked_name}",
                "received_at": metadata[index]["received_at"],
            })

        image_by_index = {
            image["frame_index"]: image for image in evidence_images
        }

        for item in scan["confirmed"]:
            evidence = image_by_index[item["evidence_frame_index"]]
            item["evidence_image_url"] = evidence["image_url"]

        package_lookup = resolve_packages(scan)

        confirmed_count = len(scan["confirmed"])
        pending_count = len(scan["pending"])

        if pending_count:
            outcome = "needs_review"
            message = (
                f"Đã xác nhận {confirmed_count} mã; "
                f"{pending_count} mã cần kiểm tra."
            )
        elif confirmed_count:
            outcome = "confirmed"
            message = f"Đã xác nhận {confirmed_count} mã."
        else:
            outcome = "not_detected"
            message = (
                "Không phát hiện mã ArUco trong ba khung hình. "
                "Chưa thể kết luận không có hàng."
            )

        if confirmed_count:
            known_count = package_lookup["recognized_package_count"]
            unknown_count = len(package_lookup["unregistered_ids"])
            message += f" Tra cứu được {known_count} kiện đã đăng ký."

            if unknown_count:
                message += f" Có {unknown_count} ID chưa đăng ký."

        primary = image_by_index[primary_index]
        height, width = frames[primary_index].shape[:2]

        result = {
            "status": "ok",
            "capture_id": capture_id,
            "filename": primary["filename"],
            "image_url": primary["image_url"],
            "original_image_url": primary["original_image_url"],
            "width": width,
            "height": height,
            "received_at": primary["received_at"],
            "camera_session": camera_session,
            "message": message,
            "scan": {
                "outcome": outcome,
                "confirmed_ids": [
                    item["id"] for item in scan["confirmed"]
                ],
                "pending_ids": [
                    item["id"] for item in scan["pending"]
                ],
                **scan,
            },
            "package_lookup": package_lookup,
            "inventory": None,
            "frames": metadata,
            "evidence_images": evidence_images,
        }

        if not camera.is_current(camera_session):
            raise HTTPException(
                status_code=409,
                detail="Camera đã thay đổi trước khi hoàn tất. Hãy chụp lại.",
            )

        if inventory_session_id:
            try:
                inventory_result = record_scan(
                    inventory_session_id,
                    capture_id,
                    scan,
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error))

            # Từ đây ảnh có thể đã được cơ sở dữ liệu tham chiếu.
            # Không xóa ảnh nếu có lỗi sau khi giao dịch đã hoàn tất.
            inventory_committed = True
            result["inventory"] = inventory_result

            recorded_ids = (
                inventory_result["new_ids"]
                + inventory_result["repeated_ids"]
            )
            package_lookup["inventory_recorded"] = bool(recorded_ids)

            result["message"] += (
                f" Thêm mới {len(inventory_result['new_ids'])} kiện;"
                f" gặp lại {len(inventory_result['repeated_ids'])} kiện."
                f" Tổng trong phiên: {inventory_result['total_packages']} kiện."
            )

        result["processing_ms"] = round(
            (time.monotonic() - started) * 1000
        )
        completed = True
        return result

    except (OSError, ValueError, cv2.error):
        raise HTTPException(
            status_code=500,
            detail="Không xử lý hoặc lưu được lượt chụp.",
        )
    finally:
        if not completed and not inventory_committed:
            for path in created_files:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

        capture_lock.release()
