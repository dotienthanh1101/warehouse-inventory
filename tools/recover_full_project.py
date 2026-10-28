"""Decrypt a project snapshot into a new folder. Never start or overwrite a DB."""
import argparse
import getpass
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile
from full_backup_crypto import decrypt,safe_name


def recover(backup,destination,password):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Thư mục khôi phục phải mới; không ghi đè dữ liệu đang có.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.project-recovery-',dir=destination.parent) as temporary:
        private=Path(temporary);plain=private/'verified.zip'
        with plain.open('xb') as stream:
            os.chmod(plain,0o600);decrypt(backup,password,stream)
        tree=private/'content';tree.mkdir(mode=0o700)
        with ZipFile(plain) as archive:
            index={i.filename:i for i in archive.infolist()}
            if len(index)!=len(archive.infolist()) or len(index)>500010 or len({n.casefold() for n in index})!=len(index):
                raise ValueError('Archive có file trùng hoặc quá nhiều file.')
            if 'full-manifest.json' not in index or index['full-manifest.json'].file_size>128*1024**2:
                raise ValueError('Thiếu/sai danh mục bản sao.')
            meta=json.loads(archive.read('full-manifest.json'))
            if meta.get('format')!='warehouse-full-project-v1':raise ValueError('Sai định dạng bản sao dự án.')
            entries=meta['files'];names={e['path'] for e in entries}
            if len(names)!=len(entries) or set(index)!=names|{'full-manifest.json'}:raise ValueError('Danh mục file không khớp.')
            total=sum(e['size'] for e in entries)
            if total>60*1024**3 or total+64*1024**2>shutil.disk_usage(private).free:raise ValueError('Không đủ dung lượng khôi phục.')
            for entry in entries:
                path=safe_name(entry['path']);info=index[entry['path']]
                if path.parts[0] not in ('project','data','resources','recovery') or info.file_size!=entry['size'] or (info.external_attr>>16)&0o170000==0o120000:
                    raise ValueError('File ngoài phạm vi hoặc loại file không hợp lệ.')
                target=tree.joinpath(*path.parts);target.parent.mkdir(parents=True,exist_ok=True)
                digest=hashlib.sha256();count=0
                with archive.open(info) as src,target.open('xb') as dst:
                    for chunk in iter(lambda:src.read(1024*1024),b''):
                        count+=len(chunk)
                        if count>entry['size']:raise ValueError('File vượt kích thước khai báo.')
                        digest.update(chunk);dst.write(chunk)
                if count!=entry['size'] or digest.hexdigest()!=entry['sha256']:raise ValueError('File khôi phục không khớp mã kiểm tra.')
        # Preserve original bytes while preventing accidental source database startup.
        project=tree/'project'
        for file in list(project.rglob('*')):
            if not file.is_file():continue
            relative=file.relative_to(project)
            launcher=file.suffix.lower() in ('.bat','.cmd','.ps1') and not any(p in ('.venv','node_modules') for p in relative.parts)
            if launcher or relative.as_posix()=='config/auth.local.json':
                protected=file.with_name(file.name+'.source-copy')
                if protected.exists():raise ValueError('Tên file bảo vệ cấu hình đã có.')
                file.rename(protected)
        (tree/'full-manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
        (tree/'RECOVERY_READ_ME.txt').write_text(
            'Da giai ma va kiem tra file; CHUA khoi phuc PostgreSQL hoac khoi dong backend.\n'
            'project/: ma nguon; data/system.zip: snapshot du lieu; resources/: cache mo hinh.\n'
            'Cau hinh/launcher nguon giu duoi .source-copy de tranh ket noi nham database cu.\n'
            'Lam theo RESTORE_GUIDE; chi khoi phuc database/thu muc MOI.\n',encoding='utf-8')
        destination.mkdir(mode=0o700,exist_ok=False)
        for file in tree.iterdir():shutil.move(str(file),destination/file.name)
    return meta


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='Giải mã và khôi phục file toàn dự án vào thư mục mới.')
    parser.add_argument('--backup',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();password=getpass.getpass('Mat khau MA HOA BACKUP: ')
    meta=recover(args.backup,args.destination,password)
    print('Đã giải mã và kiểm tra file; chưa khởi động backend hoặc ghi vào PostgreSQL.')
    if meta.get('known_missing_images'):print('Bản sao giữ danh sách ảnh lịch sử đã thiếu từ trước.')


if __name__=='__main__':
    try:main()
    except (Exception,KeyboardInterrupt) as error:raise SystemExit('Chưa khôi phục: '+(str(error) or 'Mật khẩu sai hoặc bản sao bị hỏng.'))
