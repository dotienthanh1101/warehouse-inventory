"""FR-35 HTTPS trial launcher; one backend, built React, no config edits."""
import argparse
from datetime import datetime,timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

HERE=Path(__file__).resolve().parent
PORT=8765
URL_PATTERN=re.compile(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com\b')


def verify_source(project):
    baseline=json.loads((HERE/'baseline.json').read_text(encoding='utf-8'))
    bad=[name for name,sha in baseline.items() if not (project/name).is_file() or hashlib.sha256((project/name).read_bytes()).hexdigest()!=sha]
    if bad:raise ValueError('Mã nguồn lệch bản đã đối chiếu; chưa công khai: '+', '.join(bad))
    for name in ('frontend/package.json','frontend/index.html','frontend/node_modules/.bin/vite.cmd' if os.name=='nt' else 'frontend/node_modules/.bin/vite'):
        if not (project/name).is_file():raise ValueError('Thiếu '+name+'. Chưa công khai.')


def ensure_stopped():
    for port in (8000,PORT):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.5):pass
        except OSError:continue
        raise ValueError(f'Cổng {port} đang được dùng. Đợi tác vụ xong và dừng backend cũ bằng Ctrl+C trước.')


def find_cloudflared():
    candidate=shutil.which('cloudflared')
    if candidate:return candidate
    if os.name=='nt':
        for variable in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA'):
            base=os.environ.get(variable)
            if not base:continue
            for relative in ('cloudflared/cloudflared.exe','Microsoft/WinGet/Links/cloudflared.exe'):
                path=Path(base)/relative
                if path.is_file():return str(path)
    raise ValueError('Chưa tìm thấy cloudflared. Chạy: winget install --id Cloudflare.cloudflared --exact. Sau đó mở terminal mới.')


def stop(process):
    if process is None or process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
    else:process.terminate()
    try:process.wait(timeout=20)
    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)


def readiness(token):
    connection=http.client.HTTPConnection('127.0.0.1',PORT,timeout=2)
    try:
        connection.request('GET','/_internet/ready',headers={'X-Internet-Health':token})
        response=connection.getresponse();data=response.read()
        return response.status==200 and json.loads(data).get('ready') is True
    except (OSError,http.client.HTTPException,ValueError):return False
    finally:connection.close()


def read_tunnel(pipe,log,found):
    try:
        for raw in pipe:
            log.write(raw);log.flush()
            match=URL_PATTERN.search(raw.decode('utf-8',errors='replace'))
            if match:found.append(match.group())
    except (OSError,ValueError):pass


def run(project):
    ensure_stopped();verify_source(project)
    executable=find_cloudflared()
    # Do not replace or edit an existing operator tunnel configuration.
    for name in ('config.yml','config.yaml'):
        if (Path.home()/'.cloudflared'/name).exists():
            raise ValueError('Đã có cấu hình cloudflared riêng. Chưa chạy Quick Tunnel; giữ nguyên cấu hình đó để đối chiếu trước.')
    sys.path.insert(0,str(project))
    from backend.app.auth_settings import load_settings
    settings=load_settings()
    if os.environ.get('WAREHOUSE_STORAGE_ROOT') or os.environ.get('WAREHOUSE_AUTH_CONFIG_FILE'):
        raise ValueError('Terminal đang có cấu hình chuyển vùng dữ liệu. Mở terminal bình thường trước khi chạy public.')
    # Check DB connectivity before exposing any application route.
    import psycopg
    try:
        with psycopg.connect(settings.database_url,connect_timeout=10) as c:c.execute('SELECT 1')
    except psycopg.Error as error:raise ValueError('Chưa kết nối được PostgreSQL của ứng dụng. Chưa công khai.') from error
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]
    work=HERE/'runtime'/stamp;work.mkdir(parents=True)
    site=work/'site';backend=tunnel=None;thread=None;report=None
    report_path=project/'docs'/('internet_run_'+stamp+'.json');report_path.parent.mkdir(exist_ok=True)
    try:
        print('1/3 · Build giao diện React hiện tại…',flush=True)
        npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
        if not npm:raise ValueError('Chưa tìm thấy npm trong terminal hiện tại.')
        with (work/'build.log').open('wb') as log:
            built=subprocess.run([npm,'run','build','--','--outDir',str(site),'--emptyOutDir'],cwd=project/'frontend',stdout=log,stderr=subprocess.STDOUT)
        if built.returncode or not (site/'index.html').is_file():raise ValueError('Build React chưa thành công. Xem '+str(work/'build.log'))
        print('2/3 · Tạo đường kết nối HTTPS thử nghiệm…',flush=True)
        with (work/'tunnel.log').open('wb') as tunnel_log,(work/'backend.log').open('wb') as backend_log:
            tunnel=subprocess.Popen([executable,'tunnel','--no-autoupdate','--url',f'http://127.0.0.1:{PORT}'],
                cwd=project,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            found=[];thread=threading.Thread(target=read_tunnel,args=(tunnel.stdout,tunnel_log,found),daemon=True);thread.start()
            deadline=time.monotonic()+90
            while not found:
                if tunnel.poll() is not None:raise ValueError('Cloudflare Tunnel chưa khởi động được. Xem '+str(work/'tunnel.log'))
                if time.monotonic()>deadline:raise ValueError('Chưa lấy được link HTTPS sau 90 giây. Xem '+str(work/'tunnel.log'))
                time.sleep(.2)
            origin=found[-1]
            token=secrets.token_urlsafe(32)
            env=dict(os.environ)
            env.update(WAREHOUSE_PUBLIC_PROJECT=str(project),WAREHOUSE_PUBLIC_ORIGIN=origin,WAREHOUSE_AUTH_ORIGINS=origin,
                WAREHOUSE_AUTH_SECURE_COOKIE='true',WAREHOUSE_PUBLIC_SITE=str(site),WAREHOUSE_PUBLIC_HEALTH_TOKEN=token,
                PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')
            print('3/3 · Khởi động một backend với cookie HTTPS…',flush=True)
            backend=subprocess.Popen([sys.executable,'-m','uvicorn','public_app:app','--app-dir',str(HERE),
                '--host','127.0.0.1','--port',str(PORT),'--no-proxy-headers','--no-access-log'],
                cwd=project,env=env,stdout=backend_log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+180
            while not readiness(token):
                if backend.poll() is not None:raise ValueError('Backend public chưa khởi động được. Xem '+str(work/'backend.log'))
                if tunnel.poll() is not None:raise ValueError('Tunnel đã ngắt khi đang khởi động backend.')
                if time.monotonic()>deadline:raise ValueError('Backend public chưa sẵn sàng sau 180 giây. Xem '+str(work/'backend.log'))
                time.sleep(.25)
            report={'started_at':datetime.now(timezone.utc).isoformat(),'origin':origin,'mode':'temporary_https_trial',
                'secure_cookie':True,'camera_gateway_implemented':True,'source_modified':False,'stopped_at':None}
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('\nLINK HTTPS: '+origin,flush=True)
            print('Mở link bằng thiết bị khác dùng 4G/5G để kiểm tra đăng nhập và dữ liệu kho.',flush=True)
            print('Vào tab Camera để ghép cầu nối tại kho. Không nhập IP LAN của kho vào máy chủ public.',flush=True)
            print('Giữ cửa sổ này và PostgreSQL chạy. Nhấn Ctrl+C để dừng truy cập Internet.',flush=True)
            while True:
                if tunnel.poll() is not None:raise ValueError('Đường kết nối Internet đã ngắt. Xem '+str(work/'tunnel.log'))
                if backend.poll() is not None:raise ValueError('Backend public đã dừng. Xem '+str(work/'backend.log'))
                time.sleep(.5)
    finally:
        # Stop exposure first, then the one backend. Do not kill other processes.
        stop(tunnel);stop(backend)
        if thread:thread.join(timeout=2)
        if report:
            report['stopped_at']=datetime.now(timezone.utc).isoformat()
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if site.exists():shutil.rmtree(site)
        print('Đã dừng các tiến trình của lượt thử; cấu hình local và dữ liệu kho được giữ lại.',flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,default=HERE.parent)
    args=parser.parse_args()
    try:run(args.project.resolve())
    except KeyboardInterrupt:pass
    except ValueError as error:raise SystemExit('Chưa mở Internet: '+str(error))


if __name__=='__main__':main()
