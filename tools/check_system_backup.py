"""NFR-11 / DOC-08: one-command, offline backup and isolated restore acceptance.

Run on the deployment host after stopping the backend. Never restore over live
warehouses or existing PostgreSQL tables. This proves only the backup/restore
workflow on the current host, not overall application or 100-user acceptance.
"""
import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg
from psycopg.rows import dict_row
from backup_core import create_backup, restore_check
from system_backup import authorize, ensure_offline, private_json


def run_acceptance(connection, project, storage, public_settings, actor, output=None, progress=print, known_missing=()):
    project = Path(project).resolve()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    output = Path(output).resolve() if output else project.parent / (project.name + '_backups') / 'data' / ('system_' + stamp + '.zip')
    progress('1/2 · Đang tạo bản sao PostgreSQL, dữ liệu và ảnh các kho…')
    manifest = create_backup(connection, project, storage, output, public_settings, actor=actor, **({"known_missing": known_missing} if known_missing else {}))
    progress('Đã tạo bản sao: ' + str(output))
    progress('2/2 · Đang kiểm tra file và phục hồi thử trong vùng cách ly…')
    with tempfile.TemporaryDirectory(prefix='warehouse-backup-acceptance-') as temporary:
        checked = restore_check(connection, output, temporary)
    if checked['backup_id'] != manifest['backup_id'] or checked['source_data_modified'] is not False:
        raise ValueError('Kết quả phục hồi thử không khớp bản sao vừa tạo.')
    result = {
        'format': 'warehouse-backup-acceptance-v1',
        'status': 'preserved_with_known_missing_evidence' if checked.get('known_missing_images') else 'passed',
        'requirement': 'NFR-11',
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'backup_file': str(output),
        'backup_id': checked['backup_id'],
        'checks': {
            'archive_hashes_and_paths': 'passed',
            'postgres_restored_row_content': 'passed',
            'sqlite_integrity_and_table_counts': 'passed',
            'referenced_image_files_present': 'passed',
        },
        'postgres_rows': checked['postgres_rows'],
        'sqlite_database_count': len(checked['sqlite_tables']),
        'referenced_image_count': checked['referenced_images'],
        'source_business_data_modified': False,
        'live_environment_replaced': False,
        'scope_note': 'Kiểm tra sao lưu và phục hồi thử; không thay nghiệm thu camera/AI, trình duyệt, triển khai public hoặc tải 100 user.',
    }
    if checked.get('known_missing_images'):
        result['known_missing_images'] = checked['known_missing_images']
        result['evidence_complete'] = False
        result['checks']['referenced_image_files_present'] = 'incomplete_preexisting_legacy_evidence'
    directory = project / 'docs'
    directory.mkdir(parents=True, exist_ok=True)
    receipt = directory / ('backup_acceptance_' + stamp + '.json')
    private_json(receipt, result)
    return result, receipt


def load_missing_report(path, project, storage):
    """Use a specific read-only scan; never infer an ignore list automatically."""
    path = Path(path)
    if path.stat().st_size > 32 * 1024 ** 2:
        raise ValueError('Báo cáo ảnh thiếu quá lớn.')
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    if report.get('read_only_business_data') is not True or report.get('unreadable_locations'):
        raise ValueError('Cần báo cáo quét đầy đủ, không có vị trí không đọc được.')
    records = report.get('missing_references')
    if not isinstance(records, list) or not records:
        raise ValueError('Báo cáo không có danh sách ảnh thiếu đã kiểm tra.')
    result = []
    for record in records:
        scope, name = record['scope'], record['filename']
        if not isinstance(name, str) or Path(name).name != name or '/' in name or chr(92) in name:
            raise ValueError('Tên ảnh trong báo cáo không hợp lệ.')
        if scope != 'legacy' and (not isinstance(scope, str) or not __import__('re').fullmatch('[0-9a-f]{32}', scope)):
            raise ValueError('Mã kho trong báo cáo không hợp lệ.')
        base = Path(project) if scope == 'legacy' else Path(storage) / scope
        expected = (base / 'captures' / name).resolve()
        if Path(record['expected_path']).resolve() != expected:
            raise ValueError('Báo cáo không thuộc đúng đường dẫn dữ liệu nguồn trên máy này.')
        candidates = report.get('candidates', {})
        if name not in candidates or candidates[name]:
            raise ValueError('Báo cáo còn ảnh có vị trí ứng viên; cần kiểm tra phục hồi ảnh đó trước.')
        result.append(('legacy/captures/' if scope == 'legacy' else 'warehouses/' + scope + '/captures/') + name)
    return sorted(set(result))


def print_result(result, receipt):
    if result.get('known_missing_images'):
        print('\nĐÃ SAO LƯU HIỆN TRẠNG · Chưa khôi phục đủ ảnh cũ.')
        counts = {}
        for path in result['known_missing_images']:
            parts = path.split('/')
            scope = 'Dữ liệu cũ' if parts[0] == 'legacy' else 'Kho lịch sử ' + parts[1]
            counts[scope] = counts.get(scope, 0) + 1
        for scope, count in sorted(counts.items()): print(scope + ': thiếu ' + str(count) + ' ảnh tham chiếu đã ghi rõ trong bản sao.')
        print('Không đánh dấu nghiệm thu đầy đủ NFR-11; thiếu ảnh ngoài danh sách vẫn bị từ chối.')
    else:
        print('\nĐẠT · Sao lưu và phục hồi thử hoàn tất.')
    print('Database SQLite đã kiểm tra: ' + str(result['sqlite_database_count']))
    print('Ảnh tham chiếu còn có đã kiểm tra: ' + str(result['referenced_image_count']))
    print('Biên bản: ' + str(receipt))
    print('Dữ liệu kho nguồn được giữ nguyên. Có thể khởi động lại backend.')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Một lệnh: tạo backup, kiểm tra và phục hồi thử; giữ nguyên dữ liệu đang dùng.')
    parser.add_argument('--admin', default='admin', help='Tên tài khoản admin website.')
    parser.add_argument('--config', type=Path, help='Cấu hình nguồn riêng, nếu có.')
    parser.add_argument('--storage-root', type=Path, help='Thư mục kho nguồn riêng, nếu có.')
    parser.add_argument('--output', type=Path, help='ZIP mới bên ngoài dự án; mặc định tạo tên riêng.')
    parser.add_argument('--missing-report', type=Path, help='Báo cáo quét xác nhận ảnh lịch sử đã thiếu; không áp dụng cho kho user.')
    args = parser.parse_args(argv)
    if args.config:
        os.environ['WAREHOUSE_AUTH_CONFIG_FILE'] = str(args.config.resolve())
    if args.storage_root:
        os.environ['WAREHOUSE_STORAGE_ROOT'] = str(args.storage_root.resolve())
    ensure_offline()
    from backend.app.auth_settings import load_settings
    from backend.app.warehouse_scope import STORAGE_ROOT
    settings = load_settings()
    with psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=10) as connection:
        actor = authorize(connection, args.admin)
        known = load_missing_report(args.missing_report, PROJECT, STORAGE_ROOT) if args.missing_report else ()
        result, receipt = run_acceptance(connection, PROJECT, STORAGE_ROOT, asdict(settings), actor,
                                         args.output, progress=lambda message: print(message, flush=True), known_missing=known)
    print_result(result, receipt)


if __name__ == '__main__':
    try:
        main()
    except psycopg.Error as error:
        print('CHƯA ĐẠT · Kiểm tra kết nối/quyền PostgreSQL. SQLSTATE: ' + str(error.sqlstate or 'không có'), file=sys.stderr)
        raise SystemExit(1)
    except (Exception, KeyboardInterrupt) as error:
        print('CHƯA ĐẠT · ' + str(error), file=sys.stderr)
        print('Chưa hoàn tất nghiệm thu. Nếu đã có ZIP, giữ lại; không phục hồi đè dữ liệu đang dùng.', file=sys.stderr)
        raise SystemExit(1)
