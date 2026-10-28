"""Read-only camera/codec diagnostic. No DB, web login, pairing or source edits."""
import argparse
from contextlib import contextmanager
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time


@contextmanager
def private_native_stderr():
    """FFmpeg/OpenCV errors can contain the camera URL. Never print that URL."""
    previous=os.dup(2)
    with open(os.devnull,'w') as sink:
        os.dup2(sink.fileno(),2)
        try:yield
        finally:os.dup2(previous,2);os.close(previous)


def load_camera_helpers(project):
    path=project/'camera_bridge/run.py'
    if not path.is_file():raise ValueError('Khong tim thay camera_bridge/run.py. Chay trong thu muc goc du an.')
    spec=importlib.util.spec_from_file_location('camera_probe_bridge',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def measure_camera(cv2,address,duration=10,warmup=2):
    cap=None;count=0;shape=None;reported=None;reason=None;elapsed=0
    # Suppress native decoder logs, including credential-bearing URLs on failure.
    with private_native_stderr():
        try:
            cap=cv2.VideoCapture(address,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,7000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,3000])
            if not cap.isOpened():return {'ok':False,'reason':'Khong mo duoc luong camera. Kiem tra dia chi, quyen camera va mang LAN.'}
            value=cap.get(cv2.CAP_PROP_FPS)
            if value and 0<value<1000:reported=round(value,2)
            begin=time.monotonic()
            while time.monotonic()-begin<warmup:
                ok,frame=cap.read()
                if not ok:return {'ok':False,'reason':'Mo duoc luong nhung khong doc duoc anh.'}
            begin=time.monotonic()
            while time.monotonic()-begin<duration:
                ok,frame=cap.read()
                if not ok:reason='Camera ngat trong luc do';break
                count+=1;shape=frame.shape
            elapsed=time.monotonic()-begin
        except Exception as error:reason=type(error).__name__
        finally:
            if cap is not None:cap.release()
    result={'ok':count>0 and reason is None,'decoded_frames':count,'seconds':round(elapsed,2),
        'decoded_fps':round(count/elapsed,2) if elapsed else 0,'reported_fps':reported,
        'width':int(shape[1]) if shape else None,'height':int(shape[0]) if shape else None}
    if reason:result['reason']=reason
    return result


def benchmark_command(executable,codec):
    command=[executable,'-hide_banner','-loglevel','error','-f','lavfi','-i',
        'testsrc2=size=1920x1080:rate=30','-frames:v','90','-an','-c:v',codec]
    if codec=='libx264':command+=['-preset','ultrafast','-tune','zerolatency','-threads','2']
    elif codec=='h264_nvenc':command+=['-preset','p1','-tune','ull','-bf','0']
    else:raise ValueError('Unsupported codec')
    return command+['-pix_fmt','yuv420p','-b:v','4000k','-f','null','-']


def benchmark_codec(executable,codec):
    begin=time.monotonic()
    try:
        result=subprocess.run(benchmark_command(executable,codec),capture_output=True,timeout=30,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired:return {'ok':False,'reason':'Vuot 30 giay; da dung phep thu.'}
    seconds=time.monotonic()-begin
    if result.returncode:
        # This command's input is a synthetic filter, never a camera URL or secret.
        lines=result.stderr.decode('utf-8',errors='replace').strip().splitlines()
        return {'ok':False,'reason':' | '.join(lines[:4])[:600] or 'Encoder khong chay duoc'}
    return {'ok':True,'synthetic_frames':90,'width':1920,'height':1080,
        'seconds':round(seconds,3),'synthetic_encode_fps':round(90/seconds,2),
        'note':'Anh tong hop; khong phai FPS camera, trinh duyet hay qua Internet.'}


def ffmpeg_read_command(executable,address):
    # Decode the actual stream to a null sink, without frame-rate conversion,
    # resizing, video encoding, disk output or a Python raw-video pipe.
    return [executable,'-hide_banner','-nostdin','-loglevel','info',
        '-stats_period','0.5','-progress','pipe:1','-rw_timeout','5000000',
        '-i',address,'-map','0:v:0','-an','-sn','-dn',
        '-fps_mode','passthrough','-f','null','-']


def read_progress(stream,samples,lock):
    for raw in iter(lambda:stream.readline(2049),b''):
        match=re.fullmatch(rb'frame=\s*(\d+)\s*',raw)
        if match:
            with lock:
                samples.append((time.monotonic(),int(match[1])))
                del samples[:-100]


def read_metadata(stream,result):
    # Never store/print FFmpeg's raw lines: they can contain a credential URL.
    for raw in iter(lambda:stream.readline(2049),b''):
        line=raw.decode('utf-8',errors='replace')
        if result.get('codec') is None and 'Video:' in line:
            codec=re.search(r'Video:\s*([A-Za-z0-9_]+)',line)
            size=re.search(r'\b(\d{2,5})x(\d{2,5})\b',line)
            if codec:result['codec']=codec[1]
            if size:result['width'],result['height']=int(size[1]),int(size[2])


def progress_result(first,last,meta):
    elapsed=last[0]-first[0];count=last[1]-first[1]
    return {'ok':elapsed>=1 and count>0,'decoded_frames':max(0,count),
        'seconds':round(elapsed,2),'decoded_fps':round(count/elapsed,2) if elapsed>0 else 0,
        **meta,'note':'FFmpeg giai ma truc tiep, khong tao khung lap de du 30 FPS.'}


def measure_ffmpeg(executable,address,duration=10,warmup=2):
    process=subprocess.Popen(ffmpeg_read_command(executable,address),stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    lock=threading.Lock();samples=[];meta={};first=None;started=None
    readers=[threading.Thread(target=read_progress,args=(process.stdout,samples,lock),daemon=True),
        threading.Thread(target=read_metadata,args=(process.stderr,meta),daemon=True)]
    for reader in readers:reader.start()
    deadline=time.monotonic()+28
    try:
        while time.monotonic()<deadline:
            with lock:current=list(samples)
            positive=next((s for s in current if s[1]>0),None)
            if started is None and positive:started=positive[0]
            if started is not None:
                if first is None:
                    first=next((s for s in current if s[0]>=started+warmup),None)
                if first is not None and current and current[-1][0]-first[0]>=duration:
                    return progress_result(first,current[-1],dict(meta))
            if process.poll() is not None:break
            time.sleep(.05)
        with lock:last=samples[-1] if samples else None
        if first is not None and last and last[0]-first[0]>=1:
            result=progress_result(first,last,dict(meta));result['ok']=False
            result['reason']='Luong ngat hoac vuot thoi gian do.';return result
        return {'ok':False,**meta,'reason':'Khong doc duoc du khung trong gioi han 28 giay; khong in URL/mat khau.'}
    finally:
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
        for reader in readers:reader.join(timeout=2)
        process.stdout.close();process.stderr.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=Path.cwd())
    parser.add_argument('--codecs-only',action='store_true')
    parser.add_argument('--compare-readers',action='store_true')
    args=parser.parse_args()
    if args.codecs_only and args.compare_readers:raise ValueError('Chi chon mot che do do.')
    project=args.project.resolve();bridge=load_camera_helpers(project)
    import imageio_ffmpeg
    executable=imageio_ffmpeg.get_ffmpeg_exe()
    report={'diagnostic':'camera-1080-readonly-v2','project_modified':False,'db_accessed':False}
    if not args.codecs_only:
        print('Chi do camera LAN va encoder; khong ghep ma hay ghi kiem ke.',flush=True)
        address=input('Dia chi camera (http://IP:8081/live.flv): ').strip()
        username=input('Tai khoan CAMERA (Enter neu khong): ').strip()
        password=getpass.getpass('Mat khau CAMERA: ') if username else ''
        address=bridge.camera_url(address,username,password);password=''
        print(('1/2' if args.compare_readers else '1/3')+' - Doc camera truc tiep: khoang 12 giay...',flush=True)
        os.environ['OPENCV_LOG_LEVEL']='SILENT';os.environ['OPENCV_FFMPEG_LOGLEVEL']='-8'
        import cv2
        if callable(getattr(cv2,'setLogLevel',None)):cv2.setLogLevel(0)
        report['camera_lan']=measure_camera(cv2,address)
        print('CAMERA_LAN: '+json.dumps(report['camera_lan'],ensure_ascii=True),flush=True)
        if args.compare_readers:
            print('2/2 - FFmpeg doc cung camera, khong chuyen thanh anh BGR trong Python...',flush=True)
            result=measure_ffmpeg(executable,address)
            address='';username=''
            print('FFMPEG_LAN: '+json.dumps(result,ensure_ascii=True),flush=True)
            print('Khong sua du an, khong ghi file hay du lieu kiem ke.',flush=True)
            return
        address='';username=''
    print('2/3 - Thu encoder CPU voi 90 khung tong hop Full HD...',flush=True)
    report['cpu_1080']=benchmark_codec(executable,'libx264')
    print('CPU_1080: '+json.dumps(report['cpu_1080'],ensure_ascii=True),flush=True)
    print('3/3 - Thu encoder NVIDIA voi cung kich thuoc...',flush=True)
    report['nvenc_1080']=benchmark_codec(executable,'h264_nvenc')
    print('NVENC_1080: '+json.dumps(report['nvenc_1080'],ensure_ascii=True),flush=True)
    print('Khong sua du an, khong ghi file, khong thay doi luong dang cai. Khong chia se mat khau.',flush=True)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\nDa dung phep do. Khong thay doi du lieu.')
    except Exception as error:
        # Do not print exception text: third-party errors could contain credentials.
        print('Chua do duoc ('+type(error).__name__+'). Kiem tra thu muc du an va thu vien video da cai.')
        raise SystemExit(1)
