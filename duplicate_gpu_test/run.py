"""NFR-06 duplicate-comparison queue/GPU probe, isolated from production accounts and data."""
import argparse
import concurrent.futures
import getpass
import hashlib
from http.client import HTTPConnection,HTTPException
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime,timezone
from http.cookies import SimpleCookie
from pathlib import Path
from uuid import uuid4

import isolation

HERE=Path(__file__).resolve().parent


def http(port,path,identity=None,body=None,binary=False,timeout=30):
    connection=HTTPConnection('127.0.0.1',port,timeout=timeout)
    headers={'Origin':f'http://127.0.0.1:{port}'}
    if identity:
        headers.update(Cookie=identity['cookie'])
        headers['X-Warehouse-ID']=identity['warehouse_id']
        if body is not None:headers['X-CSRF-Token']=identity['csrf_token']
    if body is not None:headers['Content-Type']='application/json'
    began=time.perf_counter()
    try:
        connection.request('POST' if body is not None else 'GET',path,
                           json.dumps(body).encode() if body is not None else None,headers)
        response=connection.getresponse();raw=response.read()
        return response.status,raw if binary else json.loads(raw),response.getheaders(),(time.perf_counter()-began)*1000
    finally:connection.close()


def login(port,account,password):
    status,data,headers,_=http(port,'/api/auth/login',body={'username':account['username'],'password':password})
    if status!=200 or data.get('warehouse',{}).get('warehouse_id')!=account['warehouse_id']:
        raise ValueError('Tài khoản thử đăng nhập lỗi hoặc không khớp kho.')
    jar=SimpleCookie()
    for key,value in headers:
        if key.lower()=='set-cookie':jar.load(value)
    if 'warehouse_session' not in jar:raise ValueError('Đăng nhập thử chưa nhận được cookie.')
    return {**account,'cookie':'warehouse_session='+jar['warehouse_session'].value,'csrf_token':data['csrf_token']}


def prepare(project,control,image_path,password):
    """Seed reviewed fixture regions, two history images + current per tenant."""
    sys.path.insert(0,str(project))
    from backend.app.auth_settings import load_settings
    from backend.app.auth_store import AuthStore
    from backend.app.warehouse_scope import warehouse_scope
    from backend.app.daily_inventory_service import create_day
    from backend.app.daily_session_service import create_daily_session
    from backend.app.review_api import save_review, CAPTURE_DIR
    import cv2
    import numpy as np
    frame=cv2.imdecode(np.fromfile(str(image_path),dtype=np.uint8),cv2.IMREAD_COLOR)
    if frame is None:raise ValueError('Không đọc được ảnh thử JPG/PNG.')
    original_size=[frame.shape[1],frame.shape[0]]
    scale=min(1,1920/max(frame.shape[:2]))
    if scale<1:frame=cv2.resize(frame,(round(frame.shape[1]*scale),round(frame.shape[0]*scale)),interpolation=cv2.INTER_AREA)
    if min(frame.shape[:2])<160:raise ValueError('Chọn ảnh thử có mỗi cạnh ít nhất 160 pixel.')
    h,w=frame.shape[:2]
    store=AuthStore(load_settings());store.open();accounts=[];stage='initialize'
    try:
        store.initialize()
        for index in range(10):
            stage='create_warehouse'
            username=f'dupqueue-{index:02}'
            profile=store.register(username,username,password,f'Kho thử so sánh {index:02}')
            warehouse=profile['warehouse']['warehouse_id']
            history=[];image_hashes={}
            with warehouse_scope(warehouse):
                day=create_day({i:0 for i in range(1,31)})
                session=create_daily_session(day['day_id'],f'Duplicate queue test {index:02}')
                CAPTURE_DIR.mkdir(parents=True,exist_ok=True)
                for number in range(3):
                    review_id=uuid4().hex
                    marked=frame.copy()
                    cv2.rectangle(marked,(0,0),(95,47),(20+index*21,210-index*13,35+index*17),-1)
                    cv2.putText(marked,f'TEST {index:02}',(3,30),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,255,255),1,cv2.LINE_AA)
                    # Synthetic corner exclusion exercises the masked branch;
                    # it does not measure real marker detection accuracy.
                    cv2.rectangle(marked,(w-44,h-44),(w-8,h-8),(255,255,255),-1)
                    cv2.rectangle(marked,(w-38,h-38),(w-14,h-14),(0,0,0),-1)
                    ok,jpg=cv2.imencode('.jpg',marked,[cv2.IMWRITE_JPEG_QUALITY,95])
                    if not ok:raise ValueError('Không mã hóa được ảnh thử.')
                    filename=f'review_{review_id}_frame1.jpg'
                    jpg.tofile(str(CAPTURE_DIR/filename));url='/api/captures/'+filename
                    image_hashes[url]=hashlib.sha256(jpg.tobytes()).hexdigest()
                    box={'box_id':'box-1','bounds':[0,0,w,h],'marker_id':23,'reviewed':True,'note':'Vùng cố định chỉ dùng kiểm tải.'}
                    doc={'status':'ok','review_id':review_id,'revision':1,'review_status':'corrected_draft',
                        'created_at':f'2020-01-01T00:00:0{number}+00:00','session_id':session['session_id'],
                        'image_url':url,'width':w,'height':h,'analysis':{'markers':[],'boxes':[box]},
                        'human_review':{'revision':1,'boxes':[box],'assignments':{},
                            'excluded_regions':[{'bounds':[w-48,h-48,w-4,h-4]}]},'inventory_recorded':False}
                    save_review(doc)
                    if number<2:history.append(review_id)
            accounts.append({'username':username,'warehouse_id':warehouse,'session_id':session['session_id'],
                'review_id':review_id,'reference_review_ids':history,'image_hashes':image_hashes})
        return {'accounts':accounts,'original_image_size':original_size,'test_image_size':[w,h]}
    except Exception as error:
        print('PREPARE_ERROR:'+json.dumps(isolation.seed_error(error,stage,len(accounts)),ensure_ascii=False),flush=True)
        raise
    finally:store.close()


def submit(port,identity,nonce):
    payload={'request_id':nonce,'kind':'duplicate-check','session_id':identity['session_id'],
             'review_id':identity['review_id'],'revision':1}
    try:
        status,data,_,elapsed=http(port,'/api/tasks',identity,payload)
        return {'identity':identity,'request_id':nonce,'status':status,'data':data,'submit_ms':elapsed}
    except Exception as error:
        return {'identity':identity,'request_id':nonce,'status':0,'data':{},'submit_ms':None,'error_type':type(error).__name__}


def queue_counts(connection):
    rows=connection.execute('SELECT state,count(*) FROM auth_image_tasks GROUP BY state').fetchall()
    values=dict(rows)
    return {'active':sum(values.get(s,0) for s in ('preparing','queued','running')),'running':values.get('running',0)}


def timestamps(row):
    def dt(value):return datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    created,started,finished=(dt(row[k]) if row.get(k) else None for k in ('created_at','started_at','finished_at'))
    return {'queue_wait_ms':round((started-created).total_seconds()*1000,2) if created and started else None,
            'task_elapsed_ms':round((finished-created).total_seconds()*1000,2) if created and finished else None}


def await_tasks(port,accepted,connection,timeout=1800):
    pending={row['data']['task_id']:row for row in accepted};finished=[];peak_active=peak_running=0
    until=time.monotonic()+timeout
    while pending and time.monotonic()<until:
        counts=queue_counts(connection);peak_active=max(peak_active,counts['active']);peak_running=max(peak_running,counts['running'])
        for task_id,row in list(pending.items()):
            status,value,_,_=http(port,'/api/tasks/'+task_id,row['identity'])
            if status!=200:raise ValueError('Không đọc được tác vụ thử bằng đúng tài khoản sở hữu.')
            if value['state'] in ('completed','failed'):
                finished.append({**row,'task':value,**timestamps(value)})
                del pending[task_id]
        if pending:time.sleep(.2)
    return finished,list(pending),{'peak_active_sampled':peak_active,'peak_running_sampled':peak_running}


def validate_report(document,identity):
    failures=[]
    if (document.get('session_id')!=identity['session_id'] or document.get('review_id')!=identity['review_id']
            or document.get('review_revision')!=1):return ['result_identity_mismatch']
    if document.get('inventory_modified') is not False:failures.append('unexpected_inventory_flag')
    if document.get('reference_image_count')!=2 or document.get('reference_count')!=2:
        failures.append('history_count_mismatch')
    if document.get('thresholds')!={'low':.4,'high':.75}:failures.append('thresholds_changed')
    results=document.get('results',[])
    if len(results)!=1:failures.append('region_count_mismatch')
    for region in results:
        comparisons=region.get('comparisons',[])
        if {c.get('reference_review_id') for c in comparisons}!=set(identity['reference_review_ids']) or len(comparisons)!=2:
            failures.append('wrong_or_missing_history')
        if not region.get('history_complete'):failures.append('incomplete_history')
        for value in comparisons:
            if value.get('evaluation_status')=='comparison_error' or value.get('comparison_performed') is False:
                failures.append('comparison_error')
            if set(value.get('appearance') or {})!={'original','masked'}:failures.append('missing_appearance_branches')
            if set(value.get('branches') or {})!={'original','masked'}:failures.append('missing_geometry_branches')
    return failures


def check_result(port,row,other):
    task=row['task'];identity=row['identity'];task_id=task['task_id']
    if task['state']!='completed':return ['task_failed']
    code,document,_,_=http(port,'/api/tasks/'+task_id+'/result',identity)
    if code!=200:return ['cannot_read_result']
    failures=validate_report(document,identity)
    urls=set(identity['image_hashes'])
    if document.get('image_url') not in urls:failures.append('unknown_current_image')
    for region in document.get('results',[]):
        for c in region.get('comparisons',[]):
            if c.get('reference_image_url') not in urls:failures.append('unknown_reference_image')
    for url,digest in identity['image_hashes'].items():
        code,raw,_,_=http(port,url,identity,binary=True)
        if code!=200 or hashlib.sha256(raw).hexdigest()!=digest:failures.append('image_identity_mismatch')
    for path in ['/api/tasks/'+task_id,'/api/tasks/'+task_id+'/result','/api/reviews/'+identity['review_id'],*urls]:
        code,_,_,_=http(port,path,other,binary=True)
        if code!=404:failures.append('cross_warehouse_access_allowed')
    return failures


class ReadProbe:
    def __init__(self,port,identity):
        self.port=port;self.identity=identity;self.samples=[];self.stop=threading.Event()
        self.thread=threading.Thread(target=self.loop,daemon=True)
    def loop(self):
        while not self.stop.is_set():
            try:
                code,value,_,elapsed=http(self.port,'/api/warehouse',self.identity,timeout=10)
                self.samples.append({'ok':code==200 and value.get('warehouse_id')==self.identity['warehouse_id'],'ms':elapsed})
            except Exception:self.samples.append({'ok':False,'ms':None})
            self.stop.wait(.25)
    def finish(self):
        self.stop.set();self.thread.join(timeout=12)
        return {'requests':len(self.samples),'errors':sum(not s['ok'] for s in self.samples),
                'p95_ms':isolation.percentile([s['ms'] for s in self.samples if s['ms'] is not None],.95)}


def preflight():
    import torch
    if not torch.cuda.is_available():raise ValueError('PyTorch chưa sử dụng được GPU.')
    from huggingface_hub import try_to_load_from_cache
    from transformers import AutoImageProcessor,AutoProcessor,AutoConfig
    for model in ('IDEA-Research/grounding-dino-tiny','facebook/dinov2-small'):
        weights=try_to_load_from_cache(model,'model.safetensors')
        if not isinstance(weights,str) or not Path(weights).is_file():raise ValueError('Chưa có trọng số trong cache: '+model)
        AutoConfig.from_pretrained(model,local_files_only=True)
        if 'dinov2' in model:AutoImageProcessor.from_pretrained(model,local_files_only=True,use_fast=False)
        else:AutoProcessor.from_pretrained(model,local_files_only=True)
    weights=Path(torch.hub.get_dir())/'checkpoints/loftr_indoor.ckpt'
    if not weights.is_file():raise ValueError('Chưa có LoFTR indoor trong cache PyTorch.')
    import kornia,truststore
    return {'gpu':torch.cuda.get_device_name(0),'total_bytes':torch.cuda.get_device_properties(0).total_memory,
        'models':['Grounding DINO resident','DINOv2-Small','LoFTR indoor']}


def inventory_count(control,identities):
    """Read only the explicitly isolated SQLite files, never application scope."""
    count=0
    storage=(control/'warehouses').resolve()
    for profile in identities:
        database=storage/profile['warehouse_id']/'data/warehouse.db'
        if not database.is_file() or not database.resolve().is_relative_to(storage):
            raise ValueError('Không tìm thấy SQLite thử trong đúng vùng cách ly.')
        connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
        try:
            count+=connection.execute('SELECT count(*) FROM inventory_review_receipts').fetchone()[0]
            count+=connection.execute('SELECT count(*) FROM inventory_box_instances').fetchone()[0]
        finally:connection.close()
    return count


def run(project,image_path,postgres_password):
    import psycopg
    sys.path.insert(0,str(project))
    from backend.app.auth_settings import load_settings
    from backend.app.warehouse_scope import STORAGE_ROOT
    settings=load_settings();name=isolation.DB_PREFIX+uuid4().hex[:16];marker='warehouse-benchmark:'+uuid4().hex
    area=isolation.temporary_test_area(STORAGE_ROOT);control=Path(area.name);port=isolation.free_port()
    password=secrets.token_urlsafe(24);admin_url=None;process=monitor=probe=None;result=None;cleanup_errors=[]
    stage='prepare'
    try:
        print('1/5 · Kiểm tra GPU/mô hình và tạo dữ liệu thử riêng…',flush=True)
        test_url,admin_url=isolation.create_database(settings,postgres_password,name,marker)
        env=isolation.child_environment(project,control,test_url,port)
        env.update(WAREHOUSE_DUPLICATE_GPU_TEST_ROOT=str(control),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
        # Child imports application code only after isolated DB/storage overrides.
        (control/'control.json').write_text(json.dumps({'format':'warehouse-duplicate-gpu-test-v1','project':str(project),'database':name}),encoding='utf-8')
        gpu_check=subprocess.run([sys.executable,str(HERE/'run.py'),'--preflight'],cwd=str(project),env=env,text=True,encoding='utf-8',capture_output=True,timeout=120)
        if gpu_check.returncode:
            for line in gpu_check.stderr.splitlines():
                if line.startswith('Chưa hoàn tất kiểm so sánh GPU:'):print(line,flush=True)
            raise ValueError('GPU hoặc cache mô hình chưa sẵn sàng; xem thông báo phía trên.')
        gpu=json.loads(gpu_check.stdout.strip().splitlines()[-1])
        seeded=subprocess.run([sys.executable,str(HERE/'run.py'),'--prepare',str(project)],cwd=str(project),env=env,
            input=json.dumps({'image':str(image_path),'password':password,'control':str(control)}),text=True,encoding='utf-8',capture_output=True,timeout=300)
        if seeded.returncode:
            for line in seeded.stdout.splitlines():
                if line.startswith('PREPARE_ERROR:'):print(line,flush=True)
            raise ValueError('Chưa chuẩn bị được đủ mười kho thử. Xem PREPARE_ERROR ở trên.')
        seeded=json.loads(seeded.stdout.strip().splitlines()[-1])
        stage='start_backend'
        print('2/5 · Khởi động backend thử và đăng nhập 10 tài khoản…',flush=True)
        with (control/'backend.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','worker:app','--app-dir',str(HERE),'--host','127.0.0.1',
                '--port',str(port),'--no-access-log'],cwd=str(project),env=env,stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                if process.poll() is not None:raise ValueError('Backend GPU thử chưa khởi động được; chưa gửi ảnh.')
                try:
                    if http(port,'/api/health',timeout=1)[0]==200:break
                except (OSError,HTTPException):pass
                time.sleep(.2)
            else:raise ValueError('Backend GPU thử chưa sẵn sàng sau 180 giây.')
            server_pid=isolation.backend_process_id(control/'backend.log')
            monitor=isolation.Monitor(server_pid);monitor.thread.start()
            identities=[login(port,a,password) for a in seeded['accounts']]
            stage='admission'
            print('3/5 · Gửi 10 tác vụ; kiểm giới hạn 8 và chống gửi lại…',flush=True)
            began=time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                burst=list(pool.map(lambda p:submit(port,p,uuid4().hex),identities))
            accepted=[r for r in burst if r['status']==202];rejected=[r for r in burst if r['status']==429]
            with psycopg.connect(test_url,autocommit=True) as connection:
                admission=queue_counts(connection)
                repeat_status=new_request_status=None;repeated_same=False
                if accepted:
                    target=accepted[0]
                    repeat=submit(port,target['identity'],target['request_id'])
                    repeat_status=repeat['status'];repeated_same=repeat['data'].get('task_id')==target['data'].get('task_id')
                    new_request_status=submit(port,target['identity'],uuid4().hex)['status']
                gate_seconds=round(time.monotonic()-began,3)
                (control/'release.flag').touch()
                stage='comparison'
                print('4/5 · Chạy GPU thật, đo thời gian và kiểm kết quả từng kho…',flush=True)
                probe=ReadProbe(port,identities[-1]);probe.thread.start()
                done,pending,peaks=await_tasks(port,accepted,connection)
                checks=[]
                for row in done:
                    other=next(p for p in identities if p['warehouse_id']!=row['identity']['warehouse_id'])
                    checks.extend(check_result(port,row,other))
                recovery=None
                if rejected and not pending:
                    retry=submit(port,rejected[0]['identity'],uuid4().hex)
                    if retry['status']==202:
                        recovered,still_pending,_=await_tasks(port,[retry],connection,timeout=600)
                        recovery=not still_pending and bool(recovered) and recovered[0]['task']['state']=='completed'
                        for row in recovered:checks.extend(check_result(port,row,identities[0] if identities[0]['warehouse_id']!=row['identity']['warehouse_id'] else identities[1]))
                        done.extend(recovered)
                    else:recovery=False
                rows=connection.execute('SELECT task_id,started_at,finished_at FROM auth_image_tasks WHERE started_at IS NOT NULL ORDER BY started_at').fetchall()
                no_overlap=all(a[2] is not None and a[2]<=b[1] for a,b in zip(rows,rows[1:]))
            inventory_rows=inventory_count(control,identities)
            traces=[json.loads(line) for line in (control/'gpu_trace.jsonl').read_text().splitlines()] if (control/'gpu_trace.jsonl').is_file() else []
            result={'created_at':datetime.now(timezone.utc).isoformat(),'workload':'duplicate_queue_real_models_two_fixture_history_images',
                'users':10,'queue_limit':8,'accepted':len(accepted),'rejected_full':len(rejected),
                'unexpected_submit_errors':len(burst)-len(accepted)-len(rejected),'active_at_gate':admission['active'],
                **peaks,'repeat_request_status':repeat_status,'repeat_returns_same_task':repeated_same,
                'second_active_request_status':new_request_status,'queue_recovered_after_full':recovery,
                'completed':sum(r['task']['state']=='completed' for r in done),'failed':sum(r['task']['state']=='failed' for r in done),
                'tasks_still_pending':len(pending),'result_or_isolation_errors':checks,'single_worker_intervals':no_overlap,
                'inventory_rows_written':inventory_rows,'gpu':gpu,'gpu_samples':len(traces),'gpu_analysis_errors':sum(bool(t['error']) for t in traces),
                'peak_torch_allocated_bytes':max((t['peak_allocated_bytes'] for t in traces),default=None),
                'peak_torch_reserved_bytes':max((t['peak_reserved_bytes'] for t in traces),default=None),
                'cold_analysis_ms':traces[0]['analysis_ms'] if traces else None,
                'model_calls':[c for t in traces for c in t.get('model_calls',[])],
                'grounding_warmup_ms':traces[0].get('grounding_warmup_ms') if traces else None,
                'comparisons_measured':sum(t.get('comparison_count',0) for t in traces),
                'cpu_fallback_calls':sum(c.get('device')=='cpu' for t in traces for c in t.get('model_calls',[])),
                'model_operation_errors':sum(bool(c.get('error')) for t in traces for c in t.get('model_calls',[])),
                'warm_analysis_p95_ms':isolation.percentile([t['analysis_ms'] for t in traces[1:]],.95),
                'queue_wait_p95_ms':isolation.percentile([r['queue_wait_ms'] for r in done if r['queue_wait_ms'] is not None],.95),
                'controlled_gate_seconds':gate_seconds,'image_size':seeded['test_image_size'],
                'notes':['No camera input; seeded reviewed fixture regions, two earlier images per account.',
                    'Grounding DINO is loaded by one analysis before comparison to model resident GPU pressure.',
                    'DINOv2 and LoFTR inference and the production task queue run unchanged.',
                    'Worker is paused only during admission checks; raw queue wait includes this controlled hold.',
                    'VRAM reports PyTorch allocations/reservations, not total graphics-card usage.',
                    'Does not test comparison accuracy, auto regions, real marker reading, camera transport, Internet or 100 GPU jobs.']}
            result['read_probe']=probe.finish();probe=None
            result['resources']=monitor.finish();monitor=None
            result['resources'].update(backend_pid=server_pid,launcher_pid=process.pid)
            result['trace_identity_errors']=sum(
                t.get('task_id') not in {r['task']['task_id'] for r in done}
                or t.get('warehouse_id')!=next((r['identity']['warehouse_id'] for r in done if r['task']['task_id']==t.get('task_id')),None)
                for t in traces)
            result['operational_checks_passed']=operational_pass(result)
            result['all_model_operations_on_cuda']=bool(result['model_calls']) and all(c.get('device')=='cuda' for c in result['model_calls'])

    except BaseException as error:
        failure={'created_at':datetime.now(timezone.utc).isoformat(),'stage':stage,
            'error_type':type(error).__name__,'completed':False}
        if control.exists():
            trace_path=control/'gpu_trace.jsonl'
            if trace_path.is_file():
                try:failure['gpu_trace']=[json.loads(line) for line in trace_path.read_text().splitlines()]
                except (OSError,ValueError):failure['trace_unreadable']=True
        failure_path=project/'docs'/('duplicate_gpu_queue_failed_'+uuid4().hex[:12]+'.json')
        failure_path.parent.mkdir(exist_ok=True)
        failure_path.write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Biên bản bước chưa hoàn tất:',failure_path,flush=True)
        raise
    finally:
        if probe:probe.finish()
        if monitor:monitor.finish()
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        print('5/5 · Dọn DB và ảnh thử riêng…',flush=True)
        if admin_url:
            try:isolation.drop_database(admin_url,name,marker)
            except Exception:cleanup_errors.append('Chưa dọn được DB thử '+name)
        try:area.cleanup()
        except OSError:cleanup_errors.append('Chưa dọn được vùng thử '+str(control))
        for error in cleanup_errors:print(error,flush=True)
    if result is not None:
        result['cleanup_complete']=not cleanup_errors
        result['operational_checks_passed']=result['operational_checks_passed'] and not cleanup_errors
    return result


def operational_pass(result):
    return (
        result['accepted']==8 and result['rejected_full']==2
        and result['unexpected_submit_errors']==0 and result['active_at_gate']==8
        and result['repeat_request_status']==202 and result['repeat_returns_same_task']
        and result['second_active_request_status']==409 and result['queue_recovered_after_full'] is True
        and result['completed']==9 and result['failed']==0 and result['tasks_still_pending']==0
        and not result['result_or_isolation_errors'] and result['single_worker_intervals']
        and result['inventory_rows_written']==0 and result['gpu_samples']==9
        and result['gpu_analysis_errors']==0 and result['model_operation_errors']==0
        and result['comparisons_measured']==18 and len(result['model_calls'])==45
        and result['trace_identity_errors']==0 and result['read_probe']['requests']>0
        and result['read_probe']['errors']==0)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--preflight':print(json.dumps(preflight()));return
    if len(sys.argv)>1 and sys.argv[1]=='--prepare':
        data=json.load(sys.stdin);print(json.dumps(prepare(Path(sys.argv[2]),Path(data['control']),Path(data['image']),data['password'])));return
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,default=HERE.parent);parser.add_argument('--image',type=Path)
    args=parser.parse_args();project=args.project.resolve();isolation.ensure_stopped();isolation.verify_source(project)
    image=args.image
    if image is None:
        selection=None
        if os.name=='nt':
            try:
                import tkinter as tk
                from tkinter import filedialog
                window=tk.Tk();window.withdraw()
                try:selection=filedialog.askopenfilename(title='Chọn một ảnh hàng có chi tiết để thử so sánh trùng',filetypes=[('Ảnh JPG/PNG','*.jpg *.jpeg *.png'),('Tất cả file','*.*')])
                finally:window.destroy()
                if not selection:raise ValueError('Chưa chọn ảnh thử.')
            except Exception as error:
                if isinstance(error,ValueError):raise
                print("Không mở được cửa sổ; hãy dán đường dẫn ảnh.")
        image=Path(selection or input('Dán đường dẫn một ảnh thử JPG/PNG (Copy as path): ').strip().strip('"'))
    image=image.resolve()
    if not image.is_file() or image.stat().st_size>64*1024**2:raise ValueError('Cần một ảnh thử có sẵn, tối đa 64 MiB.')
    password=getpass.getpass('Mật khẩu PostgreSQL của postgres để tạo/dọn DB thử: ')
    result=run(project,image,password)
    if result is None:raise ValueError('Chưa có kết quả bài thử.')
    output=project/'docs'/('duplicate_gpu_queue_'+datetime.now().strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]+'.json');output.parent.mkdir(exist_ok=True)
    with output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print('\n===== KIỂM TRA HÀNG ĐỢI SO SÁNH TRÙNG / GPU =====')
    print(f"Nhận {result['accepted']}/10 tác vụ; từ chối do đầy: {result['rejected_full']}; lỗi gửi không dự kiến: {result['unexpected_submit_errors']}")
    print('Tác vụ đồng thời lúc kiểm giới hạn:',result['active_at_gate'],'/ 8')
    print('Gửi lại cùng yêu cầu trả đúng tác vụ:',result['repeat_returns_same_task'])
    print('Gửi thêm khi user đang bận (mong đợi 409):',result['second_active_request_status'])
    print('Hoàn tất:',result['completed'],'| thất bại:',result['failed'],'| còn chờ:',result['tasks_still_pending'])
    print('Sai kết quả/quyền truy cập kho:',len(result['result_or_isolation_errors']))
    print('Chỉ một worker chạy:',result['single_worker_intervals'])
    print('Nhận lại tác vụ sau khi hết đầy:',result['queue_recovered_after_full'])
    print('Lỗi xử lý so sánh:',result['gpu_analysis_errors'],'| lỗi thao tác mô hình:',result['model_operation_errors'])
    print('Kiểm tra vận hành đạt:',result['operational_checks_passed'])
    print('Mọi thao tác mô hình chạy CUDA:',result['all_model_operations_on_cuda'])
    print('Cặp ảnh đã đối chiếu (mong đợi 18):',result['comparisons_measured'])
    print('Lượt mô hình dùng CPU:',result['cpu_fallback_calls'])
    print('Nạp trước Grounding DINO (ms):',result['grounding_warmup_ms'])
    for field,label in [('peak_torch_allocated_bytes','VRAM PyTorch cấp phát đỉnh'),('peak_torch_reserved_bytes','VRAM PyTorch giữ đỉnh')]:
        print(label+' (MiB):',round(result[field]/1048576,2) if result[field] is not None else 'Chưa đo được')
    print('Lượt đối chiếu đầu (ms):',result['cold_analysis_ms'],'| p95 lượt sau (ms):',result['warm_analysis_p95_ms'])
    print('p95 chờ trong bài thử (ms):',result['queue_wait_p95_ms'],'| giữ worker có kiểm soát (s):',result['controlled_gate_seconds'])
    print('API đọc khi GPU bận:',result['read_probe'])
    resource=result['resources']
    print('PID backend đo:',resource['backend_pid'],'| PID khởi động:',resource['launcher_pid'])
    print('RAM backend đỉnh (MiB):',round(resource['peak_backend_rss_bytes']/1048576,2) if resource.get('peak_backend_rss_bytes') is not None else 'Chưa đo được')
    print('CPU backend p95 (% của một lõi):',resource.get('backend_cpu_p95_one_core_percent'))
    print('Dòng kiểm kê đã ghi (mong đợi 0):',result['inventory_rows_written'])
    print('Dọn dữ liệu thử:',result['cleanup_complete'])
    print('Báo cáo:',output)
    print('Có thể khởi động lại backend chính. Chưa nghiệm thu độ chính xác so sánh/camera thật/Internet.')


if __name__=='__main__':
    try:main()
    except (Exception,KeyboardInterrupt) as error:
        message=str(error) if isinstance(error,ValueError) else type(error).__name__
        raise SystemExit('Chưa hoàn tất kiểm so sánh GPU: '+message)
