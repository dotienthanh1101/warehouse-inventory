"""Measure warehouse storage without opening databases or deleting files."""
import argparse
import json
import os
import re
import shutil
import stat
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}


def absolute(path):
    # Do not resolve symlinks before the scanner has checked them.
    return Path(os.path.abspath(path))


def unsafe(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & 0x400
    )


def category(relative):
    parts = relative.parts
    suffix = relative.suffix.lower()
    name = relative.name.lower()
    if name.endswith(('.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm',
                      '.db-journal', '.sqlite-wal', '.sqlite-shm',
                      '.sqlite3-wal', '.sqlite3-shm')):
        return 'sqlite'
    if parts and parts[0] == 'captures':
        return 'images' if suffix in IMAGE_EXTENSIONS else 'capture_other'
    if len(parts) > 1 and parts[0] == 'data' and parts[1] == 'reviews':
        return 'reviews'
    return 'other'


def scan(root, exclusions=(), classifier=category):
    root = absolute(root)
    exclusions = {absolute(p) for p in exclusions}
    counts, sizes = Counter(), Counter()
    warnings = []
    warning_count = 0

    def warn(kind, path):
        nonlocal warning_count
        warning_count += 1
        if len(warnings) < 100:
            # No credentials, database contents or draft payloads are read.
            warnings.append({'kind': kind, 'relative_path': str(path.relative_to(root))})

    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return {'exists': False, 'complete': True, 'bytes': 0, 'file_count': 0,
                'categories': {}, 'warning_count': 0, 'warnings': []}
    except OSError:
        return {'exists': None, 'complete': False, 'bytes': 0, 'file_count': 0,
                'categories': {}, 'warning_count': 1,
                'warnings': [{'kind': 'unreadable_root', 'relative_path': '.'}]}
    if unsafe(root_info) or not stat.S_ISDIR(root_info.st_mode):
        warn('linked_or_non_directory_root', root)
        pending = []
    else:
        pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    path = folder / entry.name
                    if path in exclusions:
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        warn('unreadable_or_changed', path)
                        continue
                    if unsafe(info):
                        warn('link_or_junction_skipped', path)
                    elif stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        key = classifier(path.relative_to(root))
                        counts[key] += 1
                        sizes[key] += info.st_size
                    else:
                        warn('non_regular_file_skipped', path)
        except OSError:
            warn('unreadable_directory', folder)
    return {'exists': True, 'complete': warning_count == 0,
            'bytes': sum(sizes.values()), 'file_count': sum(counts.values()),
            'categories': {k: {'bytes': sizes[k], 'file_count': counts[k]}
                           for k in sorted(counts)},
            'warning_count': warning_count, 'warnings': warnings}


def disk_info(path):
    path = absolute(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        usage = shutil.disk_usage(path)
        return {'total_bytes': usage.total, 'free_bytes': usage.free,
                'volume_id': str(path.stat().st_dev)}
    except OSError:
        return None


def warehouse_list(storage):
    try:
        info = storage.lstat()
    except FileNotFoundError:
        return [], [], True
    except OSError:
        return [], ['Không đọc được thư mục kho.'], False
    if unsafe(info) or not stat.S_ISDIR(info.st_mode):
        return [], ['Thư mục kho là liên kết hoặc không phải thư mục; không quét.'], False
    roots, problems = [], []
    try:
        with os.scandir(storage) as entries:
            for entry in entries:
                # Only canonical warehouse IDs count as tenant directories.
                if re.fullmatch('[0-9a-f]{32}', entry.name):
                    roots.append(storage / entry.name)
                else:
                    problems.append('Có mục ngoài định dạng mã kho; không tự nhận là kho.')
    except OSError:
        return [], ['Không liệt kê được đầy đủ các kho.'], False
    return sorted(roots), problems, not problems


def build_report(project, storage, backups):
    project, storage, backups = map(absolute, (project, storage, backups))
    if not (project / 'backend' / 'app' / 'database.py').is_file():
        raise ValueError('Sai thư mục dự án: thiếu backend/app/database.py.')
    roots, issues, listed = warehouse_list(storage)
    warehouses = [{'warehouse_id': p.name, **scan(p)} for p in roots]
    # The nested per-warehouse tree is counted only once.
    legacy_data = scan(project / 'data', exclusions=(storage,),
                       classifier=lambda p: category(Path('data') / p))
    legacy_captures = scan(project / 'captures',
                           classifier=lambda p: category(Path('captures') / p))
    backup_scan = scan(backups, classifier=lambda _: 'backups')
    all_scans = warehouses + [legacy_data, legacy_captures, backup_scan]
    return {
        'format_version': VERSION,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'read_only': True,
        'measurement': 'logical_file_bytes_live_scan',
        'complete': listed and all(s['complete'] for s in all_scans),
        'paths': {'project': str(project), 'warehouse_storage': str(storage),
                  'backups': str(backups)},
        'warehouses': warehouses,
        'warehouse_total_bytes': sum(w['bytes'] for w in warehouses),
        'legacy': {'data': legacy_data, 'captures': legacy_captures},
        'backups': backup_scan,
        'disks': {'project': disk_info(project), 'warehouses': disk_info(storage),
                  'backups': disk_info(backups)},
        'listing_warnings': issues,
        'quota_enforced': False,
        'evidence_references_checked': False,
        'notes': [
            'Chỉ đo kích thước file; không đọc mật khẩu, PostgreSQL hay nội dung SQLite.',
            'Không tính dữ liệu máy chủ PostgreSQL; đây không phải tổng dung lượng toàn hệ thống.',
            'Ảnh/bản nháp còn được tham chiếu chưa được phân loại; không dùng báo cáo này để xóa.',
            'Số đo trực tiếp có thể thay đổi khi ứng dụng đang ghi dữ liệu.',
            'Dung lượng logic có thể khác dung lượng chiếm trên ổ do nén hoặc liên kết cứng.',
            'Chưa áp dụng hạn mức hoặc thời hạn xóa dữ liệu.'
        ]
    }


def mib(size):
    return f'{size / 1024**2:,.2f} MiB'


def main():
    parser = argparse.ArgumentParser(description='Đo dung lượng, không xóa dữ liệu.')
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--storage', type=Path)
    parser.add_argument('--backups', type=Path)
    args = parser.parse_args()
    project = absolute(args.project)
    storage = args.storage or Path(os.environ.get('WAREHOUSE_STORAGE_ROOT', project / 'data' / 'warehouses'))
    backups = args.backups or project.parent / (project.name + '_backups')
    report = build_report(project, storage, backups)
    print('CHỈ ĐO DUNG LƯỢNG · Không sửa/xóa dữ liệu kiểm kê.')
    print(f"Số thư mục kho: {len(report['warehouses'])}")
    for item in report['warehouses']:
        print(f"  Kho {item['warehouse_id']}: {mib(item['bytes'])} | {item['file_count']} file")
        for key, label in [('images', 'Ảnh'), ('reviews', 'Bản nháp'), ('sqlite', 'SQLite')]:
            part = item['categories'].get(key, {'bytes': 0, 'file_count': 0})
            print(f"    {label}: {mib(part['bytes'])} | {part['file_count']} file")
    legacy = report['legacy']
    print('Dữ liệu cũ ngoài các kho:', mib(legacy['data']['bytes'] + legacy['captures']['bytes']))
    print('Thư mục sao lưu:', mib(report['backups']['bytes']))
    for name, label in [('project', 'dự án'), ('warehouses', 'kho'), ('backups', 'sao lưu')]:
        disk = report['disks'][name]
        print(f"Trống trên ổ {label}: " + (mib(disk['free_bytes']) if disk else 'Chưa đo được'))
    print('Lưu ý: các dòng dung lượng trống có thể cùng một ổ; không cộng chúng với nhau.')
    print('Quét đầy đủ các vùng đã chọn:', 'CÓ' if report['complete'] else 'CHƯA · xem cảnh báo trong JSON')
    output_dir = project / 'docs'
    if output_dir.exists() and unsafe(output_dir.lstat()):
        raise ValueError('docs là liên kết; không ghi báo cáo vào đường dẫn này.')
    output_dir.mkdir(exist_ok=True)
    output = output_dir / ('storage_audit_' + datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8] + '.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print('Báo cáo:', output)
    print('Chưa áp dụng hạn mức. Không cần dừng hoặc khởi động lại backend.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f'Chưa đo được dung lượng: {error}')
