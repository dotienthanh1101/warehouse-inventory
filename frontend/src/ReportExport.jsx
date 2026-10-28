import {useEffect,useRef,useState} from 'react';
import {apiFetch} from './authClient';
import './reportExport.css';

export default function ReportExport({report,dayId,warehouseId,active=true}){
 const [format,setFormat]=useState('xlsx'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const request=useRef(null);
 const closed=!!dayId&&report?.day_id===dayId&&report?.status==='closed';
 useEffect(()=>{
  setError('');setNotice('');setBusy(false);
  return()=>{const current=request.current;request.current=null;current?.abort();};
 },[dayId,warehouseId,active]);
 async function download(){
  if(!closed||busy||request.current)return;
  const controller=new AbortController();request.current=controller;
  const timeout=setTimeout(()=>controller.abort(),30000);
  setBusy(true);setError('');setNotice('');
  try{
   const response=await apiFetch(`/api/inventory/days/${dayId}/export?format=${format}`,{cache:'no-store',signal:controller.signal});
   if(!response.ok){
    let data={};try{data=await response.json();}catch{}
    throw new Error(typeof data.detail==='string'?data.detail:'Chưa xuất được báo cáo. Hãy tải lại báo cáo rồi thử lại.');
   }
   if(response.headers.get('X-Report-Day-ID')!==dayId||response.headers.get('X-Report-State')!=='closed')throw new Error('File không khớp báo cáo đã chốt đang chọn. Hãy tải lại báo cáo.');
   const blob=await response.blob();
   if(controller.signal.aborted)return;
   const filename=response.headers.get('Content-Disposition')?.match(/filename="([a-zA-Z0-9_.-]+)"/)?.[1]
     ||`kiem-ke-kho_${report.business_date}.${format}`;
   const url=URL.createObjectURL(blob),link=document.createElement('a');
   link.href=url;link.download=filename;document.body.appendChild(link);link.click();link.remove();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
   setNotice('Đã tạo file báo cáo. Kiểm tra thư mục tải xuống của trình duyệt.');
  }catch(e){
   if(controller.signal.aborted){if(request.current===controller&&active)setError('Đã dừng xuất hoặc phản hồi quá chậm. Bạn có thể thử lại.');}
   else setError(e instanceof TypeError?'Mất kết nối khi xuất báo cáo. Hãy thử lại.':e.message);
  }finally{clearTimeout(timeout);if(request.current===controller){request.current=null;setBusy(false);}}
 }
 return <div className="report-export" aria-label="Xuất báo cáo đã chốt">
  <div className="report-export-controls"><label>Định dạng<select aria-label="Định dạng báo cáo" value={format} disabled={busy} onChange={e=>setFormat(e.target.value)}><option value="xlsx">Excel (.xlsx)</option><option value="csv">CSV (.csv)</option></select></label>
   <button type="button" disabled={!closed||busy||!active} onClick={download}>{busy?'Đang xuất…':'Xuất báo cáo'}</button>
  </div>
  {!closed&&<p className="report-export-note">Chốt ngày để xuất báo cáo chính thức.</p>}
  {error&&<p className="report-export-error" role="alert">{error}</p>}
  {notice&&<p className="report-export-note" role="status">{notice}</p>}
 </div>;
}
