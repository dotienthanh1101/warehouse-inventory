import {useEffect, useRef, useState} from 'react';
import {apiFetch} from './authClient';
import './reportTrace.css';

const time = value => value ? new Date(value).toLocaleString('vi-VN', {timeZone:'Asia/Ho_Chi_Minh'}) : 'Chưa lưu';
const person = value => value ? (value.display_name || value.username || value.user_id) : 'Bản cũ chưa lưu người duyệt';

export default function ReportTrace({dayId, markerId, active, onClose}) {
  const [data,setData]=useState(null),[session,setSession]=useState(''),[offset,setOffset]=useState(0);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
  const request=useRef(null);
  useEffect(()=>{
    if (!active || !dayId || markerId===null || markerId===undefined) return;
    const controller=new AbortController();request.current=controller;
    const timer=setTimeout(()=>controller.abort(),20000);
    setBusy(true);setError('');setData(null);
    (async()=>{
      try {
        const q=new URLSearchParams({marker_id:String(markerId),offset:String(offset)});
        if(session)q.set('session_id',session);
        const response=await apiFetch(`/api/inventory/days/${dayId}/trace?${q}`,{cache:'no-store',signal:controller.signal});
        const value=await response.json();
        if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'Chưa đọc được nguồn số liệu.');
        if(request.current===controller && !controller.signal.aborted)setData(value);
      }catch(e){
        if(request.current===controller)setError(e.name==='AbortError'?'Đọc nguồn quá thời gian. Bấm thử lại.':e.message);
      }finally{
        clearTimeout(timer);if(request.current===controller)setBusy(false);
      }
    })();
    return()=>{request.current=null;controller.abort();clearTimeout(timer);};
  },[dayId,markerId,active,session,offset,attempt]);
  if(!active || markerId===null || markerId===undefined)return null;
  return <section className="report-trace" aria-label="Nguồn số liệu báo cáo">
    <div className="report-trace-heading"><h3>Nguồn số liệu · ID {String(markerId).padStart(2,'0')}</h3>
      <button type="button" className="quiet" onClick={onClose}>Đóng</button></div>
    {busy&&<p role="status">Đang đọc biên nhận…</p>}
    {error&&<p role="alert">{error} <button type="button" className="secondary" onClick={()=>{setAttempt(v=>v+1);}}>Thử lại</button></p>}
    {data&&<>
      <p><strong>{data.product_name}</strong> · {data.product_code}</p>
      <p className="report-trace-note">{data.note}</p>
      <label>Phiên đã chốt
        <select value={data.selected_session_id} disabled={busy} onChange={e=>{setSession(e.target.value);setOffset(0);}}>
          {data.sessions.map(s=><option key={s.session_id} value={s.session_id}>#{s.session_order} · {s.name} · Tồn {s.quantity??'Chưa có bản lưu'} · Chênh lệch {s.difference>0?'+':''}{s.difference??'Chưa rõ'}</option>)}
        </select>
      </label>
      <p>{data.total} biên nhận có loại hàng này trong phiên đã chọn.</p>
      {!data.receipts.length&&<p>Không có kiện loại này được ghi trong phiên. Số 0 lấy từ bản tồn đã chốt sau xác nhận đủ phạm vi.</p>}
      {data.receipts.map(receipt=><article className="report-trace-receipt" key={receipt.review_id}>
        <div className="report-trace-heading"><strong>Biên nhận {receipt.review_id.slice(0,8)} · Bản {receipt.revision}</strong><span>Ghi mới: {receipt.new_count} kiện</span></div>
        <p>{person(receipt.confirmed_by)} · {time(receipt.recorded_at)}</p>
        <div className="report-trace-evidence">
          {receipt.image_available?<a href={receipt.image_url} target="_blank" rel="noopener noreferrer"><img src={receipt.image_url} alt={`Ảnh bằng chứng ${receipt.review_id.slice(0,8)}`}/>Mở ảnh bằng chứng</a>:<p className="report-trace-warning">Ảnh bằng chứng không còn trên máy chủ.</p>}
          <div>{receipt.decisions.length?receipt.decisions.map(d=><div className="report-trace-decision" key={d.box_id}>
            <strong>{d.box_id} · {d.decision==='new'?'Kiện mới · +1':'Cùng kiện · +0'}</strong>
            {d.note&&<p>{d.note}</p>}
            {d.decision==='duplicate'&&(d.reference?.image_available?<a href={d.reference.image_url} target="_blank" rel="noopener noreferrer">Xem ảnh kiện đã ghi · {d.reference.instance_id.slice(0,8)}</a>:<p>Chưa mở được ảnh đối chiếu trong bản lưu.</p>)}
          </div>):<p>Bản cũ chưa lưu đủ chi tiết quyết định đối chiếu.</p>}</div>
        </div>
        <details><summary>Lịch sử hiệu chỉnh ({receipt.correction_history.length})</summary>
          {receipt.correction_history.map((h,i)=><div key={`${h.revision}:${i}`}><p>Bản {h.revision} · {time(h.saved_at)} · {person(h.reviewed_by)}</p>
            <ul>{h.boxes.map(b=><li key={b.box_id}>{b.box_id} · ID {b.marker_id} · {b.reviewed?'Đã kiểm tra':'Chưa duyệt'}{b.note?` · ${b.note}`:''}</li>)}</ul>
          </div>)}
        </details>
      </article>)}
      {data.total>data.limit&&<div className="report-trace-pages">
        <button type="button" className="secondary" disabled={busy||offset===0} onClick={()=>setOffset(Math.max(0,offset-data.limit))}>Trước</button>
        <span>{Math.floor(offset/data.limit)+1}/{Math.ceil(data.total/data.limit)}</span>
        <button type="button" className="secondary" disabled={busy||offset+data.limit>=data.total} onClick={()=>setOffset(offset+data.limit)}>Sau</button>
      </div>}
    </>}
  </section>;
}
