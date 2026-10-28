"""Interactive PostgreSQL setup. Passwords never appear in command arguments."""
import argparse
from contextlib import closing
import getpass
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def import_legacy(warehouse_id):
    """Copy, never move, the legacy warehouse; refuse a nonempty destination."""
    import shutil
    from backend.app.warehouse_scope import STORAGE_ROOT, warehouse_scope
    from backend.app.database import initialize_database
    folder = STORAGE_ROOT / warehouse_id
    destination = folder / 'data' / 'warehouse.db'
    source = ROOT / 'data' / 'warehouse.db'
    if not source.is_file():
        raise ValueError('Không tìm thấy data/warehouse.db cũ.')
    with closing(sqlite3.connect(destination)) as c:
        if c.execute('SELECT COUNT(*) FROM inventory_days').fetchone()[0] or c.execute('SELECT COUNT(*) FROM inventory_sessions').fetchone()[0]:
            raise ValueError('Kho đích đã có phiên/ngày; từ chối ghi đè.')
    target = destination.with_suffix('.importing.db')
    try:
        with closing(sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True)) as old, closing(sqlite3.connect(target)) as new:
            old.backup(new)
            if new.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Bản sao SQLite không hợp lệ.')
        # Existing URLs are relative; preserving names preserves evidence links.
        for relative in ('captures', 'data/reviews', 'data/duplicate_checks'):
            src, dst = ROOT / relative, folder / relative
            if src.is_dir():
                if any(p.is_symlink() for p in src.rglob('*')):
                    raise ValueError('Nguồn dữ liệu có symlink; cần kiểm tra trước khi nhập.')
                shutil.copytree(src, dst, dirs_exist_ok=True)
        os.replace(target, destination)
        with warehouse_scope(warehouse_id):
            initialize_database()
    finally:
        target.unlink(missing_ok=True)

def configure():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from backend.app.auth_settings import CONFIG_FILE
    if CONFIG_FILE.exists():
        print('Đã có config/auth.local.json; giữ nguyên cấu hình.')
        return
    host = input('Địa chỉ PostgreSQL [127.0.0.1]: ').strip() or '127.0.0.1'
    port = int(input('Cổng PostgreSQL [5432]: ').strip() or '5432')
    username = input('Tài khoản quản trị PostgreSQL [postgres]: ').strip() or 'postgres'
    password = getpass.getpass('Mật khẩu PostgreSQL: ')
    application_password = secrets.token_urlsafe(36)
    admin_dsn = make_conninfo(host=host, port=port, dbname='postgres', user=username, password=password, connect_timeout=10)
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_roles WHERE rolname='warehouse_auth_app'").fetchone():
            raise ValueError('Role warehouse_auth_app đã có; cần cấu hình DATABASE_URL thay vì ghi đè mật khẩu.')
        if c.execute("SELECT 1 FROM pg_database WHERE datname='warehouse_accounts'").fetchone():
            raise ValueError('Database warehouse_accounts đã có; không tự ghi đè.')
        c.execute(sql.SQL('CREATE ROLE warehouse_auth_app LOGIN PASSWORD {}').format(sql.Literal(application_password)))
        try:
            c.execute('CREATE DATABASE warehouse_accounts OWNER warehouse_auth_app')
        except Exception:
            c.execute('DROP ROLE warehouse_auth_app')
            raise
    from urllib.parse import quote
    db_url = f'postgresql://warehouse_auth_app:{quote(application_password, safe="")}@{host}:{port}/warehouse_accounts'
    config = {'database_url': db_url, 'secret': secrets.token_urlsafe(64),
              'origins': ['http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8000'],
              'secure_cookie': False, 'registration_open': True, 'pool_size': 20,
              'session_hours': 12, 'max_cameras': 4}
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids silently replacing another setup's secrets.
        with CONFIG_FILE.open('x', encoding='utf-8') as out:
            json.dump(config, out, ensure_ascii=False, indent=2)
    except BaseException:
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            c.execute('DROP DATABASE warehouse_accounts')
            c.execute('DROP ROLE warehouse_auth_app')
        raise
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass
    print('Đã tạo database tài khoản và cấu hình local. Không chia sẻ config/auth.local.json.')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--import-legacy', action='store_true', help='Sao chép kho cũ sang tài khoản user được chỉ định; dừng backend trước.')
    parser.add_argument('--legacy-user', help='Tên tài khoản user nhận bản sao kho cũ; không dùng tài khoản admin.')
    args = parser.parse_args()
    if args.import_legacy and not args.legacy_user:
        raise ValueError('Nhập kho cũ cần --legacy-user TEN_USER. Admin không dùng kho; dữ liệu đã nhập trước đây vẫn được giữ nguyên.')
    configure()
    from backend.app.auth_settings import load_settings
    from backend.app.auth_store import AuthStore
    from backend.app.auth_api import Registration
    store = AuthStore(load_settings())
    store.open()
    try:
        store.initialize()
        with store.pool.connection() as c:
            admin = c.execute("SELECT user_id,username FROM auth_users WHERE role='admin'").fetchone()
        if admin is None:
            username = input('Tên đăng nhập admin: ').strip()
            display_name = input('Tên hiển thị admin: ').strip()
            password = getpass.getpass('Mật khẩu admin (ít nhất 12 ký tự): ')
            if password != getpass.getpass('Nhập lại mật khẩu admin: '):
                raise ValueError('Hai mật khẩu không khớp.')
            data = Registration(username=username, display_name=display_name, password=password, warehouse_name='Kho của admin')
            result = store.register(data.username, data.display_name, data.password, data.warehouse_name, role='admin')
            admin = result['user']
            print('Đã tạo tài khoản quản trị hệ thống; không tạo kho admin.')
        else:
            print('Admin đã tồn tại; không tạo thêm.')
        if args.import_legacy:
            password = getpass.getpass('Mật khẩu admin để nhập kho cũ vào user: ')
            token = store.login(admin['username'], password)
            store.logout(token)
            with store.pool.connection() as c:
                target = c.execute("SELECT w.warehouse_id FROM auth_warehouses w JOIN auth_users u ON u.user_id=w.owner_id WHERE u.username=%s AND u.role='user' AND u.is_active=TRUE", (args.legacy_user.strip().lower(),)).fetchone()
            if target is None:
                raise ValueError('Không tìm thấy user đang hoạt động có kho riêng.')
            import_legacy(target['warehouse_id'])
            print('Đã sao chép kho cũ sang kho user được chọn. Dữ liệu nguồn được giữ nguyên.')
    finally:
        store.close()
    print('Sẵn sàng. Khởi động backend và frontend rồi mở http://127.0.0.1:5173')

if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('Đã dừng thiết lập.')
    except Exception as error:
        from pydantic import ValidationError
        if isinstance(error, ValidationError):
            raise SystemExit('Dữ liệu chưa hợp lệ: ' + '; '.join(item['msg'] for item in error.errors(include_input=False)))
        # Connection exception text can contain connection parameters; keep passwords out of output.
        if error.__class__.__module__.startswith('psycopg'):
            raise SystemExit('Không thiết lập được PostgreSQL. Kiểm tra dịch vụ, cổng và mật khẩu. Không có dữ liệu kho cũ bị xóa.')
        raise SystemExit(f'Chưa hoàn tất: {error}')
