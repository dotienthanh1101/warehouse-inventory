"""Authenticated downloads; no public report files and no inventory writes."""
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response

from backend.app.report_export_service import load_closed_report, render_csv, render_xlsx

router = APIRouter(prefix='/api/inventory', tags=['Report export'])
logger = logging.getLogger(__name__)


@router.get('/days/{day_id}/export')
def export_day(day_id: Annotated[str, Path(pattern='^[0-9a-f]{32}$')], request: Request,
               format: Literal['xlsx', 'csv'] = Query(default='xlsx')):
    actor = request.state.identity
    if actor['role'] != 'user' or not actor['warehouse_id']:
        raise HTTPException(403, 'Chỉ user được xuất báo cáo kho của mình.')
    store = request.app.state.auth_store
    store.rate_limit('report-export', actor['user_id'], 20, 60)
    try:
        report = load_closed_report(day_id)
        warehouse = {'warehouse_id': actor['warehouse_id'], 'name': actor['warehouse_name']}
        payload = render_xlsx(report, warehouse) if format == 'xlsx' else render_csv(report, warehouse)
    except LookupError as error:
        raise HTTPException(404, str(error))
    except ValueError as error:
        raise HTTPException(409, str(error))
    except ImportError:
        logger.exception('Thiếu thư viện xuất báo cáo Excel')
        raise HTTPException(503, 'Chưa sẵn sàng xuất Excel. Hãy liên hệ quản trị hoặc chọn CSV.')
    except Exception:
        logger.exception('Không tạo được báo cáo kiểm kê')
        raise HTTPException(503, 'Chưa tạo được file báo cáo. Hãy thử lại sau.')
    try:
        with store.pool.connection() as connection:
            store.audit(connection, actor['user_id'], actor['warehouse_id'], f'report_export:{day_id}:{format}')
    except Exception:
        logger.exception('Không lưu được nhật ký xuất báo cáo')
        raise HTTPException(503, 'Chưa hoàn tất xuất báo cáo. Hãy thử lại sau.')
    filename = f"kiem-ke-kho_{report['business_date']}_{day_id[:8]}.{format}"
    media_type = ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                  if format == 'xlsx' else 'text/csv; charset=utf-8')
    return Response(payload, media_type=media_type, headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store', 'X-Report-Day-ID': day_id, 'X-Report-State': 'closed',
    })
