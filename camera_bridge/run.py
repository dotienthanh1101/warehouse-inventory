"""Warehouse-side camera bridge. HTTPS push; secrets held only in memory."""
import argparse
from datetime import datetime,timezone
import getpass
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import sys
import threading
import time
from urllib.parse import quote,urlsplit,urlunsplit


def server_origin(value):
    p=urlsplit(value.strip().rstrip('/'))
    if (p.scheme!='https' or not p.hostname or p.path or p.query or p.fragment
            or p.username is not None or p.password is not None or p.port not in (None,443)):
        raise ValueError('Nhập link HTTPS của hệ thống, không kèm đường dẫn.')
    return 'https://'+p.hostname


def camera_url(value,username='',password=''):
    value=value.strip()
    if '://' not in value:value='http://'+value
    if any(c.isspace() for c in value):raise ValueError('Dia chi camera khong duoc chua khoang trang.')
    p=urlsplit(value)
    try:
        ip=ipaddress.IPv4Address(p.hostname)
        allowed=any(ip in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
        if not allowed or p.scheme not in ('http','https') or p.username is not None or p.query or p.fragment or (p.port is not None and not 1<=p.port<=65535):raise ValueError()
    except (ValueError,TypeError):raise ValueError('Nhập IP camera trong mạng kho, ví dụ http://192.168.1.24:8081/live.flv.')
    auth=quote(username,safe='')+':'+quote(password,safe='')+'@' if username else ''
    return urlunsplit((p.scheme,auth+p.netloc,p.path if p.path not in ('','/') else '/live.flv','',''))


class Client:
    """One authenticated TLS connection; discard it after any transport error.

    A failed frame is never replayed here. The caller takes the latest camera
    snapshot before its next attempt. Redirects are never followed.
    """
    def __init__(self,origin):
        self.origin=server_origin(origin);self.host=urlsplit(self.origin).hostname
        self.connection=None
        try:
            import truststore
            self.context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:self.context=ssl.create_default_context()
    def close(self):
        connection,self.connection=self.connection,None
        if connection is not None:connection.close()
    def post(self,path,body,token=None,sequence=None,sent_at=None):
        if self.connection is None:
            self.connection=http.client.HTTPSConnection(self.host,timeout=12,context=self.context)
        connection=self.connection
        headers={'Origin':self.origin,'Content-Type':'image/jpeg' if sequence is not None else 'application/json'}
        if token:headers['Authorization']='Bearer '+token
        if sequence is not None:headers.update({'X-Frame-Sequence':str(sequence),'X-Frame-Time':sent_at})
        data=body if isinstance(body,bytes) else json.dumps(body).encode()
        try:
            connection.request('POST',path,data,headers)
            response=connection.getresponse()
            # Fully consume a bounded response before reusing the connection.
            raw=response.read(65537)
            if len(raw)>65536:raise ValueError('Phan hoi may chu qua lon.')
            status=response.status
            try:value=json.loads(raw)
            except ValueError:value={}
            if getattr(response,'will_close',False) or status!=200:self.close()
            return status,value
        except BaseException:
            self.close()
            raise


class LatestCamera:
    def __init__(self,address):
        self.address=address;self.lock=threading.Lock();self.stop=threading.Event()
        self.latest=None;self.sequence=0;self.thread=threading.Thread(target=self.read,daemon=True)
    def read(self):
        import cv2
        while not self.stop.is_set():
            cap=None
            try:
                cap=cv2.VideoCapture(self.address,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,5000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,3000])
                if not cap.isOpened():self.stop.wait(2);continue
                while not self.stop.is_set():
                    ok,frame=cap.read()
                    if not ok:break
                    with self.lock:
                        self.sequence+=1;self.latest=(frame,self.sequence,time.monotonic(),datetime.now(timezone.utc).isoformat())
            except Exception:pass
            finally:
                if cap is not None:cap.release()
                with self.lock:self.latest=None
            self.stop.wait(1)
    def snapshot(self):
        with self.lock:
            if self.latest is None or time.monotonic()-self.latest[2]>1.5:return None
            frame,seq,at,utc=self.latest
            return frame.copy(),seq,utc
    def close(self):
        self.stop.set()
        if self.thread.ident is not None:self.thread.join(timeout=9)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--server');parser.add_argument('--camera')
    parser.add_argument('--jpeg-quality',type=int,default=80)
    args=parser.parse_args()
    if not 60<=args.jpeg_quality<=95:raise ValueError('JPEG quality can tu 60 den 95.')
    origin=server_origin(args.server or input('Link HTTPS cua he thong: '))
    address=args.camera or input('Dia chi camera tai kho (http://IP:8081/live.flv): ')
    username=input('Tai khoan CAMERA neu co (Enter neu khong): ').strip()
    password=getpass.getpass('Mat khau CAMERA: ') if username else ''
    address=camera_url(address,username,password)
    code=getpass.getpass('Ma ket noi vua tao tren React (dan ma roi Enter): ').strip()
    if not re.fullmatch(r'pair_[A-Za-z0-9_-]{43}',code):raise ValueError('Ma ket noi chua dung dinh dang. Tao ma tren tab Camera.')
    # FFmpeg may print a credential-bearing source URL on failure. Keep native
    # decoder stderr private; all user-facing errors below are sanitized.
    os.environ['OPENCV_LOG_LEVEL']='SILENT';os.environ['OPENCV_FFMPEG_LOGLEVEL']='-8'
    import cv2
    if callable(getattr(cv2,"setLogLevel",None)):cv2.setLogLevel(0)
    from video import VideoStreamer,check_dependencies
    check_dependencies()
    client=Client(origin)
    sink=open(os.devnull,'w');original_stderr=os.dup(2);os.dup2(sink.fileno(),2)
    reader=None;token=None;video_stream=None
    try:
        status,data=client.post('/api/camera/gateway/claim',{'pair_code':code})
        code='';password=''
        if status!=200:raise ValueError(f'Khong ghep duoc (HTTP {status}). Kiem tra ma con han, nguon cu da ngat va dung link HTTPS.')
        token=data['token']
        video_stream=VideoStreamer(address,origin,token,client.context,jpeg_quality=args.jpeg_quality);video_stream.start()
        name=''.join(c for c in data.get('warehouse_name','') if ord(c)>=32)[:100]
        print('Da ghep kho: '+name+'. Video 1080p, timestamp chuan hoa; camera chi doc mot lan.',flush=True)
        print('Ve tab Camera tren React de xem hinh va chup. Ctrl+C de ngat cau noi.',flush=True)
        sent=0;last=0;next_message=0;network_errors=0
        sample_start=time.monotonic();sample_sent=0;sample_total=0.0;sample_max=0.0
        while True:
            began=time.monotonic();snap=video_stream.snapshot_jpeg()
            if snap is None:
                if began>=next_message:print('Dang cho anh moi tu camera tai kho…',flush=True);next_message=began+8
                time.sleep(.2);continue
            jpeg,seq,utc=snap
            if seq==last:time.sleep(.05);continue
            if len(jpeg)>data['max_frame_bytes']:raise ValueError('Anh JPEG qua lon.')
            try:
                upload_started=time.monotonic()
                status,value=client.post('/api/camera/gateway/frame',jpeg,token,seq,utc)
                upload_ms=(time.monotonic()-upload_started)*1000
                if status in (401,403,410):raise ValueError('Cau noi da ngat, het han hoac tai khoan da bi thu hoi. Dang nhap va ghep lai tren React.')
                if status==200:
                    sent+=1;last=seq;network_errors=0
                    sample_sent+=1;sample_total+=upload_ms;sample_max=max(sample_max,upload_ms)
                    elapsed=time.monotonic()-sample_start
                    if elapsed>=5:
                        print(f'Anh chup du phong: {sample_sent/elapsed:.2f} FPS | HTTPS TB {sample_total/sample_sent:.0f} ms | Video dau ra: {video_stream.stats():.1f} FPS',flush=True)
                        if sample_total/sample_sent>1000:print('HTTPS van cham: anh co the vuot moc tuoi 2 giay. Khong dung anh cu de chup.',flush=True)
                        sample_start=time.monotonic();sample_sent=0;sample_total=0.0;sample_max=0.0
                elif status==409:
                    last=seq
                    if began>=next_message:print('Anh cu/sai thu tu hoac dong ho lech. Kiem tra gio may; dang doi anh moi.',flush=True);next_message=began+8
                elif status==429:time.sleep(1)
                else:raise ValueError(f'May chu tu choi anh (HTTP {status}). Dung cau noi va gui ma loi nay de kiem tra.')
            except (OSError,http.client.HTTPException):
                network_errors+=1
                if network_errors>=5:
                    client.close();network_errors=0
                    print('Kenh anh chup dang thu lai; giu luong video neu con ket noi.',flush=True)
                print('Ket noi HTTPS gian doan; se gui khung moi, khong gui lai anh cu.',flush=True);time.sleep(1)
            time.sleep(max(0,1.0-(time.monotonic()-began)))
    finally:
        if video_stream:video_stream.close()
        if token:
            try:client.post('/api/camera/gateway/stop',{},token)
            except Exception:pass
        client.close()
        if reader:reader.close()
        os.dup2(original_stderr,2);os.close(original_stderr);sink.close()


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\nDa dung cau noi.',flush=True)
    except (ValueError,OSError,http.client.HTTPException) as error:
        print('Chua ket noi duoc: '+(str(error) if isinstance(error,ValueError) else type(error).__name__),flush=True)
        raise SystemExit(1)
