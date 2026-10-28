"""Authenticated queue submission, status and results (FR-36)."""
import json
import re

import cv2
from fastapi import APIRouter,HTTPException,Request,Response
from pydantic import BaseModel,ConfigDict,Field
from starlette.concurrency import run_in_threadpool

from backend.app.auth_store import COOKIE,token_digest
from backend.app.database import get_connection
from backend.app.image_tasks import INPUTS

router=APIRouter(prefix='/api/tasks',tags=['Image tasks'])

class SubmitTask(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: str=Field(pattern='^[0-9a-f]{32}$')
    kind: str=Field(pattern='^(capture|duplicate-check)$')
    session_id: str=Field(min_length=1,max_length=64)
    review_id: str|None=Field(default=None,pattern='^[0-9a-f]{32}$')
    revision: int|None=Field(default=None,ge=1,strict=True)

def require_open_session(session_id):
    with get_connection() as c:
        row=c.execute('''SELECT 1 FROM inventory_sessions s
            JOIN inventory_day_sessions ds ON ds.session_id=s.session_id
            JOIN inventory_days d ON d.day_id=ds.day_id
            WHERE s.session_id=? AND s.status='open' AND ds.finalized_at IS NULL AND d.status='open' ''',(session_id,)).fetchone()
    if not row:raise HTTPException(409,'Cần chọn phiên đang mở thuộc ngày đang mở trong kho của bạn.')

def prepare(row,body):
    from backend.app.review_api import collect_one_frame,read_review,review_lock,review_fingerprint,get_review_receipt
    require_open_session(body.session_id)
    payload={'request':body.model_dump(exclude={'request_id','kind'})}
    if body.kind=='capture':
        if body.review_id is not None or body.revision is not None:raise HTTPException(422,'Lượt chụp không nhận mã bản nháp hoặc phiên bản.')
        from backend.app.storage_quota import require_capture_capacity
        require_capture_capacity()
        frame,metadata,session=collect_one_frame()
        INPUTS.mkdir(parents=True,exist_ok=True)
        ok,encoded=cv2.imencode('.png',frame)
        if not ok:raise HTTPException(500,'Không lưu được ảnh chụp tạm.')
        from backend.app.server_capacity import capacity_lock, require_disk_space
        with capacity_lock:
            require_disk_space(int(encoded.nbytes))
            encoded.tofile(str(INPUTS/(row['task_id']+'.png')))
        payload.update(frame_metadata=metadata,camera_session=session)
    else:
        if body.review_id is None or body.revision is None:raise HTTPException(422,'Cần mã bản nháp và phiên bản để kiểm tra trùng.')
        with review_lock:
            document=read_review(body.review_id)
            if document.get('revision')!=body.revision:raise HTTPException(409,'Bản nháp đã đổi. Lưu và tải lại trước khi kiểm tra.')
            if document.get('session_id') not in (None,body.session_id):raise HTTPException(409,'Bản nháp thuộc phiên khác.')
            if get_review_receipt(body.review_id) is not None:raise HTTPException(409,'Bản nháp đã ghi vào phiên.')
            if not document.get('human_review'):raise HTTPException(409,'Lưu hiệu chỉnh trước khi kiểm tra trùng.')
            payload['fingerprint']=review_fingerprint(document)
    return payload

@router.post('',status_code=202)
async def submit_task(body:SubmitTask,request:Request,response:Response):
    tasks=request.app.state.image_tasks
    identity=request.state.identity
    row,fresh=await run_in_threadpool(tasks.reserve,identity,token_digest(request.cookies[COOKIE]),
        body.request_id,body.kind,body.model_dump(exclude={'request_id','kind'}))
    if fresh:
        try:
            payload=await run_in_threadpool(prepare,row,body)
            row=await run_in_threadpool(tasks.mark_queued,row['task_id'],payload)
            tasks.wake.set()
        except Exception as error:
            code=error.status_code if isinstance(error,HTTPException) else 500
            message=str(error.detail) if isinstance(error,HTTPException) else 'Không chuẩn bị được ảnh. Hãy thử lại.'
            tasks.preparation_failures[row['task_id']]=(code,message)
            try:
                await run_in_threadpool(tasks.fail,row['task_id'],code,message)
                tasks.preparation_failures.pop(row['task_id'],None)
            finally:
                await run_in_threadpool(tasks.cleanup,row)
            row=await run_in_threadpool(tasks.get,identity,row['task_id'])
    response.headers['Cache-Control']='no-store'
    return await run_in_threadpool(tasks.public,row)

@router.get('/latest')
async def latest_task(request:Request):
    tasks=request.app.state.image_tasks
    row=await run_in_threadpool(tasks.get,request.state.identity)
    return {'task':await run_in_threadpool(tasks.public,row)}

@router.get('/{task_id}')
async def read_task(task_id:str,request:Request):
    tasks=request.app.state.image_tasks
    row=await run_in_threadpool(tasks.get,request.state.identity,task_id)
    return await run_in_threadpool(tasks.public,row)

@router.get('/{task_id}/result')
async def read_result(task_id:str,request:Request):
    row=await run_in_threadpool(request.app.state.image_tasks.get,request.state.identity,task_id)
    if row['state']=='failed':raise HTTPException(409,row['error_message'] or 'Tác vụ thất bại.')
    if row['state']!='completed':raise HTTPException(409,'Tác vụ chưa hoàn tất.')
    return row['result']
