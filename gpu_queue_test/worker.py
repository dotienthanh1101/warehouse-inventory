"""Test-only entrypoint: recorded camera input; real production queue and CV."""
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

CONTROL=Path(os.environ.get('WAREHOUSE_GPU_QUEUE_TEST_ROOT','')).resolve()
if not os.environ.get('WAREHOUSE_GPU_QUEUE_TEST_ROOT') or not (CONTROL/'control.json').is_file():
    raise RuntimeError('Chỉ chạy worker qua gpu_queue_test/run.py trong vùng thử riêng.')
CONFIG=json.loads((CONTROL/'control.json').read_text(encoding='utf-8'))
if CONFIG.get('format')!='warehouse-gpu-queue-test-v1':
    raise RuntimeError('Sai định dạng vùng thử.')
sys.path.insert(0,CONFIG['project'])
from psycopg.conninfo import conninfo_to_dict
from backend.app.auth_settings import load_settings
from backend.app.warehouse_scope import STORAGE_ROOT,current_warehouse_id
if (conninfo_to_dict(load_settings().database_url).get('dbname')!=CONFIG['database']
        or not CONFIG['database'].startswith('warehouse_load_test_')
        or not Path(STORAGE_ROOT).resolve().is_relative_to(CONTROL)):
    raise RuntimeError('Từ chối khởi động worker trên dữ liệu ngoài vùng thử.')

import cv2
import numpy as np
import torch
from backend.app.daily_api import app
from backend.app import review_api
from backend.app.main import camera
from backend.app.image_tasks import ImageTasks

if not torch.cuda.is_available():
    raise RuntimeError('Bài kiểm GPU cần CUDA khả dụng; chưa chạy tác vụ.')

_thread=threading.local()
_trace_lock=threading.Lock()
_original_analysis=review_api.analyze_frame
_original_execute=ImageTasks.execute


def collect_recorded_frame():
    warehouse=current_warehouse_id()
    path=CONTROL/'frames'/(warehouse+'.png')
    frame=cv2.imdecode(np.fromfile(str(path),dtype=np.uint8),cv2.IMREAD_COLOR)
    if frame is None:raise RuntimeError('Không đọc được ảnh thử của đúng kho.')
    reader=camera._reader()
    with reader.lock:
        reader.revision=1
        reader.stop_event.clear()
    return frame,{'frame_index':0,'sequence':1,'received_at':datetime.now(timezone.utc).isoformat()},1


def trace_analysis(frame):
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    start=time.perf_counter();result=None;error=None
    try:
        result=_original_analysis(frame)
        if result.get('detection_error'):error='analysis_error'
        return result
    except Exception as exc:
        error='cuda_out_of_memory' if isinstance(exc,torch.cuda.OutOfMemoryError) else type(exc).__name__
        raise
    finally:
        try:torch.cuda.synchronize()
        except Exception:error=error or 'cuda_synchronize_error'
        record={'task_id':getattr(_thread,'task_id',None),'warehouse_id':current_warehouse_id(),
                'analysis_ms':round((time.perf_counter()-start)*1000,2),
                'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
                'peak_reserved_bytes':torch.cuda.max_memory_reserved(),
                'device_total_bytes':torch.cuda.get_device_properties(0).total_memory,
                'error':error,'detected_boxes':len(result.get('boxes',[])) if result else None}
        with _trace_lock:
            with (CONTROL/'gpu_trace.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(record)+'\n')


def gated_execute(self,row):
    _thread.task_id=row['task_id']
    # Controlled admission phase: prevent completion until all ten submissions
    # have received a response, so the eight-task boundary is reproducible.
    deadline=time.monotonic()+90
    while not (CONTROL/'release.flag').exists():
        if time.monotonic()>deadline:raise RuntimeError('Bài thử chưa mở worker sau 90 giây.')
        time.sleep(.02)
    return _original_execute(self,row)


review_api.collect_one_frame=collect_recorded_frame
review_api.analyze_frame=trace_analysis
ImageTasks.execute=gated_execute
