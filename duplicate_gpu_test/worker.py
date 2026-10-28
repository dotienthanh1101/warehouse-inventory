"""Guarded test entrypoint; real queue and unchanged model inference."""
import json
import os
import sys
import threading
import time
from pathlib import Path

CONTROL=Path(os.environ.get('WAREHOUSE_DUPLICATE_GPU_TEST_ROOT','')).resolve()
if not os.environ.get('WAREHOUSE_DUPLICATE_GPU_TEST_ROOT') or not (CONTROL/'control.json').is_file():
    raise RuntimeError('Chỉ chạy worker qua duplicate_gpu_test/run.py trong vùng thử riêng.')
CONFIG=json.loads((CONTROL/'control.json').read_text(encoding='utf-8'))
if CONFIG.get('format')!='warehouse-duplicate-gpu-test-v1':raise RuntimeError('Sai định dạng vùng thử.')
sys.path.insert(0,CONFIG['project'])
from psycopg.conninfo import conninfo_to_dict
from backend.app.auth_settings import load_settings
from backend.app.warehouse_scope import STORAGE_ROOT,current_warehouse_id
if (conninfo_to_dict(load_settings().database_url).get('dbname')!=CONFIG['database']
        or not CONFIG['database'].startswith('warehouse_load_test_')
        or not Path(STORAGE_ROOT).resolve().is_relative_to(CONTROL)):
    raise RuntimeError('Từ chối chạy trên dữ liệu ngoài vùng thử.')

import torch
from backend.app.daily_api import app
from backend.app import review_api,visual_similarity
from backend.app.image_tasks import ImageTasks

if not torch.cuda.is_available():raise RuntimeError('Bài kiểm GPU cần CUDA khả dụng.')
_thread=threading.local()
_trace_lock=threading.Lock()
_original_check=review_api.create_check
_original_execute=ImageTasks.execute
_original_infer=visual_similarity.SimilarityEngine._infer
_warmed=False


def trace_infer(self,kind,operation):
    calls=getattr(_thread,'model_calls',None)
    def measured_operation(model,device):
        entry={'kind':kind,'device':device,'error':None}
        start=time.perf_counter()
        try:return operation(model,device)
        except Exception as exc:
            entry['error']=type(exc).__name__
            raise
        finally:
            entry['ms']=round((time.perf_counter()-start)*1000,2)
            if calls is not None:calls.append(entry)
    return _original_infer(self,kind,measured_operation)


def trace_check(session_id,document):
    global _warmed
    warmup_ms=None
    if not _warmed:
        # Mirror a real process that has already analysed a capture. Do not
        # unload Grounding DINO to make the comparison appear cheaper.
        from backend.app.duplicate_check import _load
        start=time.perf_counter()
        analysis=review_api.analyze_frame(_load(document['image_url'],document['width'],document['height']))
        if analysis.get('detection_error'):raise RuntimeError('Grounding DINO nạp trước bị lỗi.')
        torch.cuda.synchronize();warmup_ms=round((time.perf_counter()-start)*1000,2);_warmed=True
    torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
    start=time.perf_counter();result=None;error=None;_thread.model_calls=[]
    try:
        result=_original_check(session_id,document)
        comparisons=[c for r in result.get('results',[]) for c in r.get('comparisons',[])]
        if any(c.get('evaluation_status')=='comparison_error' or c.get('comparison_performed') is False for c in comparisons):error='comparison_error'
        return result
    except Exception as exc:
        error='cuda_out_of_memory' if isinstance(exc,torch.cuda.OutOfMemoryError) else type(exc).__name__
        raise
    finally:
        try:torch.cuda.synchronize()
        except Exception:error=error or 'cuda_synchronize_error'
        record={'task_id':getattr(_thread,'task_id',None),'warehouse_id':current_warehouse_id(),
            'analysis_ms':round((time.perf_counter()-start)*1000,2),'grounding_warmup_ms':warmup_ms,
            'peak_allocated_bytes':torch.cuda.max_memory_allocated(),
            'peak_reserved_bytes':torch.cuda.max_memory_reserved(),
            'device_total_bytes':torch.cuda.get_device_properties(0).total_memory,
            'model_calls':_thread.model_calls,'error':error,
            'comparison_count':sum(len(r.get('comparisons',[])) for r in result.get('results',[])) if result else 0}
        with _trace_lock:
            with (CONTROL/'gpu_trace.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(record)+'\n')


def gated_execute(self,row):
    _thread.task_id=row['task_id']
    deadline=time.monotonic()+90
    while not (CONTROL/'release.flag').exists():
        if time.monotonic()>deadline:raise RuntimeError('Chưa mở worker sau 90 giây.')
        time.sleep(.02)
    return _original_execute(self,row)


review_api.create_check=trace_check
visual_similarity.SimilarityEngine._infer=trace_infer
ImageTasks.execute=gated_execute
