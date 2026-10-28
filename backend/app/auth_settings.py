"""Load local secrets; production settings can be supplied by environment."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / 'config' / 'auth.local.json'

@dataclass(frozen=True)
class Settings:
    database_url: str
    secret: str
    origins: tuple[str, ...]
    secure_cookie: bool = False
    session_hours: int = 12
    pool_size: int = 20
    registration_open: bool = True
    max_cameras: int = 4
    camera_hosts: tuple[str, ...] = ()
    camera_ports: tuple[int, ...] = (80, 443, 8080, 8081)
    max_image_tasks: int = 8

    def validate(self):
        if not self.database_url.startswith(('postgresql://', 'postgres://')):
            raise ValueError('Auth cần kết nối PostgreSQL.')
        if len(self.secret) < 48:
            raise ValueError('Khóa phiên đăng nhập phải có ít nhất 48 ký tự.')
        if not self.origins:
            raise ValueError('Chưa cấu hình địa chỉ giao diện được phép.')
        for origin in self.origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError('Địa chỉ giao diện phải là origin không có đường dẫn.')
            if parsed.hostname not in ('localhost', '127.0.0.1', '::1') and (parsed.scheme != 'https' or not self.secure_cookie):
                raise ValueError('Địa chỉ ngoài localhost cần HTTPS và secure_cookie=true.')
        if not 1 <= self.pool_size <= 100 or not 1 <= self.session_hours <= 24:
            raise ValueError('Cấu hình pool hoặc thời hạn phiên không hợp lệ.')
        if not self.camera_ports or any(type(p) is not int or not 1 <= p <= 65535 for p in self.camera_ports):
            raise ValueError('Danh sách cổng camera không hợp lệ.')
        if type(self.max_image_tasks) is not int or not 1 <= self.max_image_tasks <= 100:
            raise ValueError('Số tác vụ ảnh tối đa phải từ 1 đến 100.')
        return self

def load_settings():
    config_file = Path(os.environ.get('WAREHOUSE_AUTH_CONFIG_FILE', CONFIG_FILE))
    data = json.loads(config_file.read_text(encoding='utf-8')) if config_file.is_file() else {}
    return Settings(
        database_url=os.environ.get('WAREHOUSE_AUTH_DATABASE_URL', data.get('database_url', '')),
        secret=os.environ.get('WAREHOUSE_AUTH_SECRET', data.get('secret', '')),
        origins=tuple(os.environ['WAREHOUSE_AUTH_ORIGINS'].split(',')) if 'WAREHOUSE_AUTH_ORIGINS' in os.environ else tuple(data.get('origins', ['http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8000'])),
        secure_cookie=os.environ.get('WAREHOUSE_AUTH_SECURE_COOKIE', str(data.get('secure_cookie', False))).lower() == 'true',
        session_hours=int(data.get('session_hours', 12)), pool_size=int(data.get('pool_size', 20)),
        registration_open=bool(data.get('registration_open', True)), max_cameras=int(data.get('max_cameras', 4)),
        camera_hosts=tuple(data.get('camera_hosts', [])),
        camera_ports=tuple(data.get('camera_ports', [80, 443, 8080, 8081])),
        max_image_tasks=int(data.get('max_image_tasks',8)),
    ).validate()
