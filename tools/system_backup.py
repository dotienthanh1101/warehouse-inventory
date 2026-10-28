"""Offline backup, verification and recovery into an empty, separate environment."""
import argparse
import getpass
import json
import os
import re
import secrets
import socket
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg
from psycopg.rows import dict_row
from backup_core import (SETTINGS, create_backup, extract_verified, restore_check,
                         empty_database, restore_rows, schemas, encode)


def ensure_offline():
    with socket.socket() as connection:
        connection.settimeout(.3)
        if connection.connect_ex(('127.0.0.1', 8000)) == 0:
            raise ValueError('Backend đang chạy ở cổng 8000. Chờ tác vụ hoàn tất rồi Ctrl+C ở cửa sổ backend.')


def authorize(connection, username):
    from backend.app.auth_store import verify_password
    password = getpass.getpass('Mat khau admin WEBSITE: ')
    row = connection.execute("SELECT user_id,password_hash FROM auth_users WHERE username=%s AND role='admin' AND is_active=TRUE",
                             (username.strip().lower(),)).fetchone()
    if not row or not verify_password(row['password_hash'], password):
        raise ValueError('Không xác thực được admin website đang hoạt động.')
    connection.commit()
    return row['user_id']


def private_json(path, data):
    with path.open('xb') as stream:
        stream.write(encode(data))
    os.chmod(path, 0o600)


def target_url(host, port, username, password, database):
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]+', host) or not 1 <= port <= 65535:
        raise ValueError('Địa chỉ PostgreSQL đích không hợp lệ.')
    host_part = '[' + host + ']' if ':' in host else host
    return f'postgresql://{quote(username, safe="")}:{quote(password, safe="")}@{host_part}:{port}/{quote(database, safe="")}'


def restore_new(backup, destination, connection, database_url, project=PROJECT):
    """Caller supplies a new empty database. Never overwrite existing data/config."""
    destination = Path(destination).resolve()
    project = Path(project).resolve()
    source_storage = Path(os.environ.get('WAREHOUSE_STORAGE_ROOT', project / 'data' / 'warehouses')).resolve()
    if destination.is_relative_to(project) or destination.is_relative_to(source_storage) or destination.exists():
        raise ValueError('Chọn thư mục khôi phục mới bên ngoài dự án; không ghi đè thư mục đã có.')
    with connection.transaction():
        empty_database(connection)
        # Verify/extract only after the target database is proved empty.
        manifest, postgres = extract_verified(backup, destination)
        database = connection.execute('SELECT current_database() n').fetchone()['n']
        if database == manifest['source_database']:
            raise ValueError('Database khôi phục phải có tên khác database nguồn trong backup.')
        connection.execute('SET LOCAL search_path TO public')
        connection.execute(schemas())
        counts = restore_rows(connection, 'public', postgres)
        if counts != {key: len(rows) for key, rows in postgres['tables'].items()}:
            raise ValueError('Số dòng PostgreSQL khôi phục không khớp.')
        config = json.loads((destination / 'settings.json').read_text(encoding='utf-8'))
        config = {key: config[key] for key in SETTINGS if key in config}
        config.update(database_url=database_url, secret=secrets.token_urlsafe(48))
        private_json(destination / 'auth.local.json', config)
        # No passwords/secrets are embedded in the launcher or printed to console.
        def batch_path(path):
            return str(path).replace('%', '%%')
        command = '\r\n'.join([
            '@echo off', 'setlocal DisableDelayedExpansion', 'chcp 65001 >nul',
            'if not exist "%~dp0RESTORE_COMPLETE.json" (',
            '  echo Restore chua hoan tat. Khong khoi dong backend.', '  pause', '  exit /b 1', ')',
            'set "WAREHOUSE_AUTH_DATABASE_URL="', 'set "WAREHOUSE_AUTH_SECRET="',
            'set "WAREHOUSE_AUTH_ORIGINS="', 'set "WAREHOUSE_AUTH_SECURE_COOKIE="',
            f'set "WAREHOUSE_AUTH_CONFIG_FILE={batch_path(destination / "auth.local.json")}"',
            f'set "WAREHOUSE_STORAGE_ROOT={batch_path(destination / "warehouses")}"',
            f'cd /d "{batch_path(project)}"',
            '".venv\\Scripts\\python.exe" -m uvicorn backend.app.daily_api:app --host 127.0.0.1 --port 8000 --no-access-log',
            'endlocal', '',
        ])
        (destination / 'start_restored_backend.cmd').write_bytes(command.encode('utf-8'))
    # This marker is created only after PostgreSQL committed successfully.
    result = {'backup_id': manifest['backup_id'], 'postgres_rows': counts,
              **manifest['validation'], 'restored_at': datetime.now(timezone.utc).isoformat()}
    private_json(destination / 'RESTORE_COMPLETE.json', result)
    return result


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Sao lưu/kiểm tra khôi phục hệ thống; không ghi đè dữ liệu đang dùng.')
    parser.add_argument('--config', type=Path, help='Cấu hình nguồn khác mặc định, nếu có.')
    parser.add_argument('--storage-root', type=Path, help='Nơi lưu kho nguồn khác mặc định, nếu có.')
    parser.add_argument('--admin', default='admin')
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('create')
    make.add_argument('--output', type=Path)
    for action in ('verify', 'restore-check'):
        commands.add_parser(action).add_argument('--backup', type=Path, required=True)
    restore = commands.add_parser('restore-new')
    restore.add_argument('--backup', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    restore.add_argument('--target-database', required=True)
    restore.add_argument('--target-host', default='127.0.0.1')
    restore.add_argument('--target-port', type=int, default=5432)
    restore.add_argument('--target-user', default='warehouse_auth_app')
    args = parser.parse_args()
    if args.config:
        os.environ['WAREHOUSE_AUTH_CONFIG_FILE'] = str(args.config.resolve())
    if args.storage_root:
        os.environ['WAREHOUSE_STORAGE_ROOT'] = str(args.storage_root.resolve())
    if args.command == 'verify':
        with tempfile.TemporaryDirectory(prefix='warehouse-backup-verify-') as folder:
            manifest, postgres = extract_verified(args.backup, Path(folder) / 'data')
            print('Đã kiểm tra file và dữ liệu còn có: ' + manifest['backup_id'])
            if manifest['validation'].get('known_missing_images'):
                print('CẢNH BÁO: còn ảnh lịch sử đã thiếu trước khi sao lưu; danh sách được giữ trong bản sao.')
            print(json.dumps(manifest['validation'], ensure_ascii=False, indent=2))
        return
    ensure_offline()
    if args.command == 'restore-new':
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', args.target_database):
            raise ValueError('Tên database khôi phục không hợp lệ.')
        password = getpass.getpass('Mat khau PostgreSQL cua tai khoan dich: ')
        url = target_url(args.target_host, args.target_port, args.target_user, password, args.target_database)
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as connection:
            result = restore_new(args.backup, args.destination, connection, url)
        print('Đã khôi phục sang database/thư mục riêng. Nguồn cũ được giữ nguyên.')
        if result.get('known_missing_images'):
            print('CẢNH BÁO: bản sao có ảnh lịch sử đã thiếu từ trước; chưa khôi phục đủ bằng chứng.')
        print('Kết quả: ' + str(args.destination.resolve() / 'RESTORE_COMPLETE.json'))
        return
    from backend.app.auth_settings import load_settings
    from backend.app.warehouse_scope import STORAGE_ROOT
    settings = load_settings()
    with psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=10) as connection:
        actor = authorize(connection, args.admin)
        if args.command == 'create':
            output = args.output or PROJECT.parent / (PROJECT.name + '_backups') / 'data' / (
                'system_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8] + '.zip')
            print('Đang sao lưu PostgreSQL, SQLite và kiểm tra các ảnh tham chiếu…', flush=True)
            manifest = create_backup(connection, PROJECT, STORAGE_ROOT, output, asdict(settings), actor=actor)
            print('Đã tạo bản sao lưu: ' + str(output.resolve()))
            print('Mã bản sao: ' + manifest['backup_id'])
            print('Ảnh tham chiếu đã kiểm tra: ' + str(manifest['validation']['referenced_images']))
        else:
            print('Đang khôi phục thử trong vùng cách ly; chờ kết quả kiểm tra…', flush=True)
            with tempfile.TemporaryDirectory(prefix='warehouse-restore-check-') as folder:
                result = restore_check(connection, args.backup, folder)
            print('Đã phục hồi thử và đối chiếu PostgreSQL/SQLite/ảnh trong bản cách ly.')
            if result.get('known_missing_images'):
                print('CẢNH BÁO: dữ liệu còn có đã phục hồi; chưa khôi phục đủ ảnh lịch sử đã thiếu từ trước.')
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except psycopg.Error as error:
        print('Chưa hoàn tất. Kiểm tra PostgreSQL/quyền kết nối. SQLSTATE: ' + str(error.sqlstate or 'không có'), file=sys.stderr)
        raise SystemExit(1)
    except (Exception, KeyboardInterrupt) as error:
        print('Chưa hoàn tất: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
