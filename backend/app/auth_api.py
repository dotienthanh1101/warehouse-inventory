"""Public registration and cookie sessions; no public admin bootstrap endpoint."""
import re
from fastapi import APIRouter, HTTPException, Request, Response, Query
from starlette.concurrency import run_in_threadpool
from backend.app.account_gate import account_gate
from backend.app.recovery_email import normalize_email
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg import IntegrityError
from backend.app.auth_store import COOKIE,token_digest
from backend.app.warehouse_scope import warehouse_scope

router = APIRouter(prefix='/api', tags=['Accounts'])

class Registration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)
    warehouse_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


    @field_validator('username')
    @classmethod
    def valid_username(cls, value):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,31}', value):
            raise ValueError('Tên đăng nhập gồm 3–32 ký tự chữ, số, dấu chấm, gạch ngang hoặc gạch dưới.')
        return value.lower()

    @field_validator('display_name', 'warehouse_name')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('Không được để trống.')
        return value.strip()

class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

def admin_identity(request):
    identity = request.state.identity
    if identity['role'] != 'admin':
        raise HTTPException(403, 'Chỉ admin được sử dụng chức năng này.')
    return identity

@router.get('/auth/config')
def config(request: Request):
    return {'registration_open': request.app.state.auth_store.settings.registration_open}

@router.post('/auth/register', status_code=201)
def register(body: Registration, request: Request):
    store = request.app.state.auth_store
    if not store.settings.registration_open:
        raise HTTPException(403, 'Đăng ký hiện đang tạm đóng.')
    ip = request.client.host if request.client else 'unknown'
    store.rate_limit('registration', ip, 20, 60)
    store.rate_limit('registration-all', 'all', 200, 60)
    try:
        result = store.register(body.username, body.display_name, body.password, body.warehouse_name, email=body.email)
    except IntegrityError:
        raise HTTPException(409, 'Tên đăng nhập đã được sử dụng.')
    return {**result, 'message': 'Đã tạo tài khoản và kho riêng. Bạn có thể đăng nhập.'}

@router.post('/auth/login')
def login(body: Login, request: Request, response: Response):
    store = request.app.state.auth_store
    ip = request.client.host if request.client else 'unknown'
    store.rate_limit('login-ip', ip, 120, 60)
    store.rate_limit('login-user', body.username.strip().lower(), 10, 300)
    token = store.login(body.username, body.password)
    identity = store.authenticate(token)
    response.set_cookie(COOKIE, token, httponly=True, secure=store.settings.secure_cookie,
                        samesite='lax', max_age=store.settings.session_hours * 3600, path='/')
    return store.profile(identity, token)

@router.get('/auth/me')
def me(request: Request):
    store = request.app.state.auth_store
    return store.profile(request.state.identity, request.cookies[COOKIE])

@router.post('/auth/logout')
def logout(request: Request, response: Response):
    store = request.app.state.auth_store
    store.logout(request.cookies[COOKIE])
    tasks=getattr(request.app.state,'image_tasks',None)
    if tasks is not None:tasks.cancel_user(request.state.identity['user_id'],token_digest(request.cookies[COOKIE]))
    response.delete_cookie(COOKIE, path='/', secure=store.settings.secure_cookie, httponly=True, samesite='lax')
    return {'message': 'Đã đăng xuất.'}

@router.get('/warehouse')
def own_warehouse(request: Request):
    return {'warehouse_id': request.state.identity['warehouse_id'],
            'name': request.state.identity['warehouse_name']}

@router.get('/admin/users')
def list_users(request: Request, search: str=Query(default='',max_length=100),
               status: str=Query(default='all',pattern='^(all|active|locked)$'),
               offset: int=Query(default=0,ge=0), limit: int=Query(default=20,ge=1,le=100)):
    admin_identity(request)
    return request.app.state.auth_store.list_users(search,status,offset,limit)

@router.get('/admin/users/{user_id}')
def user_detail(user_id: str, request: Request):
    actor=admin_identity(request)
    return request.app.state.auth_store.user_detail(user_id,actor['user_id'])

class AccountAction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action: str=Field(pattern='^(lock|unlock|revoke|reset)$')
    expected_version: int=Field(ge=1,strict=True)
    reason: str=Field(min_length=3,max_length=240)
    admin_password: str=Field(default='',max_length=128)
    recovery_ticket_id: str | None=Field(default=None,pattern='^[0-9a-f]{32}$')
    confirm_identity: bool=Field(default=False,strict=True)
    @field_validator('reason')
    @classmethod
    def reason_not_blank(cls,value):
        if len(value.strip())<3:raise ValueError('Nhập lý do ít nhất 3 ký tự.')
        return value.strip()

@router.post('/admin/users/{user_id}/actions')
async def manage_user(user_id: str, body: AccountAction, request: Request, response: Response):
    actor=admin_identity(request)
    store=request.app.state.auth_store
    store.rate_limit('admin-action',actor['user_id'],30,60)
    async with account_gate(request.app,user_id):
        # Recheck actor after waiting for an in-flight target write.
        await run_in_threadpool(store.authenticate,request.cookies[COOKIE])
        result=await run_in_threadpool(store.manage_user,actor['user_id'],user_id,body.action,
            body.expected_version,body.reason,body.admin_password,actor['account_version'],body.recovery_ticket_id,body.confirm_identity)
        tasks=getattr(request.app.state,'image_tasks',None)
        if tasks is not None:await run_in_threadpool(tasks.cancel_user,user_id)
        if result['warehouse_id']:
            from backend.app.main import camera
            await run_in_threadpool(camera.disconnect_warehouse,result['warehouse_id'])
    response.headers['Cache-Control']='no-store'
    messages={'lock':'Đã khóa tài khoản và thu hồi đăng nhập.','unlock':'Đã mở khóa. User cần đăng nhập lại.',
        'revoke':'Đã thu hồi toàn bộ phiên đăng nhập. User có thể đăng nhập lại.',
        'reset':'Đã cấp mật khẩu tạm, có hiệu lực 24 giờ. User phải đổi trước khi dùng kho.'}
    return {**result,'message':messages[body.action]}

class PasswordChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    current_password: str=Field(min_length=1,max_length=128)
    new_password: str=Field(min_length=12,max_length=128)

@router.post('/auth/change-password')
def change_password(body: PasswordChange, request: Request, response: Response):
    store=request.app.state.auth_store
    identity=request.state.identity
    store.rate_limit('password-change',identity['user_id'],10,300)
    store.change_password(identity,body.current_password,body.new_password)
    tasks=getattr(request.app.state,'image_tasks',None)
    if tasks is not None:tasks.cancel_user(identity['user_id'])
    if identity['warehouse_id']:
        from backend.app.main import camera
        camera.disconnect_warehouse(identity['warehouse_id'])
    response.delete_cookie(COOKIE,path='/',secure=store.settings.secure_cookie,httponly=True,samesite='lax')
    return {'message':'Đã đổi mật khẩu và thu hồi các phiên cũ. Đăng nhập lại bằng mật khẩu mới.'}

@router.get('/admin/warehouses/{warehouse_id}/overview')
def warehouse_overview(warehouse_id: str, request: Request):
    identity = admin_identity(request)
    store = request.app.state.auth_store
    with store.pool.connection() as c:
        warehouse = c.execute('SELECT warehouse_id,name FROM auth_warehouses WHERE warehouse_id=%s', (warehouse_id,)).fetchone()
        if not warehouse:
            raise HTTPException(404, 'Không tìm thấy kho.')
        store.audit(c, identity['user_id'], warehouse_id, 'admin_view_warehouse')
    from backend.app.database import get_connection
    with warehouse_scope(warehouse_id), get_connection() as c:
        summary = {key: c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for key, table in
                   [('product_count', 'packages'), ('day_count', 'inventory_days'),
                    ('session_count', 'inventory_sessions'), ('box_count', 'inventory_box_instances')]}
    return {'warehouse': warehouse, **summary, 'read_only': True}


async def safe_validation_error(request, error):
    details = [{'loc': list(item['loc']), 'msg': item['msg'], 'type': item['type']} for item in error.errors()]
    return JSONResponse({'detail': details}, status_code=422)



class RecoveryEmailChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email: str=Field(min_length=3,max_length=254)
    current_password: str=Field(min_length=1,max_length=128)
    @field_validator('email')
    @classmethod
    def valid_email(cls,value):return normalize_email(value)

@router.put('/auth/email')
def update_recovery_email(body: RecoveryEmailChange,request: Request,response: Response):
    actor=request.state.identity
    if actor['role']!='user':raise HTTPException(403,'Chỉ tài khoản user dùng email khôi phục tại đây.')
    store=request.app.state.auth_store
    store.rate_limit('email-change',actor['user_id'],5,300)
    store.change_email(actor,body.email,body.current_password)
    tasks=getattr(request.app.state,'image_tasks',None)
    if tasks is not None:tasks.cancel_user(actor['user_id'])
    from backend.app.main import camera
    camera.disconnect_warehouse(actor['warehouse_id'])
    response.delete_cookie(COOKIE,path='/',secure=store.settings.secure_cookie,httponly=True,samesite='lax')
    return {'message':'Đã lưu email khôi phục và thu hồi các phiên cũ. Đăng nhập lại để tiếp tục.'}

@router.get('/admin/users/{user_id}/recovery-requests')
def recovery_requests(user_id: str,request: Request):
    actor=admin_identity(request)
    return request.app.state.support_service.recovery_requests(user_id,actor['user_id'])
