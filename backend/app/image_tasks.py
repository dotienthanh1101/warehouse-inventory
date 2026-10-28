"""FR-36: bounded, durable task metadata; one in-process image worker.

No inventory confirmations run here. GPU work shares the legacy global lock.
"""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from fastapi import HTTPException, Response
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from backend.app.account_gate import account_gate
from backend.app.warehouse_scope import warehouse_scope, ScopedPath

logger=logging.getLogger(__name__)
INPUTS=ScopedPath('data','task_inputs')
ACTIVE=('preparing','queued','running')
SCHEMA='''
CREATE TABLE IF NOT EXISTS auth_image_tasks (
 task_id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES auth_users(user_id),
 warehouse_id TEXT NOT NULL REFERENCES auth_warehouses(warehouse_id),
 request_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('capture','duplicate-check')),
 account_version BIGINT NOT NULL,
 session_token_hash TEXT NOT NULL,
 payload JSONB NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('preparing','queued','running','completed','failed')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
 result JSONB, error_code INTEGER, error_message TEXT,
 UNIQUE(user_id,request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS auth_image_tasks_one_active
 ON auth_image_tasks(user_id) WHERE state IN ('preparing','queued','running');
CREATE INDEX IF NOT EXISTS auth_image_tasks_queue ON auth_image_tasks(state,created_at,task_id);
'''

class ImageTasks:
    def __init__(self,store):
        self.store=store
        self.app=None
        self.worker=None
        self.wake=asyncio.Event()
        self.stopping=False
        self.pending_failure=None
        self.preparation_failures={}

    def _interrupted(self):
        with self.store.pool.connection() as c:
            c.execute(SCHEMA)
            rows=c.execute("""UPDATE auth_image_tasks SET state='failed',finished_at=now(),
                error_code=503,error_message='Backend đã dừng hoặc khởi động lại. Hãy tạo lượt xử lý mới.'
                WHERE state IN ('preparing','queued','running') RETURNING task_id,warehouse_id""").fetchall()
        for row in rows:self.cleanup(row)

    async def start(self):
        await run_in_threadpool(self._interrupted)
        self.worker=asyncio.create_task(self._loop(),name='image-task-worker')

    async def close(self):
        self.stopping=True;self.wake.set()
        if self.worker is not None:await self.worker
        await run_in_threadpool(self._interrupted)

    def reserve(self,identity,token_hash,request_id,kind,payload):
        with self.store.pool.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(21456004)')
            allowed=c.execute('''SELECT 1 FROM auth_users u JOIN auth_sessions s ON s.user_id=u.user_id
                JOIN auth_warehouses w ON w.owner_id=u.user_id
                WHERE u.user_id=%s AND w.warehouse_id=%s AND u.is_active=TRUE AND u.role='user'
                AND u.must_change_password=FALSE AND u.account_version=%s
                AND s.account_version=u.account_version AND s.token_hash=%s AND s.expires_at>now()
                AND (u.temporary_password_expires_at IS NULL OR u.temporary_password_expires_at>now())
                FOR SHARE OF u''',(identity['user_id'],identity['warehouse_id'],identity['account_version'],token_hash)).fetchone()
            if not allowed:raise HTTPException(401,'Phiên đăng nhập không còn hiệu lực.')
            previous=c.execute('SELECT * FROM auth_image_tasks WHERE user_id=%s AND request_id=%s',
                (identity['user_id'],request_id)).fetchone()
            if previous:
                if previous['kind']!=kind or previous['payload'].get('request')!=payload:
                    raise HTTPException(409,'Mã yêu cầu đã dùng cho nội dung khác.')
                return previous,False
            if self.stopping:raise HTTPException(503,'Backend đang dừng. Thử lại sau khi khởi động.')
            if c.execute("SELECT 1 FROM auth_image_tasks WHERE user_id=%s AND state IN ('preparing','queued','running')",(identity['user_id'],)).fetchone():
                raise HTTPException(409,'Bạn đang có một tác vụ ảnh chưa hoàn tất. Mở phần Tác vụ ảnh để theo dõi.')
            count=c.execute("SELECT COUNT(*) n FROM auth_image_tasks WHERE state IN ('preparing','queued','running')").fetchone()['n']
            if count>=self.store.settings.max_image_tasks:
                raise HTTPException(429,'Hàng đợi ảnh đang đầy. Vui lòng thử lại sau.',headers={'Retry-After':'5'})
            row=c.execute("""INSERT INTO auth_image_tasks(task_id,user_id,warehouse_id,request_id,kind,
                account_version,session_token_hash,payload,state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'preparing') RETURNING *""",
                (uuid4().hex,identity['user_id'],identity['warehouse_id'],request_id,kind,
                 identity['account_version'],token_hash,Jsonb({'request':payload}))).fetchone()
        return row,True

    def mark_queued(self,task_id,payload):
        with self.store.pool.connection() as c:
            row=c.execute("UPDATE auth_image_tasks SET payload=%s,state='queued' WHERE task_id=%s AND state='preparing' RETURNING *",
                (Jsonb(payload),task_id)).fetchone()
        if row is None:raise HTTPException(409,'Tác vụ không còn chờ xử lý.')
        return row

    def cancel_user(self,user_id,token_hash=None):
        with self.store.pool.connection() as c:
            query="""UPDATE auth_image_tasks SET state='failed',finished_at=now(),error_code=401,
                error_message='Phiên đăng nhập đã được thu hồi. Tác vụ chờ đã dừng.'
                WHERE user_id=%s AND state IN ('preparing','queued')"""
            params=(user_id,)
            if token_hash:
                query+=' AND session_token_hash=%s';params+=(token_hash,)
            rows=c.execute(query+' RETURNING task_id,warehouse_id',params).fetchall()
        for row in rows:self.cleanup(row)

    def fail(self,task_id,code,message):
        with self.store.pool.connection() as c:
            c.execute("""UPDATE auth_image_tasks SET state='failed',finished_at=now(),error_code=%s,error_message=%s
                WHERE task_id=%s AND state IN ('preparing','queued','running')""",(code,message,task_id))

    def get(self,identity,task_id=None):
        with self.store.pool.connection() as c:
            if task_id:
                row=c.execute('SELECT * FROM auth_image_tasks WHERE task_id=%s AND user_id=%s AND warehouse_id=%s',
                    (task_id,identity['user_id'],identity['warehouse_id'])).fetchone()
            else:
                row=c.execute('''SELECT * FROM auth_image_tasks WHERE user_id=%s AND warehouse_id=%s
                    ORDER BY created_at DESC,task_id DESC LIMIT 1''',(identity['user_id'],identity['warehouse_id'])).fetchone()
        if row is None and task_id:raise HTTPException(404,'Không tìm thấy tác vụ ảnh.')
        return row

    def public(self,row):
        if row is None:return None
        position=None
        if row['state']=='queued':
            with self.store.pool.connection() as c:
                position=1+c.execute("""SELECT COUNT(*) n FROM auth_image_tasks WHERE
                    state='running' OR (state='queued' AND (created_at,task_id)<(%s,%s))""",
                    (row['created_at'],row['task_id'])).fetchone()['n']
        request=row['payload']['request']
        return {k:row[k] for k in ('task_id','kind','state','created_at','started_at','finished_at','error_code','error_message')} | {
            'session_id':request.get('session_id'),'review_id':request.get('review_id'),
            'revision':request.get('revision'),'queue_position':position,'result_ready':row['state']=='completed'}

    def claim(self):
        with self.store.pool.connection() as c:
            row=c.execute("""SELECT task_id FROM auth_image_tasks WHERE state='queued'
                ORDER BY created_at,task_id LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
            if row is None:return None
            return c.execute("UPDATE auth_image_tasks SET state='running',started_at=now() WHERE task_id=%s RETURNING *",(row['task_id'],)).fetchone()

    def authorize(self,row):
        with self.store.pool.connection() as c:
            ok=c.execute('''SELECT 1 FROM auth_sessions s JOIN auth_users u ON u.user_id=s.user_id
                JOIN auth_warehouses w ON w.owner_id=u.user_id WHERE u.user_id=%s AND w.warehouse_id=%s
                AND u.role='user' AND u.is_active=TRUE AND u.must_change_password=FALSE
                AND (u.temporary_password_expires_at IS NULL OR u.temporary_password_expires_at>now())
                AND u.account_version=%s AND s.account_version=u.account_version
                AND s.token_hash=%s AND s.expires_at>now()''',
                (row['user_id'],row['warehouse_id'],row['account_version'],row['session_token_hash'])).fetchone()
        if not ok:raise HTTPException(401,'Phiên đăng nhập đã hết hiệu lực. Tác vụ chưa chạy được dừng lại.')

    @staticmethod
    def cleanup(row):
        with warehouse_scope(row['warehouse_id']):
            try:(INPUTS/(row['task_id']+'.png')).unlink(missing_ok=True)
            except OSError:logger.warning('Không dọn được ảnh tạm của tác vụ %s',row['task_id'])

    def execute(self,row):
        from backend.app.main import capture_lock,CaptureOptions
        from backend.app.review_api import capture_review_work,duplicate_check_review,DuplicateCheckRequest,read_review,review_fingerprint
        from backend.app.image_task_api import require_open_session
        with warehouse_scope(row['warehouse_id']),capture_lock:
            self.authorize(row)
            payload=row['payload'];request=payload['request']
            require_open_session(request['session_id'])
            if row['kind']=='capture':
                from backend.app.main import camera
                if not camera.is_current(payload['camera_session']):
                    raise HTTPException(409,'Camera đã đổi trong lúc chờ. Hãy chụp lại.')
                frame=cv2.imdecode(np.fromfile(str(INPUTS/(row['task_id']+'.png')),dtype=np.uint8),cv2.IMREAD_COLOR)
                if frame is None:raise HTTPException(409,'Không đọc được ảnh đã chụp. Hãy chụp lại.')
                snapshot=(frame,payload['frame_metadata'],payload['camera_session'])
                return capture_review_work(Response(),CaptureOptions(session_id=request['session_id']),snapshot,row['task_id'])
            if review_fingerprint(read_review(request['review_id']))!=payload['fingerprint']:
                raise HTTPException(409,'Bản nháp đã đổi trong lúc chờ. Lưu và kiểm tra trùng lại.')
            return duplicate_check_review(request['review_id'],DuplicateCheckRequest(
                session_id=request['session_id'],revision=request['revision']),Response())

    def complete(self,row,result):
        with self.store.pool.connection() as c:
            c.execute("UPDATE auth_image_tasks SET state='completed',finished_at=now(),result=%s WHERE task_id=%s AND state='running'",
                (Jsonb(result),row['task_id']))

    async def _loop(self):
        while not self.stopping:
            row=None
            try:
                for task_id,failure in list(self.preparation_failures.items()):
                    await run_in_threadpool(self.fail,task_id,*failure)
                    self.preparation_failures.pop(task_id,None)
                if self.pending_failure:
                    await run_in_threadpool(self.fail,*self.pending_failure)
                    self.pending_failure=None
                row=await run_in_threadpool(self.claim)
                if row is None:
                    self.wake.clear()
                    try:await asyncio.wait_for(self.wake.wait(),timeout=.5)
                    except TimeoutError:pass
                    continue
                async with account_gate(self.app,row['user_id']):
                    result=await run_in_threadpool(self.execute,row)
                    await run_in_threadpool(self.cleanup,row)
                    await run_in_threadpool(self.complete,row,result)
            except HTTPException as error:
                if row:self.pending_failure=(row['task_id'],error.status_code,str(error.detail))
            except Exception:
                logger.exception('Lỗi xử lý tác vụ ảnh')
                if row:self.pending_failure=(row['task_id'],500,'Không xử lý được ảnh. Tác vụ đã dừng; hãy thử lại.')
                await asyncio.sleep(.5)
            finally:
                if row:await run_in_threadpool(self.cleanup,row)
        if self.pending_failure:
            await run_in_threadpool(self.fail,*self.pending_failure)
            self.pending_failure=None
