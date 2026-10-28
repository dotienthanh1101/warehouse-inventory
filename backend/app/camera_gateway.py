"""Authenticated push camera: no server-side URL fetch; latest RAM frame only."""
import hashlib
import re
import secrets
import threading
import time
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import cv2
import numpy as np
from fastapi import HTTPException
from backend.app.auth_store import token_digest
from backend.app.warehouse_scope import warehouse_scope

MAX_BODY=4*1024**2
MAX_PIXELS=4_194_304
SCHEMA='''
CREATE TABLE IF NOT EXISTS auth_camera_gateways (
 channel_id TEXT PRIMARY KEY,
 warehouse_id TEXT NOT NULL UNIQUE REFERENCES auth_warehouses(warehouse_id),
 user_id TEXT NOT NULL REFERENCES auth_users(user_id),
 session_token_hash TEXT NOT NULL,
 account_version BIGINT NOT NULL,
 pair_hash TEXT UNIQUE,
 pair_expires_at TIMESTAMPTZ NOT NULL,
 token_hash TEXT UNIQUE,
 expires_at TIMESTAMPTZ NOT NULL,
 revoked BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
'''


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def decode_frame(data):
    if len(data)>MAX_BODY:raise HTTPException(413,'Ảnh gửi lên vượt 4 MiB.')
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            if image.format!='JPEG' or min(image.size)<64 or image.width*image.height>MAX_PIXELS:
                raise ValueError()
            image.verify()
        frame=cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_COLOR)
        if frame is None or frame.shape[0]*frame.shape[1]>MAX_PIXELS:raise ValueError()
        return frame
    except (ValueError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning,cv2.error) as error:
        raise HTTPException(422,'Cần ảnh JPEG hợp lệ, mỗi cạnh ít nhất 64 px và tối đa 4 megapixel.') from error


class CameraGateway:
    def __init__(self,store,camera):
        self.store=store;self.camera=camera
        self.lock=threading.RLock()
        self.next_upload={}

    def initialize(self):
        with self.store.pool.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(21456006)')
            c.execute(SCHEMA)
            # A process restart has no surviving camera frames or sequence state.
            c.execute('UPDATE auth_camera_gateways SET revoked=TRUE,pair_hash=NULL WHERE revoked=FALSE')
        self.camera.gateway=self

    def _active(self,c,row):
        return c.execute('''SELECT 1 FROM auth_users u JOIN auth_sessions s ON s.user_id=u.user_id
            JOIN auth_warehouses w ON w.owner_id=u.user_id
            WHERE u.user_id=%s AND w.warehouse_id=%s AND u.is_active=TRUE AND u.role='user'
            AND u.must_change_password=FALSE AND u.account_version=%s
            AND s.account_version=u.account_version AND s.token_hash=%s AND s.expires_at>now()
            AND (u.temporary_password_expires_at IS NULL OR u.temporary_password_expires_at>now())''',
            (row['user_id'],row['warehouse_id'],row['account_version'],row['session_token_hash'])).fetchone() is not None

    def pair(self,identity,cookie):
        if identity['role']!='user':raise HTTPException(403,'Chỉ user được ghép camera vào kho của mình.')
        self.store.rate_limit('gateway-pair',identity['user_id'],10,60)
        now=datetime.now(timezone.utc);code='pair_'+secrets.token_urlsafe(32);channel=uuid4().hex
        with self.lock,self.store.pool.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(21456004)')
            row={'user_id':identity['user_id'],'warehouse_id':identity['warehouse_id'],
                 'account_version':identity['account_version'],'session_token_hash':token_digest(cookie)}
            if not self._active(c,row):raise HTTPException(401,'Phiên đăng nhập đã hết hiệu lực.')
            if c.execute("SELECT 1 FROM auth_image_tasks WHERE warehouse_id=%s AND state IN ('preparing','queued','running')",(identity['warehouse_id'],)).fetchone():
                raise HTTPException(409,'Đợi tác vụ ảnh hoàn tất rồi tạo mã kết nối.')
            self.camera.ensure_gateway_available(identity['warehouse_id'])
            c.execute('DELETE FROM auth_camera_gateways WHERE warehouse_id=%s',(identity['warehouse_id'],))
            c.execute('''INSERT INTO auth_camera_gateways(channel_id,warehouse_id,user_id,session_token_hash,
                account_version,pair_hash,pair_expires_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                (channel,identity['warehouse_id'],identity['user_id'],token_digest(cookie),identity['account_version'],
                 digest(code),now+timedelta(minutes=5),now+timedelta(hours=8)))
            self.store.audit(c,identity['user_id'],identity['warehouse_id'],'camera_gateway_pair')
        return {'pair_code':code,'expires_at':(now+timedelta(minutes=5)).isoformat(),
                'message':'Nhập mã này vào cầu nối tại kho trong 5 phút.'}

    def claim(self,code):
        token='gw_'+secrets.token_urlsafe(32)
        with self.lock,self.store.pool.connection() as c:
            row=c.execute('''SELECT * FROM auth_camera_gateways WHERE pair_hash=%s
                AND pair_expires_at>now() AND revoked=FALSE AND token_hash IS NULL FOR UPDATE''',(digest(code),)).fetchone()
            if row is None or not self._active(c,row):raise HTTPException(401,'Mã ghép không đúng, đã dùng hoặc hết hạn.')
            warehouse=c.execute('SELECT name FROM auth_warehouses WHERE warehouse_id=%s',(row['warehouse_id'],)).fetchone()
            self.camera.enable_gateway(row['warehouse_id'],row['channel_id'])
            try:
                c.execute('UPDATE auth_camera_gateways SET pair_hash=NULL,token_hash=%s WHERE channel_id=%s',(digest(token),row['channel_id']))
                self.store.audit(c,row['user_id'],row['warehouse_id'],'camera_gateway_claim')
            except BaseException:
                self.camera.disable_gateway(row['warehouse_id'],row['channel_id']);raise
        return {'token':token,'warehouse_id':row['warehouse_id'],'warehouse_name':warehouse['name'],'expires_at':row['expires_at'].isoformat(),
                'max_frame_bytes':MAX_BODY,'recommended_fps':3}

    def authorize(self,token,admit=False):
        if not re.fullmatch(r'gw_[A-Za-z0-9_-]{43}',token):raise HTTPException(401,'Khóa cầu nối không hợp lệ.')
        with self.lock,self.store.pool.connection() as c:
            row=c.execute('SELECT * FROM auth_camera_gateways WHERE token_hash=%s',(digest(token),)).fetchone()
            if row is None:raise HTTPException(401,'Cầu nối đã bị thu hồi. Tạo mã ghép mới trên React.')
            if row['revoked'] or row['expires_at']<=datetime.now(timezone.utc) or not self._active(c,row):
                self.camera.disable_gateway(row['warehouse_id'],row['channel_id'])
                raise HTTPException(401,'Đăng nhập hoặc khóa cầu nối đã hết hiệu lực. Ghép lại trên React.')
            if admit:
                now=time.monotonic()
                if now<self.next_upload.get(row['channel_id'],0):raise HTTPException(429,'Gửi ảnh quá nhanh.',headers={'Retry-After':'1'})
                self.next_upload[row['channel_id']]=now+.12
            return dict(row)

    def publish(self,token,sequence,sent_at,data,frame):
        if type(sequence) is not int or not 1<=sequence<2**53:raise HTTPException(422,'Số thứ tự ảnh không hợp lệ.')
        age=(datetime.now(timezone.utc)-sent_at).total_seconds()
        if age>5 or age< -30:raise HTTPException(409,'Ảnh quá cũ hoặc đồng hồ máy cầu nối lệch. Đồng bộ ngày giờ rồi thử lại.')
        # Reauthorize after upload/decode, before publishing, to reject revocation
        # during a slow request. All channel transitions use this same lock.
        with self.lock:
            row=self.authorize(token)
            self.camera.publish_gateway(row['warehouse_id'],row['channel_id'],sequence,data,frame,max(0,age))
        return {'accepted':True,'sequence':sequence}

    def revoke(self,warehouse_id,channel_id=None):
        with self.lock,self.store.pool.connection() as c:
            if channel_id:
                row=c.execute('SELECT * FROM auth_camera_gateways WHERE warehouse_id=%s AND channel_id=%s FOR UPDATE',(warehouse_id,channel_id)).fetchone()
            else:row=c.execute('SELECT * FROM auth_camera_gateways WHERE warehouse_id=%s FOR UPDATE',(warehouse_id,)).fetchone()
            if row:
                c.execute('UPDATE auth_camera_gateways SET revoked=TRUE,pair_hash=NULL WHERE channel_id=%s',(row['channel_id'],))
                self.camera.disable_gateway(warehouse_id,row['channel_id'])
                self.next_upload.pop(row['channel_id'],None)
                self.store.audit(c,row['user_id'],warehouse_id,'camera_gateway_revoke')
        return {'disconnected':True}

    def stop_token(self,token):
        if not re.fullmatch(r'gw_[A-Za-z0-9_-]{43}',token):raise HTTPException(401,'Khóa cầu nối không hợp lệ.')
        with self.store.pool.connection() as c:row=c.execute('SELECT warehouse_id,channel_id FROM auth_camera_gateways WHERE token_hash=%s',(digest(token),)).fetchone()
        # Allow the holder to stop its own channel even after browser logout.
        if row:self.revoke(row['warehouse_id'],row['channel_id'])
        return {'disconnected':True}

    def status(self,identity):
        with self.lock,self.store.pool.connection() as c:
            row=c.execute('SELECT * FROM auth_camera_gateways WHERE warehouse_id=%s',(identity['warehouse_id'],)).fetchone()
            if row is None:return {'state':'not_paired'}
            now=datetime.now(timezone.utc)
            valid=not row['revoked'] and row['expires_at']>now and self._active(c,row)
            if not valid:
                self.camera.disable_gateway(row['warehouse_id'],row['channel_id'])
                return {'state':'disconnected'}
            if row['token_hash'] is None:return {'state':'waiting' if row['pair_expires_at']>now else 'expired'}
            return {'state':'paired','expires_at':row['expires_at'].isoformat()}
