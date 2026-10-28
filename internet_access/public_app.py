"""Separate launch entrypoint. Original backend files remain untouched."""
import os
import sys
from pathlib import Path
from public_boundary import PublicBoundary,validate_origin

ROOT=Path(os.environ.get('WAREHOUSE_PUBLIC_PROJECT',Path(__file__).resolve().parent.parent)).resolve()
sys.path.insert(0,str(ROOT))
origin=validate_origin(os.environ.get('WAREHOUSE_PUBLIC_ORIGIN',''))
if (os.environ.get('WAREHOUSE_AUTH_ORIGINS')!=origin or os.environ.get('WAREHOUSE_AUTH_SECURE_COOKIE')!='true'):
    raise RuntimeError('Thiếu cấu hình HTTPS/Secure cookie cho tiến trình public.')
from backend.app.auth_settings import load_settings
settings=load_settings()
if settings.origins!=(origin,) or not settings.secure_cookie:raise RuntimeError('Cấu hình xác thực public không khớp.')
from backend.app.daily_api import app as backend
app=PublicBoundary(backend,origin,os.environ['WAREHOUSE_PUBLIC_SITE'],os.environ['WAREHOUSE_PUBLIC_HEALTH_TOKEN'])
