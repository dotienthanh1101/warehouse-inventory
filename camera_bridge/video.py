"""Low-latency H.264 preview, separate from full-resolution inventory JPEGs."""
from collections import deque
import json
import os
import re
import shutil
import struct
import socket
from datetime import datetime,timezone
import subprocess
import threading
import time

MAX_BOX=2*1024*1024


def ffmpeg_executable():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        path=shutil.which('ffmpeg')
        if path:return path
        raise ValueError('Thieu FFmpeg. Cai backend/requirements-video.txt truoc khi chay.')


def check_dependencies():
    from websockets.sync.client import connect
    return ffmpeg_executable()


def read_exact(stream,size):
    data=bytearray()
    while len(data)<size:
        part=stream.read(size-len(data))
        if not part:raise EOFError('Video encoder stopped')
        data.extend(part)
    return bytes(data)


def read_box(stream):
    header=read_exact(stream,8);size,kind=struct.unpack('!I4s',header)
    if not 8<=size<=MAX_BOX:raise ValueError('Invalid MP4 box size')
    return kind,header+read_exact(stream,size-8)


def video_mime(init):
    position=init.find(b'avcC')
    if position<0 or position+8>len(init):raise ValueError('H264 configuration missing')
    profile=init[position+5:position+8].hex()
    return 'video/mp4; codecs="avc1.'+profile+'"'


"""Small bounded fMP4 metadata reader for copied H264 streams."""

def boxes(data,start=0,end=None):
    end=len(data) if end is None else end
    while start<end:
        if end-start<8:raise ValueError('Truncated MP4 box')
        size,kind=struct.unpack_from('!I4s',data,start)
        if size<8 or start+size>end:raise ValueError('Invalid MP4 size')
        yield kind,data[start:start+size]
        start+=size

def descendants(data,wanted):
    for kind,box in boxes(data):
        if kind==wanted:yield box
        if kind in (b'moov',b'trak',b'mdia',b'minf',b'stbl',b'mvex',b'moof',b'traf'):
            yield from descendants(box[8:],wanted)

def track_info(init):
    tkhd=next(descendants(init,b'tkhd'),None)
    mdhd=next(descendants(init,b'mdhd'),None)
    if tkhd is None or mdhd is None or len(tkhd)<16 or len(mdhd)<32:raise ValueError('Missing H264 track metadata')
    width,height=struct.unpack_from('!II',tkhd,len(tkhd)-8)
    timescale=struct.unpack_from('!I',mdhd,28 if mdhd[8]==1 else 20)[0]
    if not timescale or not width or not height:raise ValueError('Invalid H264 track metadata')
    return width>>16,height>>16,timescale

def fragment_seconds(fragment,timescale):
    tfhd=next(descendants(fragment,b'tfhd'),None)
    trun=next(descendants(fragment,b'trun'),None)
    if tfhd is None or trun is None:raise ValueError('Missing H264 fragment timing')
    flags=int.from_bytes(tfhd[9:12],'big');offset=16
    if flags&1:offset+=8
    if flags&2:offset+=4
    default_duration=struct.unpack_from('!I',tfhd,offset)[0] if flags&8 else 0
    flags=int.from_bytes(trun[9:12],'big');count=struct.unpack_from('!I',trun,12)[0]
    if not 1<=count<=10000:raise ValueError('Invalid H264 sample count')
    offset=16+(4 if flags&1 else 0)+(4 if flags&4 else 0)
    if flags&256:
        total=0;stride=4*sum(bool(flags&flag) for flag in (256,512,1024,2048))
        for _ in range(count):total+=struct.unpack_from('!I',trun,offset)[0];offset+=stride
    else:total=default_duration*count
    if not total:raise ValueError('Missing H264 sample durations')
    return total/timescale


_ENCODER=None

def select_encoder(executable):
    global _ENCODER
    if _ENCODER is None:
        command=[executable,'-hide_banner','-loglevel','error','-f','lavfi','-i',
            'color=size=1920x1080:rate=30','-frames:v','3','-c:v','h264_nvenc',
            '-preset','p1','-tune','ull','-f','null','-']
        try:
            result=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            _ENCODER='h264_nvenc' if result.returncode==0 else 'libx264'
        except (OSError,subprocess.TimeoutExpired):_ENCODER='libx264'
    return _ENCODER


class JPEGFrames:
    """Bounded concatenated-JPEG parser for the private loopback output."""
    def __init__(self):self.buffer=bytearray()
    def feed(self,data):
        self.buffer.extend(data);frames=[]
        while True:
            start=self.buffer.find(b'\xff\xd8')
            if start<0:
                if len(self.buffer)>4*1024*1024:raise ValueError('JPEG_BUFFER_LIMIT')
                if len(self.buffer)>1:del self.buffer[:-1]
                return frames
            if start:del self.buffer[:start]
            end=self.buffer.find(b'\xff\xd9',2)
            if end<0:
                if len(self.buffer)>4*1024*1024:raise ValueError('JPEG_BUFFER_LIMIT')
                return frames
            end+=2
            if end>4*1024*1024:raise ValueError('JPEG_BUFFER_LIMIT')
            frames.append(bytes(self.buffer[:end]));del self.buffer[:end]


class NativeH264Source:
    """One native camera pull; normalized video + full-resolution still JPEG."""
    def __init__(self,address,executable,codec=None,jpeg_quality=80):
        self.address=address;self.stop=threading.Event();self.lock=threading.Lock()
        self.init=None;self.latest=None;self.sequence=0;self.times=deque(maxlen=40);self.total_frames=0
        self.error='';self._info=None;self.photo=None;self.photo_sequence=0;self.jpeg_socket=None
        self.codec=codec or select_encoder(executable)
        self.listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1',0));self.listener.listen(1);self.listener.settimeout(.5)
        try:
            self.process=subprocess.Popen(native_command(executable,address,self.listener.getsockname()[1],self.codec,jpeg_quality),
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,bufsize=0,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except BaseException:self.listener.close();raise
        self.progress=threading.Thread(target=self.read_progress,daemon=True)
        self.reader=threading.Thread(target=self.read,daemon=True)
        self.jpeg_reader=threading.Thread(target=self.read_jpeg,daemon=True)
        self.progress.start();self.reader.start();self.jpeg_reader.start()
    def read_progress(self):
        try:
            for line in iter(lambda:self.process.stderr.readline(2049),b''):
                # Drain native errors privately; never print camera credentials.
                known=((b'401 Unauthorized','CAMERA_AUTH_401'),(b'Unknown encoder','NATIVE_ENCODER_UNAVAILABLE'),
                    (b'Error initializing output stream','NATIVE_ENCODER_INIT_FAILED'),
                    (b'Error while opening encoder','NATIVE_ENCODER_INIT_FAILED'),
                    (b'OpenEncodeSessionEx failed','NATIVE_ENCODER_INIT_FAILED'),
                    (b'Error reinitializing filters','NATIVE_FILTER_INIT_FAILED'),
                    (b'Connection refused','CAMERA_CONNECTION_REFUSED'))
                for pattern,code in known:
                    if pattern in line:
                        with self.lock:self.error=code
        except (OSError,ValueError):pass
    def read_jpeg(self):
        parser=JPEGFrames();connection=None
        try:
            while not self.stop.is_set():
                try:connection,_=self.listener.accept();break
                except socket.timeout:continue
            if connection is None:return
            self.jpeg_socket=connection;connection.settimeout(.5)
            while not self.stop.is_set():
                try:data=connection.recv(65536)
                except socket.timeout:continue
                if not data:break
                for jpeg in parser.feed(data):
                    with self.lock:
                        self.photo_sequence+=1
                        self.photo=(jpeg,self.photo_sequence,time.monotonic(),datetime.now(timezone.utc).isoformat())
        except (OSError,ValueError):pass
        finally:
            if connection:connection.close()
            with self.lock:self.photo=None
    def read(self):
        init=b'';fragment=b'';info=None
        try:
            while not self.stop.is_set():
                kind,data=read_box(self.process.stdout)
                if kind in (b'ftyp',b'moov'):
                    init+=data
                    if len(init)>128*1024:raise ValueError('INIT_TOO_LARGE')
                    if kind==b'moov':
                        width,height,timescale=track_info(init)
                        if not 64<=width<=1920 or not 64<=height<=1080:raise ValueError('SOURCE_SIZE_UNSUPPORTED')
                        info={'mime':video_mime(init),'width':width,'height':height,'timescale':timescale}
                        with self.lock:self.init=init
                elif kind==b'moof':fragment=data
                elif kind==b'mdat' and fragment and info:
                    fragment+=data
                    if len(fragment)>MAX_BOX:raise ValueError('FRAGMENT_TOO_LARGE')
                    count=sum(struct.unpack_from('!I',box,12)[0] for box in descendants(fragment,b'trun'))
                    if not 1<=count<=10000:raise ValueError('INVALID_SAMPLE_COUNT')
                    # Our native output has normalized display timestamps and a
                    # fixed GOP. Source PTS/GOP estimates never trigger a restart.
                    seconds=count/30
                    with self.lock:
                        self._info={k:v for k,v in info.items() if k!='timescale'}
                        self._info.update(fps=30,segment_seconds=round(min(5,max(.03,seconds)),3),buffer_seconds=1.5)
                        self.total_frames+=count;self.times.append((time.monotonic(),self.total_frames))
                        self.sequence+=1;self.latest=(self.sequence,time.time(),fragment)
                    fragment=b''
        except (EOFError,OSError,ValueError,struct.error) as error:
            safe=('INIT_TOO_LARGE','SOURCE_SIZE_UNSUPPORTED','FRAGMENT_TOO_LARGE','INVALID_SAMPLE_COUNT')
            code=str(error) if str(error) in safe else 'CAMERA_STREAM_ENDED' if isinstance(error,EOFError) else 'INVALID_VIDEO_CONTAINER'
            with self.lock:
                if not self.error:self.error=code
    def snapshot(self):
        with self.lock:return self.init,self.latest
    def snapshot_jpeg(self):
        with self.lock:
            if self.photo is None or time.monotonic()-self.photo[2]>1.5:return None
            data,sequence,_,utc=self.photo
            return data,sequence,utc
    def info(self):
        with self.lock:return dict(self._info) if self._info else None
    def stats(self):
        with self.lock:samples=list(self.times)
        if len(samples)<2 or time.monotonic()-samples[-1][0]>6:return 0.0
        span=samples[-1][0]-samples[0][0]
        return round((samples[-1][1]-samples[0][1])/span,1) if span>0 else 0.0
    def close(self):
        self.stop.set()
        if self.process.poll() is None:self.process.terminate()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
        self.listener.close()
        if self.jpeg_socket:
            try:self.jpeg_socket.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        for thread in (self.progress,self.reader,self.jpeg_reader):thread.join(timeout=2)
        self.process.stdout.close();self.process.stderr.close()


def native_command(executable,address,jpeg_port,codec='libx264',jpeg_quality=80):
    if codec not in ('libx264','h264_nvenc'):raise ValueError('Unsupported video encoder')
    q=max(2,min(8,round(10-jpeg_quality/12)))
    command=[executable,'-hide_banner','-nostdin','-loglevel','error','-filter_complex_threads','1',
        '-rw_timeout','7000000','-analyzeduration','1000000','-probesize','1000000','-i',address,
        '-filter_complex','[0:v:0]settb=1/90000,setpts=N*3000,split=2[v][p];[p]fps=1[photo]',
        '-map','[v]','-an','-c:v',codec]
    command+=['-preset','p1','-tune','ull'] if codec=='h264_nvenc' else ['-preset','ultrafast','-tune','zerolatency','-threads','2','-keyint_min','15','-sc_threshold','0']
    command+=['-bf','0','-g','15','-profile:v','main','-level:v','4.0',
        '-pix_fmt','yuv420p','-b:v','4000k','-maxrate','5000k','-bufsize','1000k','-fps_mode','passthrough',
        '-movflags','empty_moov+default_base_moof+frag_keyframe','-flush_packets','1','-f','mp4','pipe:1',
        '-map','[photo]','-an','-c:v','mjpeg','-threads','1','-q:v',str(q),'-f','image2pipe',f'tcp://127.0.0.1:{jpeg_port}']
    return command


class VideoStreamer:
    def __init__(self,camera,origin,token,context,jpeg_quality=80):
        self.jpeg_quality=jpeg_quality;self.address=camera;self.origin=origin;self.token=token;self.context=context
        self.stop=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True)
        self.ws=None;self.encoder=None;self.sent_times=deque(maxlen=40);self.failure=''
        self._photo_key=None;self._photo_serial=0
    def start(self):self.thread.start()
    def run(self):
        from websockets.sync.client import connect
        executable=ffmpeg_executable();retry=1;encoder=None;codec_override=None
        try:
            while not self.stop.is_set():
                try:
                    if encoder is None or encoder.error or encoder.process.poll() is not None:
                        if encoder and encoder.codec=='h264_nvenc' and encoder.error in ('NATIVE_ENCODER_UNAVAILABLE','NATIVE_ENCODER_INIT_FAILED'):
                            codec_override='libx264'
                            print('NVIDIA chua khoi tao duoc; thu encoder CPU native.',flush=True)
                        if encoder:encoder.close()
                        encoder=NativeH264Source(self.address,executable,codec=codec_override,jpeg_quality=self.jpeg_quality)
                        self.encoder=encoder
                    deadline=time.monotonic()+25
                    while not self.stop.is_set():
                        init,latest=encoder.snapshot()
                        if init and latest and encoder.info():break
                        if encoder.error or encoder.process.poll() is not None:raise ValueError(encoder.error or 'CAMERA_SOURCE_STOPPED')
                        if time.monotonic()>deadline:raise TimeoutError('No camera video')
                        self.stop.wait(.05)
                    if self.stop.is_set():break
                    with connect('wss://'+self.origin.split('://',1)[1]+'/api/camera/gateway/video/publish',
                        origin=self.origin,ssl=self.context,additional_headers={'Authorization':'Bearer '+self.token},
                        compression=None,open_timeout=10,close_timeout=2,ping_interval=10,ping_timeout=10,
                        max_size=4096,max_queue=1,proxy=None) as ws:
                        self.ws=ws
                        ws.send(json.dumps(encoder.info()));ws.send(b'I'+init)
                        last=0;retry=1;self.failure='';meta=encoder.info()
                        print(f"H264 NORMALIZED: {meta['width']}x{meta['height']} | muc tieu {meta['fps']} FPS | GOP {meta['segment_seconds']}s. Ma hoa native {encoder.codec}; doc camera 1 lan.",flush=True)
                        while not self.stop.is_set():
                            _,latest=encoder.snapshot()
                            if latest is None or latest[0]==last:
                                if encoder.error or encoder.process.poll() is not None:raise ValueError(encoder.error or 'CAMERA_SOURCE_STOPPED')
                                if latest and time.time()-latest[1]>12:raise TimeoutError('Source stalled')
                                self.stop.wait(.01);continue
                            sequence,created,data=latest
                            if time.time()-created>10:raise TimeoutError('Video stalled')
                            ws.send(b'F'+struct.pack('!Qd',sequence,created)+data)
                            last=sequence;self.sent_times.append(time.monotonic())
                except Exception as error:
                    safe=('CAMERA_SOURCE_STOPPED','SOURCE_SIZE_UNSUPPORTED','FRAGMENT_TOO_LARGE','INVALID_SAMPLE_COUNT',
                        'CAMERA_AUTH_401','NATIVE_ENCODER_UNAVAILABLE','NATIVE_ENCODER_INIT_FAILED','NATIVE_FILTER_INIT_FAILED',
                        'CAMERA_CONNECTION_REFUSED','CAMERA_STREAM_ENDED','INVALID_VIDEO_CONTAINER')
                    self.failure=str(error) if isinstance(error,ValueError) and str(error) in safe else type(error).__name__
                    if hasattr(error,'rcvd') and error.rcvd is not None:self.failure+=' code='+str(error.rcvd.code)
                    if not self.stop.is_set():print('Video dang ket noi lai ('+self.failure+'). Giu nguon camera neu con hoat dong.',flush=True)
                finally:
                    self.ws=None
                self.stop.wait(retry);retry=min(8,retry*2)
        finally:
            if encoder:encoder.close()
            self.encoder=None
    def snapshot_jpeg(self):
        encoder=self.encoder
        photo=encoder.snapshot_jpeg() if encoder else None
        if photo is None:return None
        data,local_sequence,utc=photo
        key=(encoder,local_sequence)
        if key!=self._photo_key:self._photo_serial+=1;self._photo_key=key
        return data,self._photo_serial,utc
    def stats(self):
        encoder=self.encoder
        return encoder.stats() if encoder else 0.0
    def close(self):
        self.stop.set()
        if self.ws:
            try:self.ws.close()
            except Exception:pass
        self.thread.join(timeout=8)
