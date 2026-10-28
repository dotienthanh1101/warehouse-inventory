"""Browser pairing is session/CSRF guarded; agent uses only a scoped bearer."""
import asyncio
from datetime import datetime
from fastapi import APIRouter,HTTPException,Request,Response
from pydantic import BaseModel,ConfigDict,Field
from starlette.concurrency import run_in_threadpool
from backend.app.auth_store import COOKIE
from backend.app.camera_gateway import MAX_BODY,decode_frame

router=APIRouter(prefix='/api/camera/gateway',tags=['Camera gateway'])

class Claim(BaseModel):
    model_config=ConfigDict(extra='forbid')
    pair_code:str=Field(pattern=r'^pair_[A-Za-z0-9_-]{43}$')


def service(request):return request.app.state.camera_gateway

def bearer(request):
    value=request.headers.get('authorization','')
    if not value.startswith('Bearer ') or len(value)>128:raise HTTPException(401,'Thiếu khóa cầu nối.')
    return value[7:]

def private(response):response.headers['Cache-Control']='no-store'

@router.post('/pair')
def create_pair(request:Request,response:Response):
    private(response)
    return service(request).pair(request.state.identity,request.cookies[COOKIE])

@router.get('/status')
def gateway_status(request:Request,response:Response):
    private(response)
    return service(request).status(request.state.identity)

@router.post('/revoke')
def revoke_gateway(request:Request,response:Response):
    private(response)
    return service(request).revoke(request.state.identity['warehouse_id'])

@router.post('/claim')
def claim_gateway(body:Claim,request:Request,response:Response):
    private(response)
    store=request.app.state.auth_store
    store.rate_limit('gateway-claim-ip',request.client.host if request.client else 'unknown',20,60)
    return service(request).claim(body.pair_code)

@router.post('/frame')
async def upload_frame(request:Request,response:Response):
    private(response)
    token=bearer(request)
    try:await run_in_threadpool(service(request).authorize,token,True)
    except HTTPException as error:
        if error.status_code==401:
            await run_in_threadpool(request.app.state.auth_store.rate_limit,'gateway-invalid-ip',request.client.host if request.client else 'unknown',40,60)
        raise
    if request.headers.get('content-type','').split(';')[0].strip().lower()!='image/jpeg':raise HTTPException(415,'Chỉ nhận ảnh JPEG.')
    try:
        sequence=int(request.headers.get('x-frame-sequence',''))
        sent_at=datetime.fromisoformat(request.headers.get('x-frame-time','').replace('Z','+00:00'))
        if sent_at.tzinfo is None:raise ValueError()
        length=request.headers.get('content-length')
        if length is not None and int(length)>MAX_BODY:raise HTTPException(413,'Ảnh gửi lên vượt 4 MiB.')
    except (ValueError,TypeError):raise HTTPException(422,'Thiếu số thứ tự hoặc thời điểm ảnh hợp lệ.')
    data=bytearray()
    try:
        async with asyncio.timeout(8):
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data)>MAX_BODY:raise HTTPException(413,'Ảnh gửi lên vượt 4 MiB.')
    except TimeoutError:raise HTTPException(408,'Gửi ảnh quá chậm. Hãy gửi khung hình mới.')
    data=bytes(data)
    frame=await run_in_threadpool(decode_frame,data)
    return await run_in_threadpool(service(request).publish,token,sequence,sent_at,data,frame)

@router.post('/stop')
def stop_gateway(request:Request,response:Response):
    private(response)
    return service(request).stop_token(bearer(request))

@router.get('/agent')
def download_agent(request:Request):
    from io import BytesIO
    from pathlib import Path
    from zipfile import ZipFile,ZIP_DEFLATED
    base=Path(__file__).resolve().parents[2]/'camera_bridge'
    names=('run.py','video.py','requirements.txt','README_VI.md')
    if not all((base/name).is_file() for name in names):raise HTTPException(503,'Chưa có bộ cầu nối để tải.')
    buffer=BytesIO()
    with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:
        for name in names:archive.writestr('camera_bridge/'+name,(base/name).read_bytes())
    return Response(buffer.getvalue(),media_type='application/zip',headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="camera_bridge.zip"'})
