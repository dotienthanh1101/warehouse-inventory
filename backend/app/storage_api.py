"""Authenticated storage status and admin quota adjustment."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from backend.app.account_gate import account_gate
from backend.app.auth_api import admin_identity
from backend.app.auth_store import COOKIE
from backend.app.storage_quota import usage, set_limit
from backend.app.server_capacity import capacity_status

router = APIRouter(prefix='/api', tags=['Storage'])


@router.get('/admin/storage-capacity')
def server_storage(request: Request):
    admin_identity(request)
    return capacity_status(request.app.state.auth_store)


class StorageLimit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit_mib: int = Field(ge=1, le=102400, strict=True)
    expected_version: int = Field(ge=0, strict=True)


def owner(store, warehouse_id):
    with store.pool.connection() as c:
        row = c.execute('SELECT owner_id FROM auth_warehouses WHERE warehouse_id=%s', (warehouse_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Không tìm thấy kho.')
    return row['owner_id']


@router.get('/storage')
def own_storage(request: Request):
    return usage(request.state.identity['warehouse_id'])


@router.get('/admin/warehouses/{warehouse_id}/storage')
def warehouse_storage(warehouse_id: str, request: Request):
    admin_identity(request)
    owner(request.app.state.auth_store, warehouse_id)
    return usage(warehouse_id)


@router.post('/admin/warehouses/{warehouse_id}/storage')
async def change_storage(warehouse_id: str, body: StorageLimit, request: Request):
    actor = admin_identity(request)
    store = request.app.state.auth_store
    target = await run_in_threadpool(owner, store, warehouse_id)
    store.rate_limit('admin-storage', actor['user_id'], 30, 60)
    # Same order as image worker: account barrier, then storage lock.
    async with account_gate(request.app, target):
        await run_in_threadpool(store.authenticate, request.cookies[COOKIE])
        return await run_in_threadpool(set_limit, store, actor, warehouse_id,
                                      body.limit_mib, body.expected_version)
