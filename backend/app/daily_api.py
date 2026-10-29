"""API quản lý ngày, phiên kiểm kê và báo cáo cuối ngày."""

from datetime import datetime, timezone

from fastapi import HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.review_api import app
from backend.app.database import get_connection, initialize_database
from backend.app.catalog_api import router as catalog_router
from backend.app.daily_inventory_service import (
    create_day, get_day, get_open_day, VIETNAM_TIMEZONE,
)
from backend.app.daily_session_service import create_daily_session
from backend.app.daily_reconciliation_service import get_session_reconciliation
from backend.app.session_finalize_service import finalize_session
from backend.app.day_closing_service import get_day_report, finalize_day


app.include_router(catalog_router)
from backend.app.image_task_api import router as image_task_router
app.include_router(image_task_router)
from backend.app.admin_warehouse_api import router as admin_warehouse_router
app.include_router(admin_warehouse_router)
from backend.app.support_api import router as support_router
app.include_router(support_router)
from backend.app.report_export_api import router as report_export_router
app.include_router(report_export_router)
from backend.app.storage_api import router as storage_router
app.include_router(storage_router)
from backend.app.camera_gateway_api import router as camera_gateway_router
app.include_router(camera_gateway_router)
from backend.app.inventory_trace_api import router as trace_router
app.include_router(trace_router)


class OpeningItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marker_id: int = Field(ge=0, le=249, strict=True)
    quantity: int = Field(ge=0, strict=True)


class CreateDayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opening_items: list[OpeningItem] | None = None


class CreateDailySessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class FinalizeSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_full_coverage: bool = Field(strict=True)
    expected_receipt_count: int = Field(ge=0, strict=True)
    confirm_empty: bool = Field(default=False, strict=True)


class FinalizeDayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_close: bool = Field(strict=True)
    expected_session_count: int = Field(ge=1, strict=True)


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def is_legacy_session_write(route):
    path = getattr(route, "path", "")
    methods = set(getattr(route, "methods", None) or [])
    is_session_path = (
        path == "/api/inventory/sessions"
        or path.startswith("/api/inventory/sessions/")
    )
    return is_session_path and bool(methods & WRITE_METHODS)


app.router.routes[:] = [
    route for route in app.router.routes
    if not is_legacy_session_write(route)
]


@app.post("/api/inventory/sessions", tags=["Daily inventory"])
def legacy_create_session_disabled():
    raise HTTPException(409, "Hãy tạo phiên thuộc ngày kiểm kê bằng POST /api/inventory/days/{day_id}/sessions.")


@app.get("/api/inventory/days", tags=["Daily inventory"])
def list_inventory_days(response: Response):
    """Danh sách ngày, kể cả ngày đã chốt, và danh mục cho ngày đầu."""
    with get_connection() as connection:
        connection.execute("BEGIN")
        days = connection.execute("""
            SELECT day_id, business_date, status, opened_at, closed_at
            FROM inventory_days ORDER BY business_date DESC
        """).fetchall()
        catalog = connection.execute("""
            SELECT marker_id, product_code, product_name, is_active, catalog_revision
            FROM packages ORDER BY marker_id
        """).fetchall()
    response.headers["Cache-Control"] = "no-store"
    return {
        "days": [dict(day) for day in days],
        "catalog": [dict(item) for item in catalog],
        "business_date_today": datetime.now(timezone.utc).astimezone(VIETNAM_TIMEZONE).date().isoformat(),
    }


@app.get("/api/inventory/days/open", tags=["Daily inventory"])
def read_open_day(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"day": get_open_day()}


@app.post("/api/inventory/days", status_code=201, tags=["Daily inventory"])
def open_inventory_day(request: CreateDayRequest, response: Response):
    opening_counts = None
    if request.opening_items is not None:
        opening_counts = {}
        for item in request.opening_items:
            if item.marker_id in opening_counts:
                raise HTTPException(422, f"ID {item.marker_id} xuất hiện nhiều lần.")
            opening_counts[item.marker_id] = item.quantity
    try:
        result = create_day(opening_counts)
    except ValueError as error:
        raise HTTPException(409, str(error))
    response.headers["Cache-Control"] = "no-store"
    return result


@app.get("/api/inventory/days/{day_id}", tags=["Daily inventory"])
def read_inventory_day(day_id: str, response: Response):
    try:
        result = get_day(day_id)
    except ValueError as error:
        raise HTTPException(404, str(error))
    response.headers["Cache-Control"] = "no-store"
    return result


@app.get("/api/inventory/days/{day_id}/sessions", tags=["Daily inventory"])
def list_daily_sessions(day_id: str, response: Response):
    with get_connection() as connection:
        connection.execute("BEGIN")
        day = connection.execute(
            "SELECT day_id FROM inventory_days WHERE day_id = ?", (day_id,)
        ).fetchone()
        if day is None:
            raise HTTPException(404, "Không tìm thấy ngày kiểm kê.")
        rows = connection.execute("""
            SELECT s.session_id, s.name, s.status, s.started_at, s.ended_at,
                   ds.day_id, ds.session_order, ds.finalized_at
            FROM inventory_day_sessions AS ds
            JOIN inventory_sessions AS s ON s.session_id = ds.session_id
            WHERE ds.day_id = ? ORDER BY ds.session_order
        """, (day_id,)).fetchall()
    response.headers["Cache-Control"] = "no-store"
    return {"day_id": day_id, "sessions": [dict(row) for row in rows]}


@app.post("/api/inventory/days/{day_id}/sessions", status_code=201, tags=["Daily inventory"])
def open_daily_session(day_id: str, request: CreateDailySessionRequest, response: Response):
    try:
        result = create_daily_session(day_id, request.name)
    except ValueError as error:
        raise HTTPException(409, str(error))
    response.headers["Cache-Control"] = "no-store"
    return result


@app.get("/api/inventory/sessions/{session_id}/reconciliation", tags=["Daily inventory"])
def read_session_reconciliation(session_id: str, response: Response):
    try:
        result = get_session_reconciliation(session_id)
    except ValueError as error:
        raise HTTPException(409, str(error))
    response.headers["Cache-Control"] = "no-store"
    return result


@app.post("/api/inventory/sessions/{session_id}/finalize", tags=["Daily inventory"])
def finalize_daily_session(session_id: str, request: FinalizeSessionRequest, response: Response):
    try:
        result = finalize_session(
            session_id,
            confirm_full_coverage=request.confirm_full_coverage,
            expected_receipt_count=request.expected_receipt_count,
            confirm_empty=request.confirm_empty,
        )
    except ValueError as error:
        raise HTTPException(409, str(error))
    response.headers["Cache-Control"] = "no-store"
    return result


@app.get("/api/inventory/days/{day_id}/report", tags=["Daily inventory"])
def read_day_report(day_id: str, response: Response):
    try:
        result = get_day_report(day_id)
    except ValueError as error:
        raise HTTPException(409, str(error))
    response.headers["Cache-Control"] = "no-store"
    return result


@app.post("/api/inventory/days/{day_id}/finalize", tags=["Daily inventory"])
def close_inventory_day(day_id: str, request: FinalizeDayRequest, response: Response):
    try:
        result = finalize_day(
            day_id,
            confirm_close=request.confirm_close,
            expected_session_count=request.expected_session_count,
        )
    except ValueError as error:
        raise HTTPException(409, str(error))
    response.headers["Cache-Control"] = "no-store"
    return result


from backend.app.camera_video import router as camera_video_router
app.include_router(camera_video_router)

app.openapi_schema = None
