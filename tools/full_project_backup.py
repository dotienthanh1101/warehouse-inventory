"""Manual complete encrypted project backup. Requires the backend stopped."""
import argparse
import getpass
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile,ZIP_DEFLATED

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT));sys.path.insert(0,str(PROJECT/'tools'))
import psycopg
from psycopg.rows import dict_row
from backup_core import create_backup,restore_check,file_sha,encode,reject_link
from check_system_backup import load_missing_report
from system_backup import authorize,ensure_offline,private_json
from full_backup_crypto import encrypt,decrypt,safe_name

VN=timezone(timedelta(hours=7))
MAX_BYTES=50*1024**3
MAX_FILES=500000


def info(path):
    value=path.stat();return value.st_size,value.st_mtime_ns,value.st_ino,value.st_dev


def inventory(root,prefix,project=False,model=False):
    root=Path(root).resolve();result={}
    if not root.exists():return result
    def unreadable(error):raise error
    for folder,dirs,files in os.walk(root,followlinks=False,onerror=unreadable):
        parent=Path(folder)
        for name in list(dirs):
            path=parent/name
            if name=='__pycache__' or (project and parent==root and name in ('data','captures')):
                dirs.remove(name);continue
            reject_link(path)
        for name in files:
            if name.endswith(('.pyc','.pyo')):continue
            path=parent/name
            if model and path.is_symlink():
                actual=path.resolve()
                if not actual.is_relative_to(root):raise ValueError('Liên kết mô hình nằm ngoài cache được chọn.')
            else:reject_link(path);actual=path
            if not stat.S_ISREG(actual.stat().st_mode):raise ValueError('Không sao lưu file thiết bị/pipe: '+str(path))
            key=prefix+'/'+path.relative_to(root).as_posix();safe_name(key)
            result[key]=(actual,info(actual))
    return result


def model_sources():
    from huggingface_hub.constants import HF_HUB_CACHE
    found=[];missing=[]
    for model in ('models--IDEA-Research--grounding-dino-tiny','models--facebook--dinov2-small'):
        folder=Path(HF_HUB_CACHE)/model
        if folder.is_dir() and any(p.is_file() for p in folder.rglob('*.safetensors')):
            found.append((folder,'resources/huggingface/'+model))
        else:missing.append(model)
    home=Path(os.environ.get('TORCH_HOME',Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'torch'))
    checkpoint=home/'hub/checkpoints/loftr_indoor.ckpt'
    if checkpoint.is_file():found.append((checkpoint,'resources/torch/hub/checkpoints/loftr_indoor.ckpt'))
    else:missing.append('loftr_indoor.ckpt')
    return found,missing


def resources_inventory(models):
    result={}
    for path,prefix in models:
        path=Path(path)
        if path.is_file():
            reject_link(path);safe_name(prefix);result[prefix]=(path,info(path))
        else:result.update(inventory(path,prefix,model=True))
    return result


def packages():
    return subprocess.run([sys.executable,'-m','pip','freeze','--all'],check=True,
                          capture_output=True,text=True,timeout=60).stdout


def make_full(connection,project,storage,settings,password,actor,output_root,known=(),models=None):
    ensure_offline()
    project=Path(project).resolve();storage=Path(storage).resolve();output_root=Path(output_root).resolve()
    if output_root.is_relative_to(project) or output_root.is_relative_to(storage):raise ValueError('Đặt bản sao bên ngoài thư mục dự án/kho.')
    output_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    model_list,missing_models=model_sources() if models is None else (models,[])
    items=inventory(project,'project',project=True);resources=resources_inventory(model_list);items.update(resources)
    if len(items)>MAX_FILES or len({n.casefold() for n in items})!=len(items):raise ValueError('Quá nhiều file hoặc trùng tên trên Windows.')
    size=sum(v[0] for _,v in items.values())
    data_size=sum(v[0] for _,v in inventory(project/'data','estimate').values())
    data_size+=sum(v[0] for _,v in inventory(project/'captures','estimate').values())
    if not storage.is_relative_to(project/'data'):data_size+=sum(v[0] for _,v in inventory(storage,'estimate').values())
    if size+data_size>MAX_BYTES or shutil.disk_usage(output_root).free<3*(size+data_size)+512*1024**2:
        raise ValueError('Không đủ chỗ trống dự phòng hoặc nguồn vượt 50 GiB; không xóa bản sao cũ.')
    stamp=datetime.now(VN).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    output=output_root/('project_'+stamp+'.wbackup');pending=output.with_name(output.name+'.partial')
    try:
        with tempfile.TemporaryDirectory(prefix='.full-backup-',dir=output_root) as temporary:
            stage=Path(temporary)/'content';stage.mkdir(mode=0o700)
            print('1/4 · Đang lưu mã nguồn, cấu hình, môi trường và mô hình…',flush=True)
            frozen_packages=packages()
            for name,(source,before) in items.items():
                if info(source)!=before:raise ValueError('Nguồn thay đổi: '+name)
                target=stage.joinpath(*safe_name(name).parts);target.parent.mkdir(parents=True,exist_ok=True)
                with source.open('rb') as src,target.open('xb') as dst:shutil.copyfileobj(src,dst,1024*1024)
                if info(source)!=before:raise ValueError('Nguồn thay đổi khi sao chép: '+name)
            print('2/4 · Đang chụp PostgreSQL, SQLite, ảnh và bản nháp…',flush=True)
            ensure_offline();(stage/'data').mkdir()
            snapshot=stage/'data/system.zip'
            manifest=create_backup(connection,project,storage,snapshot,settings,actor=actor,known_missing=known)
            print('3/4 · Đang phục hồi thử và đối chiếu dữ liệu…',flush=True)
            checked=restore_check(connection,snapshot,temporary)
            if inventory(project,'project',project=True)!={k:v for k,v in items.items() if k.startswith('project/')} or resources_inventory(model_list)!=resources:
                raise ValueError('Mã nguồn, môi trường hoặc mô hình đã đổi trong lượt sao lưu.')
            ensure_offline();(stage/'recovery').mkdir()
            private_json(stage/'recovery/auth-settings.json',settings)
            private_json(stage/'recovery/runtime.json',{'python':sys.version,'executable':sys.executable,'platform':sys.platform,
                'source_project':str(project),'source_storage':str(storage),'missing_model_caches':missing_models})
            (stage/'recovery/requirements-freeze.txt').write_text(frozen_packages,encoding='utf-8')
            listing=[{'path':p.relative_to(stage).as_posix(),'size':p.stat().st_size,'sha256':file_sha(p)}
                     for p in sorted(stage.rglob('*')) if p.is_file()]
            meta={'format':'warehouse-full-project-v1','backup_id':manifest['backup_id'],'created_at':datetime.now(VN).isoformat(),
                  'files':listing,'known_missing_images':checked.get('known_missing_images',[]),'missing_model_caches':missing_models}
            private_json(stage/'full-manifest.json',meta)
            if (stage/'full-manifest.json').stat().st_size>128*1024**2:raise ValueError('Danh mục bản sao vượt giới hạn khôi phục.')
            plain=Path(temporary)/'project.zip'
            with ZipFile(plain,'x',ZIP_DEFLATED) as archive:
                for p in sorted(stage.rglob('*')):
                    if p.is_file():archive.write(p,p.relative_to(stage).as_posix())
            with ZipFile(plain) as archive:
                if archive.testzip() is not None:raise ValueError('Bản ZIP không toàn vẹn.')
            print('4/4 · Đang mã hóa và xác minh bản sao…',flush=True)
            digest=encrypt(plain,pending,password)
            if decrypt(pending,password)!=digest:raise ValueError('Không xác minh được bản mã hóa.')
            with output.open('xb'):pass
            try:os.replace(pending,output)
            except BaseException:output.unlink(missing_ok=True);raise
            os.chmod(output,0o600)
            result={'status':'saved_with_known_gaps' if missing_models or checked.get('known_missing_images') else 'saved_and_verified',
                    'archive':str(output),'bytes':output.stat().st_size,'file_count':len(listing),'encrypted':True,
                    'created_at':datetime.now(VN).isoformat(),'backup_id':meta['backup_id'],
                    'historical_missing_references':len(meta['known_missing_images']),
                    'missing_model_caches':missing_models,'sqlite_databases':len(checked['sqlite_tables']),
                    'referenced_images':checked['referenced_images'],
                    'virtualenv_included':any(n.startswith('project/.venv/') for n in items),
                    'node_modules_included':any('/node_modules/' in n for n in items)}
            private_json(output.with_suffix('.receipt.json'),result)
            return result
    finally:pending.unlink(missing_ok=True)


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='Sao lưu thủ công toàn dự án; không tạo lịch hoặc lưu mật khẩu.')
    parser.add_argument('--missing-report',type=Path)
    args=parser.parse_args(argv);ensure_offline()
    from backend.app.auth_settings import load_settings
    from backend.app.warehouse_scope import STORAGE_ROOT
    settings=load_settings()
    with psycopg.connect(settings.database_url,row_factory=dict_row,connect_timeout=10) as connection:
        actor=authorize(connection,'admin')
        password=getpass.getpass('Mat khau MA HOA BACKUP (12-128 ky tu; giu rieng de khoi phuc): ')
        confirm=getpass.getpass('Nhap lai mat khau MA HOA BACKUP: ')
        if password!=confirm or not 12<=len(password)<=128:raise ValueError('Mật khẩu backup chưa hợp lệ hoặc không khớp.')
        known=load_missing_report(args.missing_report,PROJECT,STORAGE_ROOT) if args.missing_report else []
        result=make_full(connection,PROJECT,STORAGE_ROOT,asdict(settings),password,actor,
                         PROJECT.parent/(PROJECT.name+'_backups')/'full',known)
    print('ĐÃ SAO LƯU TOÀN DỰ ÁN: '+result['archive'])
    print('Dung lượng: '+str(result['bytes'])+' byte; số file: '+str(result['file_count']))
    print('Ảnh lịch sử thiếu đã ghi rõ: '+str(result['historical_missing_references']))
    if result['missing_model_caches']:print('Cache mô hình chưa có trên máy: '+', '.join(result['missing_model_caches']))
    print('Không tạo lịch hoặc lưu mật khẩu. Có thể khởi động lại backend.')


if __name__=='__main__':
    try:main()
    except psycopg.Error as error:raise SystemExit('Chưa sao lưu: lỗi PostgreSQL. SQLSTATE '+str(error.sqlstate or 'không có'))
    except (Exception,KeyboardInterrupt) as error:raise SystemExit('Chưa hoàn tất sao lưu: '+str(error))
