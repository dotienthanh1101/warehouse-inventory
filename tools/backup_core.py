"""NFR-11: offline data snapshots and safe, isolated recovery.

SQL schemas are taken from trusted application code, never from the archive.
Backup archives contain private warehouse data and password hashes, not the
source database password, session secret or authenticated browser sessions.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import ExitStack, closing
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from psycopg import sql
from psycopg.types.json import Jsonb

FORMAT = 'warehouse-system-backup-v1'
TABLES = ('auth_users', 'auth_warehouses', 'auth_camera_permissions', 'auth_sessions',
          'auth_audit_logs', 'auth_rate_limits', 'support_tickets', 'support_messages', 'auth_image_tasks')
OMITTED = {'auth_sessions', 'auth_rate_limits'}
SETTINGS = ('origins', 'secure_cookie', 'session_hours', 'pool_size', 'registration_open',
            'max_cameras', 'camera_hosts', 'camera_ports', 'max_image_tasks')
MAX_BYTES = 100 * 1024 ** 3
MAX_FILES = 200000


def schemas():
    from backend.app.auth_store import SCHEMA as auth
    from backend.app.support_service import SCHEMA as support
    from backend.app.image_tasks import SCHEMA as tasks
    return auth + '\n' + support + '\n' + tasks


def encode(value):
    def convert(item):
        if isinstance(item, datetime) and item.tzinfo is not None:
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        raise TypeError('Kiểu dữ liệu chưa được hỗ trợ trong bản sao lưu.')
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=convert).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '\\' in name or any(p in ('', '.', '..') for p in name.split('/'))
            or str(path) != name or len(name) > 1000):
        raise ValueError('Đường dẫn trong bản sao lưu không hợp lệ.')
    for part in path.parts:
        if (any(ord(c) < 32 or c in '<>:"|?*' for c in part) or part.endswith((' ', '.'))
                or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)):
            raise ValueError('Tên file không an toàn khi khôi phục: ' + name)
    return path


def reject_link(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Không sao lưu/khôi phục qua symlink hoặc junction: ' + str(path))


def scan(root, prefix, excluded=()):
    if not root.exists():
        return {}
    reject_link(root)
    result = {}
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=lambda e: (_ for _ in ()).throw(e)):
        parent = Path(folder)
        for name in list(dirs):
            path = parent / name
            reject_link(path)
            if any(path.resolve() == p.resolve() for p in excluded):
                dirs.remove(name)
        for name in files:
            path = parent / name
            reject_link(path)
            if name in ('warehouse.db-wal', 'warehouse.db-shm', 'warehouse.db-journal'):
                continue
            relative = prefix + '/' + path.relative_to(root).as_posix()
            safe_name(relative)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Không sao lưu file đặc biệt: ' + str(path))
            result[relative] = (path, (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev))
    return result


def columns(connection, schema):
    rows = connection.execute('''SELECT table_name,column_name,udt_name,is_nullable
        FROM information_schema.columns WHERE table_schema=%s
        ORDER BY table_name,ordinal_position''', (schema,)).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row['table_name'], []).append([row['column_name'], row['udt_name'], row['is_nullable']])
    if set(result) != set(TABLES):
        raise ValueError('Cấu trúc PostgreSQL chưa khớp phiên bản công cụ; không tự bỏ bảng lạ hoặc bảng thiếu.')
    allowed = {'text', 'bool', 'int8', 'int4', 'timestamptz', 'jsonb'}
    if any(col[1] not in allowed for values in result.values() for col in values):
        raise ValueError('Có kiểu dữ liệu PostgreSQL chưa được hỗ trợ; dừng sao lưu.')
    return result


def pg_snapshot(connection, schema):
    layout = columns(connection, schema)
    data, omitted = {}, {}
    for table in TABLES:
        ident = sql.Identifier(schema, table)
        if table in OMITTED:
            omitted[table] = connection.execute(sql.SQL('SELECT count(*) n FROM {}').format(ident)).fetchone()['n']
            data[table] = []
        else:
            rows = connection.execute(sql.SQL('SELECT * FROM {}').format(ident)).fetchall()
            data[table] = sorted(rows, key=lambda row: encode(row))
    if any(row['state'] in ('preparing', 'queued', 'running') for row in data['auth_image_tasks']):
        raise ValueError('Còn tác vụ ảnh chưa kết thúc. Chờ hoàn tất và dừng backend trước khi sao lưu.')
    owners = {row['warehouse_id']: row['owner_id'] for row in data['auth_warehouses']}
    if any(owners.get(row['warehouse_id']) != row['user_id'] for row in data['auth_image_tasks']):
        raise ValueError('Tác vụ không khớp chủ kho; cần kiểm tra dữ liệu nguồn.')
    return {'columns': layout, 'tables': data, 'omitted_session_counts': omitted}


def image_urls(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith('image_url') and isinstance(item, str) and item.startswith('/api/captures/'):
                yield item
            else:
                yield from image_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from image_urls(item)


def image_path(root, url):
    name = unquote(urlsplit(url).path[len('/api/captures/'):])
    if '/' in name or '\\' in name:
        raise ValueError('Đường dẫn ảnh tham chiếu không hợp lệ.')
    safe_name('captures/' + name)
    return root / 'captures' / name


def sqlite_info(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as c:
        c.execute('PRAGMA trusted_schema=OFF')
        if c.execute('PRAGMA quick_check').fetchall() != [('ok',)] or c.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('SQLite không toàn vẹn: ' + str(path))
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts, references = {}, set()
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            counts[table] = c.execute('SELECT count(*) FROM ' + quoted).fetchone()[0]
            names = [r[1] for r in c.execute('PRAGMA table_info(' + quoted + ')')]
            chosen = [name for name in names if name.endswith('image_url') or name.endswith('payload_json')]
            if chosen:
                fields = ','.join('"' + name.replace('"', '""') + '"' for name in chosen)
                for row in c.execute('SELECT ' + fields + ' FROM ' + quoted):
                    for name, value in zip(chosen, row):
                        if value is None:
                            continue
                        if name.endswith('payload_json'):
                            references.update(image_urls(json.loads(value)))
                        else:
                            references.update(image_urls({name: value}))
    return counts, references


def known_legacy_missing(paths, postgres):
    """Only historical legacy/admin warehouses may record pre-existing gaps."""
    if not isinstance(paths, (list, tuple, set)) or len(paths) > MAX_FILES:
        raise ValueError('Danh sách ảnh cũ thiếu không hợp lệ.')
    admins = {row['user_id'] for row in postgres['tables']['auth_users'] if row['role'] == 'admin'}
    retired = {row['warehouse_id'] for row in postgres['tables']['auth_warehouses'] if row['owner_id'] in admins}
    result = set()
    for name in paths:
        if not isinstance(name, str):
            raise ValueError('Đường dẫn ảnh cũ thiếu không hợp lệ.')
        path = safe_name(name)
        parts = path.parts
        historical = (len(parts) == 3 and parts[:2] == ('legacy', 'captures'))
        historical |= (len(parts) == 4 and parts[0] == 'warehouses' and parts[1] in retired and parts[2] == 'captures')
        if not historical:
            raise ValueError('Không được bỏ kiểm tra ảnh thiếu của kho user đang sử dụng.')
        result.add(name)
    return result


def validate_data(root, postgres, known_missing=()):
    reports, references = {}, {}
    for entry in root.rglob('warehouse.db'):
        if entry.parent.name != 'data':
            continue
        relative = entry.relative_to(root).as_posix()
        if not (relative == 'legacy/data/warehouse.db' or re.fullmatch(r'warehouses/[0-9a-f]{32}/data/warehouse.db', relative)):
            continue
        counts, urls = sqlite_info(entry)
        reports[relative] = counts
        references.setdefault(entry.parent.parent, set()).update(urls)
    for tree in (root / 'warehouses', root / 'legacy'):
        if not tree.is_dir():
            continue
        for entry in tree.rglob('*.json'):
            parts = entry.relative_to(root).parts
            is_review = 'reviews' in parts or 'duplicate_checks' in parts
            if is_review:
                if entry.stat().st_size > 64 * 1024 ** 2:
                    raise ValueError('Bản nháp quá lớn để kiểm tra an toàn.')
                item = json.loads(entry.read_text(encoding='utf-8-sig'))
                base = root / 'legacy' if parts[0] == 'legacy' else root / 'warehouses' / parts[1]
                references.setdefault(base, set()).update(image_urls(item))
    for row in postgres['tables']['auth_warehouses']:
        name = 'warehouses/' + row['warehouse_id'] + '/data/warehouse.db'
        if name not in reports:
            raise ValueError('Thiếu SQLite của kho đã đăng ký: ' + row['warehouse_id'])
    for row in postgres['tables']['auth_image_tasks']:
        base = root / 'warehouses' / row['warehouse_id']
        references.setdefault(base, set()).update(image_urls(row['result']))
    missing = {image_path(base, url).relative_to(root).as_posix()
               for base, urls in references.items() for url in urls if not image_path(base, url).is_file()}
    allowed = known_legacy_missing(known_missing, postgres)
    unexpected = missing - allowed
    if unexpected:
        raise ValueError('Thiếu ảnh bằng chứng ngoài danh sách đã kiểm tra; chưa thể xác minh bản sao: ' + '; '.join(sorted(unexpected)[:10]))
    total = sum(len(urls) for urls in references.values())
    result = {'sqlite_tables': reports, 'referenced_images': total - len(missing)}
    if missing:
        result.update(known_missing_images=sorted(missing), referenced_images_total=total,
                      evidence_complete=False)
    return result


def create_backup(connection, project, storage, output, public_settings, *, schema='public', actor='operator', known_missing=()):
    project, storage, output = Path(project).resolve(), Path(storage).resolve(), Path(output).resolve()
    if output.is_relative_to(project) or output.is_relative_to(storage):
        raise ValueError('Lưu backup bên ngoài thư mục dự án và thư mục kho.')
    if output.exists():
        raise ValueError('File sao lưu đích đã có; không ghi đè.')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    roots = [(storage, 'warehouses', ()), (project / 'data', 'legacy/data', (storage,)),
             (project / 'captures', 'legacy/captures', ())]
    def inventory():
        result = {}
        for source, prefix, excluded in roots:
            result.update(scan(source, prefix, excluded))
        folded = [name.casefold() for name in result]
        if len(set(folded)) != len(folded):
            raise ValueError('Tên file trùng khi khôi phục trên Windows.')
        return result
    with tempfile.TemporaryDirectory(prefix='.backup-stage-', dir=output.parent) as folder, ExitStack() as stack:
        stage = Path(folder)
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            connection.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE NOWAIT').format(
                sql.SQL(',').join(sql.Identifier(schema, name) for name in TABLES)))
            postgres = pg_snapshot(connection, schema)
            initial = inventory()
            if len(initial) > MAX_FILES or sum(info[0] for _, info in initial.values()) > MAX_BYTES:
                raise ValueError('Nguồn vượt giới hạn công cụ: 200.000 file hoặc 100 GiB.')
            # Hold writer reservations on all primary databases before copying any file.
            for name, (source, _) in initial.items():
                primary = name == 'legacy/data/warehouse.db' or bool(re.fullmatch(r'warehouses/[0-9a-f]{32}/data/warehouse.db', name))
                if primary:
                    lock = stack.enter_context(closing(sqlite3.connect(source.as_uri() + '?mode=rw', uri=True, timeout=.3)))
                    lock.execute('BEGIN IMMEDIATE')
            for name, (source, _) in initial.items():
                target = stage.joinpath(*safe_name(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                primary = name == 'legacy/data/warehouse.db' or bool(re.fullmatch(r'warehouses/[0-9a-f]{32}/data/warehouse.db', name))
                if primary:
                    reader = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
                    try:
                        with closing(sqlite3.connect(target)) as destination:
                            reader.backup(destination)
                    finally:
                        reader.close()
                else:
                    with source.open('rb') as src, target.open('xb') as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
            final = inventory()
            if set(initial) != set(final):
                raise ValueError('Danh sách file thay đổi trong khi sao lưu; dừng và thử lại khi backend đã tắt.')
            for name, (_, info) in initial.items():
                db = name == 'legacy/data/warehouse.db' or bool(re.fullmatch(r'warehouses/[0-9a-f]{32}/data/warehouse.db', name))
                if (info[2:] if db else info) != (final[name][1][2:] if db else final[name][1]):
                    raise ValueError('File nguồn thay đổi trong khi sao lưu: ' + name)
            (stage / 'postgres.json').write_bytes(encode(postgres))
            if (stage / 'postgres.json').stat().st_size > 64 * 1024 ** 2:
                raise ValueError('Metadata PostgreSQL vượt 64 MiB hỗ trợ trong phiên bản này.')
            (stage / 'settings.json').write_bytes(encode({key: public_settings[key] for key in SETTINGS if key in public_settings}))
            validation = validate_data(stage, postgres, known_missing)
            if validation.get('known_missing_images'):
                (stage / 'missing-evidence.json').write_bytes(encode({'known_missing_images': validation['known_missing_images'],
                    'notice': 'Historical evidence was already missing before this snapshot; not a complete evidence recovery.'}))
            origin = connection.execute('SELECT current_database() name').fetchone()['name']
            manifest = {'format': FORMAT, 'backup_id': uuid4().hex,
                        'created_at': datetime.now(timezone.utc).isoformat(), 'actor': actor,
                        'source_database': origin, 'schema_sha256': sha(schemas().encode()),
                        'validation': validation, 'files': []}
            for file in sorted(stage.rglob('*')):
                if file.is_file():
                    manifest['files'].append({'path': file.relative_to(stage).as_posix(),
                                              'size': file.stat().st_size, 'sha256': file_sha(file)})
            (stage / 'manifest.json').write_bytes(encode(manifest))
            # 'x' mode refuses an output created by another process while staging.
            pending = output.with_name(output.name + '.partial-' + uuid4().hex)
            try:
                with ZipFile(pending, 'x', ZIP_DEFLATED) as archive:
                    for file in sorted(stage.rglob('*')):
                        if file.is_file():
                            archive.write(file, file.relative_to(stage).as_posix())
                with ZipFile(pending) as archive:
                    if archive.testzip() is not None:
                        raise ValueError('Không xác minh được dữ liệu ZIP vừa ghi.')
                # Reserve the final filename exclusively, then replace our reservation.
                with output.open('xb'):
                    pass
                try:
                    os.replace(pending, output)
                except BaseException:
                    output.unlink(missing_ok=True)
                    raise
                os.chmod(output, 0o600)
            finally:
                pending.unlink(missing_ok=True)
        # ExitStack keeps every SQLite write reservation until all copies finish.
    return manifest


def extract_verified(backup, destination):
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    with ZipFile(backup) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 4 or len({i.filename.casefold() for i in infos}) != len(infos):
            raise ValueError('Archive có file trùng hoặc quá nhiều file.')
        index = {i.filename: i for i in infos}
        meta = index.get('manifest.json')
        if not meta or meta.file_size > 32 * 1024 ** 2:
            raise ValueError('Thiếu hoặc sai manifest sao lưu.')
        manifest = json.loads(archive.read(meta))
        if manifest.get('format') != FORMAT or manifest.get('schema_sha256') != sha(schemas().encode()):
            raise ValueError('Bản sao lưu không tương thích cấu trúc ứng dụng hiện tại.')
        entries = manifest.get('files', [])
        if not isinstance(entries, list) or len(entries) > MAX_FILES + 3:
            raise ValueError('Danh sách file sao lưu không hợp lệ.')
        expected = {e['path'] for e in entries}
        if len(expected) != len(entries) or set(index) != expected | {'manifest.json'}:
            raise ValueError('Archive có file thừa hoặc thiếu so với manifest.')
        total = sum(e['size'] for e in entries if type(e.get('size')) is int and e['size'] >= 0)
        if total > MAX_BYTES or total + 64 * 1024 ** 2 > shutil.disk_usage(destination).free:
            raise ValueError('Không đủ dung lượng hoặc archive vượt 100 GiB.')
        for entry in entries:
            name = entry['path']
            path = safe_name(name)
            if not (name in ('postgres.json', 'settings.json', 'missing-evidence.json') or path.parts[0] in ('warehouses', 'legacy')):
                raise ValueError('File ngoài phạm vi sao lưu dữ liệu.')
            item = index[name]
            if name in ('postgres.json', 'settings.json', 'missing-evidence.json') and item.file_size > 64 * 1024 ** 2:
                raise ValueError('Metadata sao lưu vượt giới hạn kiểm tra an toàn.')
            if (type(entry['size']) is not int or entry['size'] < 0 or item.file_size != entry['size']
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Kích thước hoặc loại file trong archive không hợp lệ.')
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with archive.open(item) as src, target.open('xb') as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b''):
                    size += len(chunk)
                    if size > entry['size']:
                        raise ValueError('File giải nén vượt kích thước khai báo.')
                    digest.update(chunk)
                    dst.write(chunk)
            if size != entry['size'] or digest.hexdigest() != entry['sha256']:
                raise ValueError('File sai mã kiểm tra: ' + name)
    postgres = json.loads((destination / 'postgres.json').read_text(encoding='utf-8'))
    if set(postgres.get('tables', {})) != set(TABLES) or any(postgres['tables'][t] for t in OMITTED):
        raise ValueError('Dữ liệu PostgreSQL không hợp lệ hoặc chứa phiên đăng nhập cũ.')
    missing_file = destination / 'missing-evidence.json'
    known = json.loads(missing_file.read_text(encoding='utf-8'))['known_missing_images'] if missing_file.is_file() else ()
    checked = validate_data(destination, postgres, known)
    if checked != manifest['validation']:
        raise ValueError('Bản khôi phục không khớp số liệu đã sao lưu.')
    return manifest, postgres


def restore_rows(connection, schema, postgres):
    if columns(connection, schema) != postgres['columns']:
        raise ValueError('Cấu trúc đích không khớp bản sao lưu.')
    for table in TABLES:
        ident = sql.Identifier(schema, table)
        if connection.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(ident)).fetchone():
            raise ValueError('Bảng đích không trống; không ghi đè dữ liệu.')
        names = [col[0] for col in postgres['columns'][table]]
        types = {col[0]: col[1] for col in postgres['columns'][table]}
        query = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(ident,
            sql.SQL(',').join(map(sql.Identifier, names)), sql.SQL(',').join(sql.Placeholder() for _ in names))
        for row in postgres['tables'][table]:
            if set(row) != set(names):
                raise ValueError('Dòng dữ liệu PostgreSQL thiếu/thừa cột.')
            connection.execute(query, [Jsonb(row[name]) if types[name] == 'jsonb' and row[name] is not None else row[name] for name in names])
    seq = connection.execute('SELECT pg_get_serial_sequence(%s,%s) name', (f'"{schema}"."support_messages"', 'sequence')).fetchone()['name']
    maximum = connection.execute(sql.SQL('SELECT max(sequence) n FROM {}').format(sql.Identifier(schema, 'support_messages'))).fetchone()['n']
    connection.execute('SELECT setval(%s,%s,%s)', (seq, maximum or 1, maximum is not None))
    if encode(pg_snapshot(connection, schema)['tables']) != encode(postgres['tables']):
        raise ValueError('Nội dung PostgreSQL khôi phục không khớp bản sao, không chỉ kiểm tra số dòng.')
    return {t: connection.execute(sql.SQL('SELECT count(*) n FROM {}').format(sql.Identifier(schema, t))).fetchone()['n'] for t in TABLES}


def restore_check(connection, backup, parent):
    with tempfile.TemporaryDirectory(prefix='.restore-check-', dir=parent) as folder:
        target = Path(folder) / 'data'
        manifest, postgres = extract_verified(backup, target)
        schema = 'backup_check_' + uuid4().hex
        with connection.transaction(force_rollback=True):
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            connection.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
            if connection.execute('SELECT current_schema() n').fetchone()['n'] != schema:
                raise ValueError('Không chọn được schema khôi phục cách ly.')
            connection.execute(schemas())
            counts = restore_rows(connection, schema, postgres)
            expected = {key: len(rows) for key, rows in postgres['tables'].items()}
            if counts != expected:
                raise ValueError('Số dòng khôi phục PostgreSQL không khớp.')
        return {'backup_id': manifest['backup_id'], **manifest['validation'],
                'postgres_rows': counts, 'source_data_modified': False}


def empty_database(connection):
    rows = connection.execute("""SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%'
        AND nspname NOT IN ('public','information_schema')""").fetchall()
    objects = connection.execute("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' LIMIT 1""").fetchone()
    functions = connection.execute("""SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' LIMIT 1""").fetchone()
    if rows or objects or functions:
        raise ValueError('Database đích không trống; công cụ không ghi đè database đang dùng.')
