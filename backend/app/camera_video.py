"""Tenant-scoped H.264 relay. No media decoding, disk writes or GPU work."""
import asyncio
import contextlib
from dataclasses import dataclass,field
import json
import logging
import math
import re
import struct
import time
from fastapi import APIRouter,HTTPException,WebSocket,WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from backend.app.auth_store import COOKIE

router=APIRouter(prefix='/api/camera/gateway/video',tags=['Camera video'])
MAX_PACKET=2*1024*1024
logger=logging.getLogger(__name__)


def box_kinds(data):
    offset=0;kinds=[]
    while offset<len(data):
        if len(data)-offset<8:raise ValueError('Truncated MP4')
        size,kind=struct.unpack('!I4s',data[offset:offset+8])
        if size<8 or size>MAX_PACKET or offset+size>len(data):raise ValueError('Invalid MP4')
        kinds.append(kind);offset+=size
    return kinds


def metadata(value):
    required={'mime','width','height','fps'}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-{'segment_seconds','buffer_seconds'}:raise ValueError('Invalid metadata')
    if not isinstance(value['mime'],str) or not re.fullmatch(r'video/mp4; codecs="avc1\.[0-9a-fA-F]{6}"',value['mime']):raise ValueError('H264 only')
    for name,low,high in [('width',64,1920),('height',64,1080),('fps',1,30)]:
        if type(value[name]) is not int or not low<=value[name]<=high:raise ValueError('Invalid video size/rate')
    for name in ('segment_seconds','buffer_seconds'):
        if name in value and (type(value[name]) not in (int,float) or not math.isfinite(value[name]) or not .03<=value[name]<=6):raise ValueError('Invalid video buffering')
    return value


def packet(data,init=False):
    if not isinstance(data,bytes) or len(data)>MAX_PACKET:raise ValueError('Invalid packet size')
    if init:
        if not data.startswith(b'I') or len(data)>128*1024 or box_kinds(data[1:])!=[b'ftyp',b'moov']:raise ValueError('Invalid init')
        return data
    if len(data)<33 or data[0:1]!=b'F':raise ValueError('Invalid fragment')
    sequence,created=struct.unpack('!Qd',data[1:17])
    if not 0<sequence<2**53 or not math.isfinite(created):raise ValueError('Invalid sequence/time')
    if box_kinds(data[17:])!=[b'moof',b'mdat']:raise ValueError('Invalid media boxes')
    age=time.time()-created
    if age>4 or age< -30:raise ValueError('Stale video or clock skew')
    return sequence,created,data


@dataclass
class Channel:
    warehouse_id:str
    channel_id:str
    token:str=field(repr=False)
    metadata:dict
    init:bytes=b''
    latest:tuple|None=None
    condition:asyncio.Condition=field(default_factory=asyncio.Condition)
    active:bool=True
    viewers:int=0


class VideoHub:
    def __init__(self):self.channels={}
    def open(self,row,token,meta):
        current=self.channels.get(row['warehouse_id'])
        if current and current.active:raise ValueError('Video source already connected')
        channel=Channel(row['warehouse_id'],row['channel_id'],token,meta)
        self.channels[row['warehouse_id']]=channel
        return channel
    async def close(self,channel):
        channel.active=False
        async with channel.condition:channel.condition.notify_all()
        if self.channels.get(channel.warehouse_id) is channel:self.channels.pop(channel.warehouse_id,None)


def services(ws):
    state=ws.app.state
    if not hasattr(state,'camera_video_hub'):state.camera_video_hub=VideoHub()
    return state.auth_store,state.camera_gateway,state.camera_video_hub


def check_origin(ws,store):
    if len(ws.headers.getlist('origin'))!=1 or ws.headers.get('origin') not in store.settings.origins:raise ValueError('Origin forbidden')
    # No query-string bearer tokens or arbitrary custom URLs.
    if 'token' in ws.query_params:raise ValueError('Token in URL forbidden')


async def authorize_viewer(ws,store):
    token=ws.cookies.get(COOKIE,'')
    identity=await run_in_threadpool(store.authenticate,token)
    if identity['role']!='user' or identity.get('must_change_password'):raise ValueError('User access required')
    if ws.query_params.get('warehouse_id')!=identity['warehouse_id']:raise ValueError('Warehouse changed')
    return token,identity


async def close_ws(ws,code=1008,reason=''):
    try:await ws.close(code=code,reason=reason)
    except (RuntimeError,WebSocketDisconnect):pass


def log_close(role,error):
    safe={'Video not ready','Viewer limit','Video stalled','Viewer too slow','Video source already connected',
        'Invalid metadata','Invalid init','Invalid fragment','Invalid media boxes','Stale video or clock skew',
        'Out of order','Video rate exceeded','Invalid acknowledgement','Session changed','Wrong channel'}
    detail=str(error) if isinstance(error,ValueError) and str(error) in safe else type(error).__name__
    if isinstance(error,HTTPException):detail='auth_http_'+str(error.status_code)
    if not isinstance(error,WebSocketDisconnect):logger.warning('camera_video_%s_closed reason=%s',role,detail)
    return detail


@router.websocket('/publish')
async def publish_video(ws:WebSocket):
    channel=None;hub=None
    try:
        store,gateway,hub=services(ws);check_origin(ws,store)
        value=ws.headers.get('authorization','')
        if not value.startswith('Bearer ') or len(value)>128:raise ValueError('Bearer required')
        token=value[7:]
        row=await run_in_threadpool(gateway.authorize,token)
        await ws.accept()
        raw=await asyncio.wait_for(ws.receive_text(),15)
        if len(raw)>1024:raise ValueError('Metadata too large')
        meta=metadata(json.loads(raw))
        channel=hub.open(row,token,meta)
        init=await asyncio.wait_for(ws.receive_bytes(),15)
        channel.init=packet(init,init=True)
        last=0;auth_at=time.monotonic()+1;window=time.monotonic();count=0
        while True:
            data=await asyncio.wait_for(ws.receive_bytes(),12)
            sequence,created,data=packet(data)
            if sequence<=last:raise ValueError('Out of order')
            last=sequence
            now=time.monotonic();count+=1
            if now-window>=1:window=now;count=1
            if count>15:raise ValueError('Video rate exceeded')
            if now>=auth_at:
                active=await run_in_threadpool(gateway.authorize,token)
                if active['channel_id']!=channel.channel_id:raise ValueError('Channel changed')
                auth_at=now+1
            async with channel.condition:
                channel.latest=(sequence,created,data);channel.condition.notify_all()
    except (WebSocketDisconnect,ValueError,HTTPException,TimeoutError,RuntimeError) as error:log_close('publish',error)
    finally:
        if channel and hub:await hub.close(channel)
        await close_ws(ws)


@router.websocket('/view')
async def view_video(ws:WebSocket):
    channel=None;registered=False;receiver=None
    try:
        store,gateway,hub=services(ws);check_origin(ws,store)
        cookie,identity=await authorize_viewer(ws,store)
        await ws.accept()
        deadline=time.monotonic()+20
        waiting=False
        while True:
            channel=hub.channels.get(identity['warehouse_id'])
            if channel and channel.active and channel.init and channel.latest:break
            if time.monotonic()>deadline:raise ValueError('Video not ready')
            await run_in_threadpool(store.authenticate,cookie)
            if not waiting:await ws.send_json({'state':'waiting'});waiting=True
            await asyncio.sleep(.5)
        if channel.viewers>=2:raise ValueError('Viewer limit')
        row=await run_in_threadpool(gateway.authorize,channel.token)
        if row['warehouse_id']!=identity['warehouse_id'] or row['channel_id']!=channel.channel_id:raise ValueError('Wrong channel')
        channel.viewers+=1
        registered=True
        await ws.send_json(channel.metadata);await ws.send_bytes(channel.init)
        last=0;auth_at=time.monotonic()+1;pending={};changed=asyncio.Event()
        async def acknowledgements():
            while True:
                ack=await asyncio.wait_for(ws.receive_text(),12)
                if len(ack)>20 or not ack.isdecimal() or int(ack) not in pending:raise ValueError('Invalid acknowledgement')
                pending.pop(int(ack));changed.set()
        receiver=asyncio.create_task(acknowledgements())
        while channel.active:
            now=time.monotonic()
            if receiver.done():receiver.result();raise ValueError('Viewer stopped')
            if now>=auth_at:
                again=await run_in_threadpool(store.authenticate,cookie)
                if again['user_id']!=identity['user_id'] or again['warehouse_id']!=identity['warehouse_id'] or again['role']!='user' or again.get('must_change_password'):raise ValueError('Session changed')
                await run_in_threadpool(gateway.authorize,channel.token)
                auth_at=now+1
            if pending and now-min(pending.values())>10:raise ValueError('Viewer too slow')
            if len(pending)>=8:
                changed.clear()
                try:await asyncio.wait_for(changed.wait(),.2)
                except TimeoutError:pass
                continue
            current=channel.latest
            if not current or current[0]<=last:
                async with channel.condition:
                    if channel.latest and channel.latest[0]>last:continue
                    try:await asyncio.wait_for(channel.condition.wait(),.5)
                    except TimeoutError:pass
                continue
            if time.time()-current[1]>max(8,2*channel.metadata.get('segment_seconds',1)+2):raise ValueError('Video stalled')
            pending[current[0]]=now
            async with asyncio.timeout(8):await ws.send_bytes(current[2])
            last=current[0]
    except (WebSocketDisconnect,ValueError,HTTPException,TimeoutError,RuntimeError) as error:log_close('view',error)
    finally:
        if receiver:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError,Exception):await receiver
        if registered and channel:channel.viewers-=1
        await close_ws(ws)
