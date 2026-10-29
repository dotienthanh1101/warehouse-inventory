"""Authenticated readonly provenance for a product line of a day report."""
from typing import Annotated
from fastapi import APIRouter, HTTPException, Path, Query, Response
from backend.app.inventory_trace_service import get_day_trace

router = APIRouter(prefix='/api/inventory', tags=['Inventory trace'])
Id = Annotated[str, Path(pattern='^[0-9a-f]{32}$')]


@router.get('/days/{day_id}/trace')
def trace_day(day_id: Id, response: Response, marker_id: Annotated[int, Query(ge=0, le=249)],
              session_id: Annotated[str | None, Query(pattern='^[0-9a-f]{32}$')] = None,
              limit: Annotated[int, Query(ge=1, le=50)] = 20,
              offset: Annotated[int, Query(ge=0)] = 0):
    try:
        result = get_day_trace(day_id, marker_id, session_id, limit, offset)
    except LookupError as error:
        raise HTTPException(404, str(error))
    except ValueError as error:
        raise HTTPException(409, str(error))
    response.headers['Cache-Control'] = 'no-store'
    return result
