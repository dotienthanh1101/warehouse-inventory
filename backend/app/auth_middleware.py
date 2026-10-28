"""Pure ASGI guard keeps context alive through threadpool and streamed responses."""
import hmac
import logging
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from backend.app.auth_store import COOKIE
from backend.app.warehouse_scope import warehouse_scope
from backend.app.account_gate import account_gate

PUBLIC = {'/api/health', '/api/auth/config', '/api/auth/login', '/api/auth/register'}
PUBLIC |= {'/api/support/guest', '/api/support/guest/read', '/api/support/guest/reply'}
# These three routes authenticate a one-time pairing code or scoped bearer,
# not browser cookies. Origin checks still apply to every unsafe request.
PUBLIC |= {'/api/camera/gateway/claim','/api/camera/gateway/frame','/api/camera/gateway/stop'}
UNSAFE = {'POST', 'PUT', 'PATCH', 'DELETE'}
logger = logging.getLogger(__name__)

class AccountMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope, receive=receive)
        path = scope.get('path', '')
        protected = path.startswith('/api/') or path in ('/docs', '/redoc', '/openapi.json') or path.startswith('/docs/')
        if not protected or path == '/api/health':
            return await self.app(scope, receive, send)
        store = getattr(scope['app'].state, 'auth_store', None)
        try:
            if store is None:
                raise HTTPException(503, 'Chưa khởi tạo tài khoản. Chạy tools/setup_accounts.py trước.')
            if scope['method'] in UNSAFE:
                if request.headers.get('origin') not in store.settings.origins:
                    raise HTTPException(403, 'Địa chỉ giao diện không được phép gửi thao tác.')
            if path in PUBLIC:
                identity = None
            else:
                token = request.cookies.get(COOKIE, '')
                identity = await run_in_threadpool(store.authenticate, token)
                expected_warehouse = request.headers.get('x-warehouse-id')
                if expected_warehouse and expected_warehouse != identity['warehouse_id']:
                    raise HTTPException(409, 'Tài khoản đã thay đổi ở tab khác. Đăng nhập lại.', headers={'X-Auth-Context-Changed': '1'})
                if scope['method'] in UNSAFE:
                    if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), store.csrf_token(token).encode()):
                        raise HTTPException(403, 'Phiên thao tác không hợp lệ. Tải lại trang.')
                if path.startswith('/api/admin/') and identity['role'] != 'admin':
                    raise HTTPException(403, 'Chỉ admin được sử dụng chức năng này.')
                if identity['role'] == 'admin' and not (
                    path.startswith('/api/auth/') or path.startswith('/api/admin/')
                    or path in ('/docs', '/redoc', '/openapi.json') or path.startswith('/docs/')
                ):
                    raise HTTPException(403, 'Admin chỉ quản trị hệ thống. Dùng tài khoản user để kiểm kê kho.')
                scope.setdefault('state', {})['identity'] = identity
                if identity.get('must_change_password') and path not in ('/api/auth/me','/api/auth/logout','/api/auth/change-password'):
                    raise HTTPException(403,'Bạn cần đổi mật khẩu tạm trước khi sử dụng hệ thống.')
        except HTTPException as error:
            response = JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)
            response.headers['Cache-Control'] = 'no-store'
            return await response(scope, receive, send)
        except Exception:
            logger.exception('Không kiểm tra được phiên đăng nhập')
            return await JSONResponse({'detail': 'Không kiểm tra được tài khoản. Hãy thử lại.'}, status_code=503,
                                      headers={'Cache-Control': 'no-store'})(scope, receive, send)

        async def protected_send(message):
            if message['type'] == 'http.response.start':
                headers = [(k,v) for k,v in message.get('headers', []) if k.lower() not in (b'cache-control',)]
                headers += [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'same-origin')]
                message = {**message, 'headers': headers}
            await send(message)

        async def dispatch():
            if identity and identity['role'] == 'user':
                with warehouse_scope(identity['warehouse_id']):
                    return await self.app(scope, receive, protected_send)
            return await self.app(scope, receive, protected_send)
        try:
            # Queue submission never commits inventory. It rechecks authorization
            # in PostgreSQL before admission, so a retry can return status while
            # the worker holds the user's write barrier for an active task.
            if identity and scope['method'] in UNSAFE and identity['role']=='user' and path!='/api/tasks':
                async with account_gate(scope['app'],identity['user_id']):
                    await run_in_threadpool(store.authenticate,token)
                    return await dispatch()
            return await dispatch()
        except HTTPException as error:
            return await JSONResponse({'detail':error.detail},status_code=error.status_code,headers={**(error.headers or {}),'Cache-Control':'no-store'})(scope,receive,send)
