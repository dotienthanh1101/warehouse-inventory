"""Readonly environment inventory. Never reads credentials, DBs, images or model weights."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import sys

MODULES = ('fastapi','uvicorn','psycopg','psycopg_pool','argon2','openpyxl','cryptography',
           'cv2','numpy','PIL','torch','torchvision','transformers','huggingface_hub',
           'kornia','truststore','websockets','imageio_ffmpeg')


def inspect(project, gpu=False):
    project=Path(project).resolve()
    if not (project/'backend/app/daily_api.py').is_file() or not (project/'frontend/package.json').is_file():
        raise ValueError('Không tìm thấy thư mục dự án.')
    packages=sorted([{'name':d.metadata['Name'],'version':d.version}
                     for d in importlib.metadata.distributions() if d.metadata.get('Name')],key=lambda d:d['name'].lower())
    modules={name:importlib.util.find_spec(name) is not None for name in MODULES}
    missing=[name for name,found in modules.items() if not found]
    opencv=[p['name'] for p in packages if p['name'].lower().replace('_','-') in
            ('opencv-python','opencv-contrib-python','opencv-python-headless','opencv-contrib-python-headless')]
    tools={name:bool(shutil.which(name)) for name in ('node','npm.cmd' if sys.platform=='win32' else 'npm','pg_dump','pg_restore','psql')}
    result={'created_at':datetime.now(timezone.utc).isoformat(),'readonly':True,
            'python':platform.python_version(),'platform':platform.system(),
            'module_presence':modules,'missing_modules':missing,'installed_packages':packages,
            'opencv_distributions':opencv,'tools_on_path':tools,
            'local_auth_config_exists':(project/'config/auth.local.json').is_file(),
            'package_lock_present':(project/'frontend/package-lock.json').is_file(),
            'gpu_checked':False,'limitations':['Presence/versions do not prove ABI compatibility or successful model inference',
                'No PostgreSQL connection, camera test, user secrets or environment variable values read',
                'No installed libraries or model weights changed']}
    if gpu and modules['torch']:
        try:
            import torch
            result.update(gpu_checked=True,cuda_available=bool(torch.cuda.is_available()),pytorch_cuda_version=torch.version.cuda)
        except Exception as error:
            result['gpu_check_error_type']=type(error).__name__
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--gpu',action='store_true');args=p.parse_args()
    try:result=inspect(args.project,args.gpu)
    except (ValueError,OSError) as error:raise SystemExit(str(error))
    folder=args.project.resolve()/'docs';folder.mkdir(exist_ok=True)
    name=folder/('environment_manifest_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'.json')
    name.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('CHỈ KIỂM TRA MÔI TRƯỜNG · Không sửa cấu hình, database hoặc thư viện.')
    print('Thiếu module:',', '.join(result['missing_modules']) or 'Không có trong danh sách kiểm tra')
    if len(result['opencv_distributions'])>1:print('Cần kiểm tra nhiều gói OpenCV đang cùng tồn tại; không tự gỡ gói nào.')
    print('BAO CAO:',name)
    return 0


if __name__=='__main__':raise SystemExit(main())
