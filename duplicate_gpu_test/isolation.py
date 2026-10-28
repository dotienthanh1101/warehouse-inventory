"""Isolated, real-HTTP account/metadata load test; no production inventory writes."""
import argparse
import concurrent.futures
import ctypes
import getpass
import hashlib
import http.client
import json
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

HERE = Path(__file__).resolve().parent
DB_PREFIX = 'warehouse_load_test_'
ENDPOINTS = ['/api/auth/me', '/api/warehouse', '/api/inventory/days',
             '/api/catalog/products', '/api/camera/status', '/api/storage']


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * p) - 1)], 2)


def summarize(samples):
    ms = [s['ms'] for s in samples]
    failed = [s for s in samples if not s['ok']]
    return {'requests': len(samples), 'errors': len(failed),
            'error_percent': round(100 * len(failed) / len(samples), 2) if samples else None,
            'p50_ms': percentile(ms, .5), 'p95_ms': percentile(ms, .95),
            'max_ms': round(max(ms), 2) if ms else None,
            'identity_mismatches': sum(s.get('identity_mismatch', False) for s in samples),
            'by_endpoint': {path: {'requests': sum(s['path'] == path for s in samples),
                                 'errors': sum(s['path'] == path and not s['ok'] for s in samples),
                                 'p95_ms': percentile([s['ms'] for s in samples if s['path'] == path], .95)}
                            for path in sorted({s['path'] for s in samples})},
            'error_examples': [{k: s[k] for k in ('path', 'status', 'reason')}
                               for s in failed[:10]]}


def request(port, path, *, body=None, cookie='', warehouse_id='', timeout=10):
    start = time.perf_counter()
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    headers = {'Origin': f'http://127.0.0.1:{port}', 'Accept': 'application/json'}
    if cookie:
        headers['Cookie'] = cookie
    if warehouse_id:
        headers['X-Warehouse-ID'] = warehouse_id
    if body is not None:
        headers['Content-Type'] = 'application/json'
    try:
        conn.request('POST' if body is not None else 'GET', path,
                     None if body is None else json.dumps(body).encode(), headers)
        response = conn.getresponse()
        raw = response.read()
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            data = None
        return response.status, data, response.getheaders(), (time.perf_counter()-start)*1000
    finally:
        conn.close()


def login_user(port, account, password):
    name = account['username']
    start = time.perf_counter()
    sample = {'path': '/api/auth/login', 'ok': False, 'status': 0, 'reason': '', 'ms': 0}
    try:
        status, data, headers, elapsed = request(port, '/api/auth/login', body={'username': name, 'password': password})
        sample.update(status=status, ms=elapsed)
        jar = SimpleCookie()
        for key, value in headers:
            if key.lower() == 'set-cookie':
                jar.load(value)
        token = jar.get('warehouse_session')
        matches = bool(isinstance(data, dict) and data.get('user', {}).get('username') == name
                       and data.get('warehouse', {}).get('warehouse_id') == account['warehouse_id'])
        sample['ok'] = status == 200 and token is not None and matches
        sample['identity_mismatch'] = status == 200 and not matches
        sample['reason'] = '' if sample['ok'] else 'Đăng nhập lỗi hoặc định danh/kho không khớp.'
        identity = {**account, 'cookie': f'warehouse_session={token.value}'} if sample['ok'] else None
        return sample, identity
    except Exception as error:
        sample.update(ms=(time.perf_counter()-start)*1000, reason=type(error).__name__)
        return sample, None


def read_user(port, identity, start_event, deadline, interval):
    start_event.wait()
    samples = []
    counter = 0
    while time.monotonic() < deadline[0]:
        began = time.monotonic()
        path = ENDPOINTS[(counter + identity['index']) % len(ENDPOINTS)]
        sample = {'path': path, 'status': 0, 'ok': False, 'reason': '', 'ms': 0,
                  'identity_mismatch': False}
        try:
            code, data, _, elapsed = request(port, path, cookie=identity['cookie'], warehouse_id=identity['warehouse_id'])
            sample.update(status=code, ms=elapsed, ok=code == 200 and isinstance(data, dict))
            if sample['ok']:
                if path == '/api/auth/me':
                    sample['identity_mismatch'] = (data.get('user', {}).get('username') != identity['username']
                        or data.get('warehouse', {}).get('warehouse_id') != identity['warehouse_id'])
                elif path in ('/api/warehouse', '/api/storage'):
                    sample['identity_mismatch'] = data.get('warehouse_id') != identity['warehouse_id']
                elif path == '/api/inventory/days':
                    sample['identity_mismatch'] = data.get('days') != []
                elif path == '/api/camera/status':
                    sample['identity_mismatch'] = bool(data.get('ready') or data.get('address'))
                sample['ok'] = not sample['identity_mismatch']
            if not sample['ok']:
                sample['reason'] = 'Định danh/kho không khớp.' if sample['identity_mismatch'] else 'HTTP hoặc JSON lỗi.'
        except Exception as error:
            sample.update(ms=(time.monotonic()-began)*1000, reason=type(error).__name__)
        samples.append(sample)
        counter += 1
        remaining = min(interval - (time.monotonic()-began), deadline[0]-time.monotonic())
        if remaining > 0:
            time.sleep(remaining)
    return samples


def run_load(port, accounts, password, duration, interval):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(accounts)) as pool:
        logins = list(pool.map(lambda a: login_user(port, a, password), accounts))
    identities = [identity for _, identity in logins if identity]
    if not identities:
        return {'login': summarize([s for s, _ in logins]), 'reads': summarize([]), 'active_users': 0,
                'duration_seconds': 0, 'target_users': len(accounts)}
    event = threading.Event()
    deadline = [0]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(identities)) as pool:
        futures = [pool.submit(read_user, port, i, event, deadline, interval) for i in identities]
        began = time.monotonic()
        deadline[0] = began + duration
        event.set()
        samples = [sample for future in futures for sample in future.result()]
    elapsed = time.monotonic()-began
    return {'target_users': len(accounts), 'active_users': len(identities),
            'duration_seconds': round(elapsed, 2), 'read_interval_seconds_per_user': interval,
            'login': summarize([s for s, _ in logins]), 'reads': summarize(samples),
            'read_requests_per_second': round(len(samples)/elapsed, 2)}


def process_sample(pid):
    """Backend CPU time and working set using native APIs; no extra package."""
    if os.name != 'nt':
        proc = Path('/proc/self') if pid == os.getpid() else Path(f'/proc/{pid}')
        self_status=Path('/proc/self/status').read_text().splitlines()
        host_pid=next(line.split()[1] for line in self_status if line.startswith('Pid:'))
        if pid != os.getpid() and int(host_pid) != os.getpid():
            # A container may expose host procfs while Popen uses namespace IDs.
            proc=None
            for candidate in Path('/proc').iterdir():
                if not candidate.name.isdigit():continue
                try:
                    status=candidate.joinpath('status').read_text().splitlines()
                    parent=next(line.split()[1] for line in status if line.startswith('PPid:'))
                    if parent!=host_pid:continue
                    ns=next(line.split()[1:] for line in status if line.startswith('NSpid:'))
                    if int(ns[-1])==pid:proc=candidate;break
                except (OSError,StopIteration,ValueError):continue
            if proc is None:raise OSError('Chưa xác định được tiến trình con để đo.')
        fields = proc.joinpath('stat').read_text().split()
        rss = next(line.split()[1] for line in proc.joinpath('status').read_text().splitlines() if line.startswith('VmRSS:'))
        return {'cpu_seconds': (int(fields[13])+int(fields[14]))/os.sysconf('SC_CLK_TCK'), 'rss_bytes': int(rss)*1024}
    from ctypes import wintypes
    class Memory(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in
            ('peak_ws', 'working_set', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise OSError('Không mở được tiến trình để đo tài nguyên.')
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        memory = Memory();memory.cb = ctypes.sizeof(memory)
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]) or not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
            raise OSError('Không đo được tài nguyên backend.')
        ticks = sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:])
        return {'cpu_seconds': ticks/10000000, 'rss_bytes': memory.working_set}
    finally:
        kernel.CloseHandle(handle)


class Monitor:
    def __init__(self, pid):
        self.pid = pid;self.stop = threading.Event();self.samples = [];self.errors = 0
        self.thread = threading.Thread(target=self.loop, daemon=True)
    def loop(self):
        while not self.stop.is_set():
            try:self.samples.append({**process_sample(self.pid), 'time': time.monotonic()})
            except (OSError, ValueError, StopIteration):self.errors += 1
            self.stop.wait(.5)
    def finish(self):
        self.stop.set();self.thread.join(timeout=3)
        percentages = [100*(b['cpu_seconds']-a['cpu_seconds'])/(b['time']-a['time'])
                       for a,b in zip(self.samples,self.samples[1:]) if b['time'] > a['time']]
        return {'samples': len(self.samples), 'measurement_errors': self.errors,
                'peak_backend_rss_bytes': max((s['rss_bytes'] for s in self.samples), default=None),
                'backend_cpu_p95_one_core_percent': percentile(percentages,.95),
                'cpu_note': '100% tương đương một lõi CPU; chỉ đo tiến trình backend, không bao gồm PostgreSQL/trình duyệt.'}


def backend_process_id(log_path):
    """Uvicorn's server PID can differ from the Windows venv launcher PID."""
    text=Path(log_path).read_text(encoding='utf-8',errors='replace')
    text=re.sub(r'\x1b\[[0-9;]*m','',text)
    values=set(re.findall(r'Started server process\s+\[(\d+)\]',text))
    if len(values)!=1:
        raise ValueError('Chưa xác định được PID backend thực từ log thử; chưa đo CPU/RAM')
    pid=int(next(iter(values)))
    if pid<=0:raise ValueError('PID backend thử không hợp lệ')
    return pid


def free_port():
    for port in range(8001, 8091):
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port));return port
            except OSError:pass
    raise ValueError('Không tìm được cổng thử trống trong khoảng 8001–8090.')


def ensure_stopped():
    with socket.socket() as sock:
        sock.settimeout(.3)
        if sock.connect_ex(('127.0.0.1',8000)) == 0:
            raise ValueError('Chờ tác vụ hoàn tất, dừng backend cổng 8000 bằng Ctrl+C trước khi đo tải.')


def verify_source(project):
    reference = json.loads((HERE/'baseline.json').read_text(encoding='utf-8'))
    for name, expected in reference.items():
        path = project/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Mã nguồn không khớp bản đã kiểm thử: '+name+'. Chưa tạo dữ liệu thử.')


def create_database(settings, password, name, marker):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    with psycopg.connect(settings.database_url, connect_timeout=10) as source:
        owner, source_name = source.execute('SELECT current_user,current_database()').fetchone()
    if name == source_name or not name.startswith(DB_PREFIX):
        raise ValueError('Tên cơ sở dữ liệu thử không hợp lệ.')
    admin_url = make_conninfo(settings.database_url, user='postgres', password=password, dbname='postgres')
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(sql.Identifier(name), sql.Identifier(owner)))
        try:
            admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(name), sql.Literal(marker)))
        except Exception:
            try:admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            except Exception:raise ValueError('Chưa gắn nhãn/dọn được DB thử '+name+'. Cần kiểm tra trong pgAdmin; không xóa DB khác.')
            raise
    return test_database_url(settings.database_url, name), admin_url


def test_database_url(source_url, name):
    if not name.startswith(DB_PREFIX) or not name[len(DB_PREFIX):].isalnum():
        raise ValueError('Tên cơ sở dữ liệu thử không hợp lệ.')
    parsed=urlsplit(source_url)
    # Keep URI form because AuthSettings requires it; strip a query dbname that
    # could otherwise override the dedicated test database in the URI path.
    query=urlencode([(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if k!='dbname'])
    return urlunsplit((parsed.scheme,parsed.netloc,'/'+name,query,''))


def drop_database(admin_url, name, marker):
    import psycopg
    from psycopg import sql
    if not name.startswith(DB_PREFIX):
        raise ValueError('Từ chối dọn cơ sở dữ liệu không phải của bài thử.')
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as c:
        row = c.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",(name,)).fetchone()
        if row is None:return
        if row[0] != marker:raise ValueError('Nhãn cơ sở dữ liệu thử không khớp; không xóa.')
        c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(name,))
        c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def child_environment(project, temporary, database_url, port):
    env = dict(os.environ)
    config = temporary/'auth.test.json'
    config.write_text(json.dumps({'pool_size':20,'max_image_tasks':8}), encoding='utf-8')
    env.update(WAREHOUSE_AUTH_DATABASE_URL=database_url, WAREHOUSE_AUTH_SECRET=uuid4().hex*2,
               WAREHOUSE_AUTH_CONFIG_FILE=str(config), WAREHOUSE_STORAGE_ROOT=str(temporary/'warehouses'),
               WAREHOUSE_AUTH_ORIGINS=f'http://127.0.0.1:{port}', WAREHOUSE_AUTH_SECURE_COOKIE='false',
               PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
               PYTHONPATH=os.pathsep.join([str(project),env.get('PYTHONPATH','')]))
    return env


def temporary_test_area(storage):
    """Use the measured warehouse volume, not the Windows system TEMP drive."""
    storage=Path(storage).resolve()
    if not storage.is_dir():
        raise ValueError('Không tìm thấy thư mục lưu dữ liệu kho để chọn ổ thử.')
    parent=storage.parent
    if parent.stat().st_dev != storage.stat().st_dev:
        parent=storage
    return tempfile.TemporaryDirectory(prefix='warehouse_load_test_',dir=str(parent))


def seed_error(error, stage, completed):
    """Whitelist diagnostics: never echo DSNs, SQL, passwords or raw traceback."""
    code=getattr(error,'status_code',None)
    state=getattr(error,'sqlstate',None)
    kind=type(error).__name__
    if code==507:
        message='Không đủ ngân sách ổ thử để tạo thêm kho (512 MiB/kho, giữ 10 GiB dự phòng).'
    elif code==503:
        message='Chưa đọc được đầy đủ dung lượng hoặc cấu hình kho thử.'
    elif isinstance(error,ModuleNotFoundError):
        name=getattr(error,'name','')
        message='Thiếu thư viện: '+name if isinstance(name,str) and all(c.isalnum() or c in '._' for c in name) else 'Thiếu thư viện Python.'
    elif state=='42501':
        message='Tài khoản sở hữu DB thử chưa có đủ quyền tạo bảng/schema.'
    elif state in ('28P01','28000'):
        message='Không xác thực được kết nối PostgreSQL của ứng dụng vào DB thử.'
    elif state=='3F000':
        message='Schema PostgreSQL cấu hình cho ứng dụng không tồn tại trong DB thử.'
    elif getattr(error,'winerror',None)==32:
        message='Một file trong vùng thử đang bị tiến trình khác giữ.'
    elif isinstance(error,PermissionError):
        message='Không có quyền ghi thư mục hoặc file của vùng thử.'
    elif isinstance(error,OSError) and getattr(error,'errno',None)==28:
        message='Ổ thử không còn đủ chỗ ghi file.'
    else:
        message='Khởi tạo DB/kho thử gặp lỗi; xem vị trí và mã lỗi bên dưới.'
    frame=error.__traceback__
    while frame is not None and frame.tb_next is not None:frame=frame.tb_next
    location=None
    if frame is not None:
        location={'file':Path(frame.tb_frame.f_code.co_filename).name,
                  'function':frame.tb_frame.f_code.co_name,'line':frame.tb_lineno}
    return {'seed_failure':True,'stage':stage,'accounts_created':completed,
            'error_type':kind,'http_status':code if type(code) is int else None,
            'sqlstate':state if isinstance(state,str) and len(state)==5 and state.isalnum() else None,
            'message':message,'location':location}


def seed(project, users, password):
    sys.path.insert(0,str(project))
    store=None;completed=0;stage='import'
    try:
        from backend.app.auth_settings import load_settings
        from backend.app.auth_store import AuthStore
        stage='connect'
        store = AuthStore(load_settings());store.open()
        stage='initialize_tables'
        store.initialize();accounts=[]
        for index in range(users):
            stage='create_warehouse'
            username=f'loadtest-{index:03}'
            result=store.register(username,username,password,f'Kho thử {index:03}')
            accounts.append({'index':index,'username':username,'warehouse_id':result['warehouse']['warehouse_id']})
            completed+=1
        print(json.dumps(accounts))
        return True
    except Exception as error:
        print(json.dumps(seed_error(error,stage,completed),ensure_ascii=False),flush=True)
        return False
    finally:
        if store is not None:
            try:store.close()
            except Exception:pass


def execute(project, users, duration, interval, postgres_password):
    import secrets
    sys.path.insert(0,str(project))
    from backend.app.auth_settings import load_settings
    from backend.app.warehouse_scope import STORAGE_ROOT
    settings=load_settings()
    name=DB_PREFIX+uuid4().hex[:16];marker='warehouse-benchmark:'+uuid4().hex
    database_url=admin_url=None;process=None;monitor=None;result=None;cleanup_error=None
    password=secrets.token_urlsafe(24)
    work=temporary_test_area(STORAGE_ROOT)
    temp=Path(work.name);port=free_port()
    try:
        print('1/4 · Tạo cơ sở dữ liệu và kho thử riêng…',flush=True)
        print('Ổ đặt kho thử:',temp.anchor,flush=True)
        database_url,admin_url=create_database(settings,postgres_password,name,marker)
        env=child_environment(project,temp,database_url,port)
        preparation=subprocess.run([sys.executable,str(HERE/'run.py'),'--seed',str(project)],
            input=json.dumps({'users':users,'password':password}),text=True,encoding='utf-8',
            capture_output=True,env=env,cwd=str(project),timeout=600)
        if preparation.returncode:
            try:
                diagnostic=json.loads(preparation.stdout.strip().splitlines()[-1])
            except (ValueError,IndexError):diagnostic=None
            if isinstance(diagnostic,dict) and diagnostic.get('seed_failure'):
                print('CHI TIẾT KHỞI TẠO:',json.dumps(diagnostic,ensure_ascii=False),flush=True)
            else:
                print('Tiến trình tạo tài khoản thử kết thúc với mã:',preparation.returncode,flush=True)
            raise ValueError('Chưa tạo đủ tài khoản thử. Gửi lại phần CHI TIẾT KHỞI TẠO ở trên; không gửi mật khẩu hoặc file cấu hình')
        accounts=json.loads(preparation.stdout.strip().splitlines()[-1])
        print('2/4 · Khởi động backend thử trên loopback…',flush=True)
        with (temp/'backend.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.daily_api:app','--host','127.0.0.1',
                '--port',str(port),'--no-access-log'],cwd=str(project),env=env,stdout=log,stderr=subprocess.STDOUT)
            until=time.monotonic()+180
            while time.monotonic()<until:
                if process.poll() is not None:raise ValueError('Backend thử dừng trước khi sẵn sàng. Kiểm tra thư viện đang cài.')
                try:
                    if request(port,'/api/health',timeout=1)[0]==200:break
                except (OSError,http.client.HTTPException):pass
                time.sleep(.2)
            else:raise ValueError('Backend thử chưa sẵn sàng sau 180 giây.')
            server_pid=backend_process_id(temp/'backend.log')
            monitor=Monitor(server_pid);monitor.thread.start()
            print(f'3/4 · {users} tài khoản đăng nhập; đọc API trong {duration} giây…',flush=True)
            result=run_load(port,accounts,password,duration,interval)
            result['resources']=monitor.finish();monitor=None
            result['resources'].update(backend_pid=server_pid,launcher_pid=process.pid,
                pid_source='uvicorn_server_startup_log')
            result.update(created_at=datetime.now(timezone.utc).isoformat(),database=name,
                isolated=True,production_inventory_written=False,workload='login_burst_then_paced_metadata_reads',
                http_connection_policy='one_connection_per_request',
                hardware={'logical_cpu_count':os.cpu_count(),'python':sys.version.split()[0],'platform':platform.platform()},
                gpu_inference_tested=False,postgresql_cpu_ram_measured=False,
                scope_note='HTTP loopback; không phải 100 trình duyệt, 100 camera, tải GPU hoặc nghiệm thu Internet/HTTPS.')
    finally:
        if monitor:monitor.finish()
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        print('4/4 · Dọn cơ sở dữ liệu và kho do bài thử tạo…',flush=True)
        if admin_url:
            try:drop_database(admin_url,name,marker)
            except Exception:cleanup_error='Chưa dọn được cơ sở dữ liệu thử '+name+'. Chỉ xóa DB mang tên này sau khi kiểm tra trong pgAdmin.'
        try:work.cleanup()
        except OSError:cleanup_error=(cleanup_error or '')+' Chưa dọn hết thư mục thử: '+str(temp)
        if cleanup_error:print(cleanup_error,flush=True)
    if result is not None:
        result['cleanup_complete']=cleanup_error is None
        result['cleanup_warning']=cleanup_error
        result['all_target_users_tested']=result['active_users']==users
    return result


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--seed':
        data=json.load(sys.stdin)
        if not seed(Path(sys.argv[2]),int(data['users']),data['password']):raise SystemExit(2)
        return
    parser=argparse.ArgumentParser(description='Kiểm tải HTTP bằng kho/DB thử riêng, không dùng dữ liệu kiểm kê thật.')
    parser.add_argument('--project',type=Path,default=HERE.parent)
    parser.add_argument('--users',type=int,default=100)
    parser.add_argument('--duration',type=int,default=30)
    parser.add_argument('--interval',type=float,default=2)
    args=parser.parse_args()
    if not 1<=args.users<=100 or not 5<=args.duration<=300 or not .25<=args.interval<=10:
        raise ValueError('Cần 1–100 user, 5–300 giây, khoảng nghỉ 0.25–10 giây.')
    project=args.project.resolve();ensure_stopped();verify_source(project)
    print('Chỉ tạo dữ liệu thử riêng. Không chụp camera, không chạy GPU, không sửa kho đang dùng.')
    password=getpass.getpass('Mật khẩu PostgreSQL của postgres để tạo và dọn DB thử: ')
    result=execute(project,args.users,args.duration,args.interval,password)
    if result is None:raise ValueError('Chưa có kết quả đo tải.')
    output=project/'docs'/('load_100_'+datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]+'.json')
    output.parent.mkdir(exist_ok=True)
    with output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print('\n===== KẾT QUẢ KIỂM TẢI =====')
    print(f"Tài khoản đọc API: {result['active_users']}/{result['target_users']}")
    for key,label in [('login','Đăng nhập'),('reads','Đọc dữ liệu')]:
        value=result[key]
        print(f"{label}: {value['requests']} yêu cầu; lỗi {value['errors']}; p50={value['p50_ms']} ms; p95={value['p95_ms']} ms")
    print('Định danh/kho không khớp:',result['login']['identity_mismatches']+result['reads']['identity_mismatches'])
    resource=result.get('resources',{})
    print('PID backend đo tài nguyên:',resource.get('backend_pid'),'; PID khởi động:',resource.get('launcher_pid'))
    print('RAM backend đỉnh (MiB):',round(resource['peak_backend_rss_bytes']/1048576,2) if resource.get('peak_backend_rss_bytes') is not None else 'Chưa đo được')
    print('CPU backend p95 (% của một lõi):',resource.get('backend_cpu_p95_one_core_percent'))
    print('Dọn dữ liệu thử:', 'Đã xong' if result['cleanup_complete'] else 'Cần kiểm tra cảnh báo')
    print('Báo cáo:',output)
    print('Chưa nghiệm thu tải camera/GPU hoặc Internet. Có thể khởi động lại backend chính.')


if __name__=='__main__':
    try:main()
    except (Exception,KeyboardInterrupt) as error:
        # Do not print exception text that might contain a DSN or credentials.
        message=str(error) if isinstance(error,ValueError) else type(error).__name__
        raise SystemExit('Chưa hoàn tất kiểm tải: '+message+'. Không phục hồi đè hoặc sửa kho thật.')
