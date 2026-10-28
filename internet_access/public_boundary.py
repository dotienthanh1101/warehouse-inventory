"""Same-origin HTTPS boundary. Does not weaken the existing API/account guard."""
import hmac
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit
from starlette.responses import JSONResponse,RedirectResponse
from starlette.staticfiles import StaticFiles


def validate_origin(origin):
    value=urlsplit(origin)
    if (value.scheme!='https' or not value.hostname or value.username is not None or value.password is not None
            or value.path or value.query or value.fragment or value.port not in (None,443)
            or value.hostname.lower() in ('localhost','127.0.0.1','::1')
            or not all(c.isascii() and (c.isalnum() or c in '.-') for c in value.hostname)):
        raise ValueError('Origin public phải là https://TEN_MIEN, không có đường dẫn hoặc tài khoản.')
    return 'https://'+value.hostname.lower()


def public_file(path):
    if path in ('/','/index.html'):return True
    if any(c in path for c in ('\\','\x00','%')) or any(p.startswith('.') for p in path.split('/') if p):return False
    suffix=Path(path).suffix.lower()
    return (path.startswith('/assets/') and suffix in {'.js','.css','.svg','.png','.jpg','.jpeg','.webp','.ico','.woff','.woff2','.ttf'}) or path in ('/vite.svg','/favicon.ico','/favicon.svg','/favicon.png')


class PublicBoundary:
    def __init__(self,backend,origin,site,health_token):
        self.backend=backend;self.origin=validate_origin(origin)
        self.host=urlsplit(self.origin).hostname
        self.site=Path(site).resolve()
        self.static=StaticFiles(directory=self.site,html=True,follow_symlink=False)
        self.health_token=health_token
        if len(health_token)<32:raise ValueError('Thiếu khóa kiểm tra nội bộ.')

    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan':return await self.backend(scope,receive,send)
        websocket=scope['type']=='websocket'
        if scope['type'] not in ('http','websocket'):
            await send({'type':'websocket.close','code':1008});return
        async def reject(detail,status):
            if websocket:return await send({'type':'websocket.close','code':1008})
            return await JSONResponse({'detail':detail},status_code=status)(scope,receive,send)
        headers={}
        for key,value in scope.get('headers',[]):headers.setdefault(key.lower(),[]).append(value.decode('latin-1'))
        def single(name):
            values=headers.get(name,[])
            return values[0] if len(values)==1 else None
        peer=(scope.get('client') or ('',0))[0]
        host=single(b'host')
        path=scope.get('path','')
        if websocket and path not in ('/api/camera/gateway/video/publish','/api/camera/gateway/video/view'):
            return await reject('WebSocket không được phép.',403)
        if (path=='/_internet/ready' and peer in ('127.0.0.1','::1') and host in ('127.0.0.1:8765','localhost:8765')
                and single(b'cf-connecting-ip') is None
                and hmac.compare_digest((single(b'x-internet-health') or '').encode(),self.health_token.encode())):
            return await JSONResponse({'ready':True},headers={'Cache-Control':'no-store'})(scope,receive,send)
        try:
            parsed=urlsplit('//'+(host or ''))
            valid_host=(parsed.hostname==self.host and parsed.port in (None,443) and parsed.username is None
                        and not parsed.path and not parsed.query and not parsed.fragment)
        except ValueError:valid_host=False
        if not valid_host or peer not in ('127.0.0.1','::1'):
            return await reject('Địa chỉ truy cập không hợp lệ.',421)
        proto=single(b'x-forwarded-proto')
        if proto=='http' and scope.get('method') in ('GET','HEAD'):
            # All redirects use configured origin; never reflected Host/XFH.
            return await RedirectResponse(self.origin+'/',status_code=307)(scope,receive,send)
        if proto!='https':
            return await reject('Hãy sử dụng đường link HTTPS.',426)
        try:visitor=str(ipaddress.ip_address(single(b'cf-connecting-ip') or ''))
        except ValueError:
            return await reject('Thiếu định danh kết nối qua cổng HTTPS.',400)
        if websocket and single(b'origin')!=self.origin:return await reject('Origin không hợp lệ.',403)
        child={**scope,'scheme':'wss' if websocket else 'https','server':(self.host,443),'client':(visitor,0),
            'headers':[(k,v) for k,v in scope.get('headers',[]) if k.lower() not in (b'forwarded',b'x-forwarded-for',b'x-forwarded-host',b'x-forwarded-proto')]}
        async def protected_send(message):
            if message['type']=='http.response.start':
                replace={b'cache-control':b'no-store',b'x-content-type-options':b'nosniff',
                    b'x-frame-options':b'DENY',b'referrer-policy':b'same-origin',
                    b'content-security-policy':("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' wss://"+self.host+"; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'").encode(),
                    b'permissions-policy':b'camera=(), microphone=(), geolocation=()',
                    b'x-robots-tag':b'noindex, nofollow'}
                message={**message,'headers':[(k,v) for k,v in message.get('headers',[]) if k.lower() not in replace]+list(replace.items())}
            await send(message)
        if path.startswith('/api/'):
            return await self.backend(child,receive,protected_send)
        if scope.get('method') in ('GET','HEAD') and public_file(path):
            try:return await self.static(child,receive,protected_send)
            except Exception as error:
                if getattr(error,'status_code',None)!=404:raise
        return await JSONResponse({'detail':'Không tìm thấy nội dung.'},status_code=404)(child,receive,protected_send)
