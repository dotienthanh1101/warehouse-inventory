"""Gom cac bo cai cu sau khi doi chieu audit; khong sua ma ung dung/du lieu kho."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from datetime import datetime
from uuid import uuid4
import zipfile


MOVE_NAMES = frozenset({
    'accounts_update', 'admin_accounts_update', 'admin_camera_update',
    'admin_warehouse_view', 'backup_finish_update', 'blue_portal_update',
    'camera_bridge_speed_update', 'camera_gateway_update', 'camera_video_update',
    'email_recovery_update', 'image_queue_update', 'legacy_evidence_update',
    'manual_full_backup_update', 'native_h264_update', 'queue_reload_fix',
    'report_export_update', 'server_capacity_update', 'stable_video_update',
    'storage_quota_update', 'task_notice_update',
})
PLAN_NAME = 'organization_plan_20261027.json'
PLAN_SHA256 = 'd15dd091cc879fee8a3be4701b4d93ef4503762fc06e21dbf8ed81fbacb15312'


class OrganizationError(Exception):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & 0x400
    )


def local_path(root, relative):
    pure = PurePosixPath(relative)
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or ':' in relative or pure.is_absolute()
            or any(x in {'..', '.'} for x in relative.split('/'))):
        raise OrganizationError('Duong dan khong hop le trong ke hoach.')
    result = root.joinpath(*pure.parts)
    for node in (result, *result.parents):
        if node == root:
            break
        if node.exists() or node.is_symlink():
            if is_link(node):
                raise OrganizationError(f'Tu choi link/junction: {relative}')
    return result


def write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    with temporary.open('w', encoding='utf-8', newline='\n') as out:
        json.dump(document, out, ensure_ascii=False, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def source_snapshot(root, guards):
    result = {}
    for entry in guards:
        relative = entry['path']
        path = local_path(root, relative)
        if not path.is_file() or digest(path) != entry['sha256']:
            raise OrganizationError(f'Ma nguon da doi so voi audit: {relative}. Chua don.')
        result[relative] = entry['sha256']
    return result


def scan_targets(root, names):
    files, directories = {}, []
    for name in names:
        top = local_path(root, name)
        if not top.is_dir():
            raise OrganizationError(f'Thieu thu muc can doi chieu: {name}. Chua don.')
        for current, children, leaves in os.walk(top, followlinks=False):
            folder = Path(current)
            if is_link(folder):
                raise OrganizationError(f'Tu choi link/junction: {folder.name}')
            directories.append(folder.relative_to(root).as_posix())
            for name2 in children:
                if is_link(folder / name2):
                    raise OrganizationError('Co link/junction trong bo cai; chua don.')
            for leaf in leaves:
                path = folder / leaf
                if is_link(path) or not path.is_file():
                    raise OrganizationError('Co file dac biet trong bo cai; chua don.')
                relative = path.relative_to(root).as_posix()
                files[relative] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
    return files, sorted(directories)


def verify_audit(actual, expected):
    wanted = {x['path']: x for x in expected}
    # Cache Python bi loai khoi audit; van luu day du vao ZIP truoc khi don.
    visible = {p: v for p, v in actual.items() if '__pycache__' not in PurePosixPath(p).parts}
    if set(visible) != set(wanted):
        raise OrganizationError('Danh sach file trong bo cai da doi; chua don. Can audit moi.')
    for name, entry in wanted.items():
        now = visible[name]
        if now['bytes'] != entry['bytes'] or (
            entry.get('sha256') and now['sha256'] != entry['sha256']
        ):
            raise OrganizationError(f'Bo cai da doi: {name}. Chua don.')


def verify_zip(archive, expected):
    with zipfile.ZipFile(archive) as z:
        if len(z.namelist()) != len(expected) or set(z.namelist()) != set(expected):
            raise OrganizationError('Danh sach ZIP khong khop. Giu nguyen file goc.')
        for name, row in expected.items():
            h = hashlib.sha256()
            count = 0
            with z.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
                    count += len(block)
            if count != row['bytes'] or h.hexdigest() != row['sha256']:
                raise OrganizationError('Noi dung ZIP khong khop. Giu nguyen file goc.')


def validate_manifest(document):
    if document.get('format') != 'warehouse-organization-receipt-v1':
        raise OrganizationError('Bien ban khoi phuc khong hop le.')
    for name in list(document['files']) + document['directories']:
        pure = PurePosixPath(name)
        if not pure.parts or pure.parts[0] not in MOVE_NAMES:
            raise OrganizationError('Bien ban vuot pham vi cac bo cai cu.')


def restore(root, manifest, automatic=False):
    manifest = Path(manifest).resolve()
    document = json.loads(manifest.read_text(encoding='utf-8'))
    validate_manifest(document)
    archive = manifest.parent / 'installed_updates.zip'
    if digest(archive) != document['archive_sha256']:
        raise OrganizationError('ZIP da bi thay doi; khong khoi phuc.')
    verify_zip(archive, document['files'])
    # Kiem tra het xung dot truoc khi ghi file nao.
    for name, row in document['files'].items():
        path = local_path(root, name)
        if path.exists() and (not path.is_file() or digest(path) != row['sha256']):
            raise OrganizationError(f'Co file moi khac noi dung: {name}. Khong ghi de.')
    for name in document['directories']:
        path = local_path(root, name)
        if path.exists() and not path.is_dir():
            raise OrganizationError(f'Xung dot thu muc: {name}. Khong ghi de.')
    for name in document['directories']:
        local_path(root, name).mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for name in document['files']:
            path = local_path(root, name)
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            # Che do xb khong cho ghi de file xuat hien giua luc khoi phuc.
            with z.open(name) as source, path.open('xb') as destination:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    destination.write(block)
    for name, row in document['files'].items():
        if digest(local_path(root, name)) != row['sha256']:
            raise OrganizationError('Chua xac minh duoc file da khoi phuc.')
    document['status'] = 'rolled_back' if automatic else 'restored'
    write_json(manifest, document)
    return document


def remove_archived_file(path):
    # Tach rieng de kiem thu gia lap loi giua qua trinh xoa.
    path.unlink()


def organize(root, plan, apply=False):
    root = Path(root).resolve()
    names = sorted(plan['move_to_archive'])
    if set(names) != MOVE_NAMES or len(names) != len(MOVE_NAMES):
        raise OrganizationError('Ke hoach khong dung 20 bo cai da doi chieu.')
    if not (root / 'backend/app/daily_api.py').is_file() or not (root / 'frontend/package.json').is_file():
        raise OrganizationError('Chon sai thu muc warehouse_inventory.')
    before = source_snapshot(root, plan['unchanged_source_guards'])
    present = [x for x in names if (root / x).exists()]
    if not present:
        return {'status': 'nothing_to_do', 'source_unchanged': True}
    if len(present) != len(names):
        raise OrganizationError('Chi con mot phan bo cai. Chua don; can doi chieu lai.')
    actual, directories = scan_targets(root, names)
    verify_audit(actual, plan['expected_files'])
    if not apply:
        return {'status': 'preview', 'directories': names, 'files': len(actual), 'source_unchanged': True}
    # Giu archive/ cu, benchmark va tat ca du lieu nghiep vu tai dung vi tri.
    tag = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    destination = local_path(root, 'archive/installed_update_packages/' + tag)
    destination.mkdir(parents=True, exist_ok=False)
    archive = destination / 'installed_updates.zip'
    pending = destination / 'installed_updates.zip.pending'
    with zipfile.ZipFile(pending, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in sorted(actual):
            z.write(local_path(root, name), name)
    verify_zip(pending, actual)
    source_snapshot(root, plan['unchanged_source_guards'])
    current_files, current_dirs = scan_targets(root, names)
    if current_files != actual or current_dirs != directories:
        raise OrganizationError('Bo cai dang thay doi; giu nguyen ban roi, chua xoa.')
    os.replace(pending, archive)
    manifest = destination / 'organization_receipt.json'
    document = {
        'format': 'warehouse-organization-receipt-v1', 'status': 'archive_verified',
        'directories': directories, 'files': actual,
        'archive_sha256': digest(archive), 'source_before': before,
        'count_top_level_removed': len(names), 'count_files_archived': len(actual),
        'raw_bytes_archived': sum(x['bytes'] for x in actual.values()),
        'archive_bytes': archive.stat().st_size,
        'inventory_data_read_or_modified': False,
    }
    write_json(manifest, document)
    try:
        for name, row in actual.items():
            path = local_path(root, name)
            if digest(path) != row['sha256']:
                raise OrganizationError('File bo cai da doi truoc khi don.')
            remove_archived_file(path)
        for name in sorted(directories, key=lambda s: len(PurePosixPath(s).parts), reverse=True):
            local_path(root, name).rmdir()
        after = source_snapshot(root, plan['unchanged_source_guards'])
        if after != before:
            raise OrganizationError('Ma nguon van hanh thay doi trong luc don.')
    except BaseException:
        try:
            restore(root, manifest, automatic=True)
        except Exception:
            print(f'CHUA HOAN TAC DU. Giu ZIP va bien ban: {manifest}', file=sys.stderr)
        raise
    document.update(status='completed', source_unchanged=True)
    write_json(manifest, document)
    return {**document, 'manifest': str(manifest)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Nen/xac minh/xoa cac ban roi da luu.')
    parser.add_argument('--restore', type=Path, help='Khoi phuc theo organization_receipt.json.')
    args = parser.parse_args()
    if args.apply and args.restore:
        parser.error('Khong dung --apply va --restore cung luc.')
    try:
        if args.restore:
            restore(args.project.resolve(), args.restore)
            print('DA KHOI PHUC cac bo cai cu; khong ghi de ma ung dung.')
            return
        path = Path(__file__).with_name(PLAN_NAME)
        if digest(path) != PLAN_SHA256:
            raise OrganizationError('File ke hoach khong con dung ban da kiem thu.')
        plan = json.loads(path.read_text(encoding='utf-8'))
        result = organize(args.project, plan, args.apply)
        if result['status'] == 'nothing_to_do':
            print('Khong con 20 thu muc bo cai tai goc. Khong thay doi them.')
        elif result['status'] == 'preview':
            print(f'DA DOI CHIEU: 20 thu muc; {result["files"]} file (gom cache neu co).')
            print('Chi xem truoc. Them --apply de gom cac bo cai cu.')
        else:
            print(f'DA GOM 20 THU MUC BO CAI: {result["count_files_archived"]} file.')
            print('ZIP da doi chieu SHA-256 tung file truoc khi xoa ban roi.')
            print('Ma nguon van hanh giu nguyen: True')
            print('Khong doc/sua database, anh, config hay benchmark.')
            print(f'BIEN BAN: {result["manifest"]}')
    except (OrganizationError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f'CHUA HOAN TAT: {error}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
