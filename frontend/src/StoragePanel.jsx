import {useEffect,useState} from 'react';
import {authRequest} from './authClient';
import './storage.css';

const size=bytes=>(bytes/1048576).toLocaleString('vi-VN',{maximumFractionDigits:2})+' MiB';
const labels={normal:'Còn dung lượng',warning:'Sắp đầy',full:'Đã đầy'};

function Meter({value}){
 return <><div className="storage-heading"><strong>Dung lượng kho</strong><span className={'storage-state '+value.state}>{labels[value.state]}</span></div>
  <p className="storage-value">{size(value.used_bytes)} <span>/ {size(value.limit_bytes)}</span></p>
  <progress aria-label="Dung lượng kho đã dùng" max="100" value={Math.min(100,value.percent)}/>
 </>;
}

export function UserStorage({warehouseId,onChange}){
 const [value,setValue]=useState(null),[error,setError]=useState('');
 useEffect(()=>{
  let active=true,controller=null;
  async function load(){
   if(document.visibilityState==='hidden')return;
   controller?.abort();controller=new AbortController();
   try{const result=await authRequest('/api/storage',undefined,{signal:controller.signal});
    if(active&&result.warehouse_id===warehouseId){setValue(result);setError('');onChange(result);}}
   catch(e){if(active&&e.name!=='AbortError'){setError('Chưa tải được dung lượng.');onChange(null);}}
  }
  load();const timer=setInterval(load,30000);
  document.addEventListener('visibilitychange',load);window.addEventListener('warehouse-storage-refresh',load);
  return()=>{active=false;controller?.abort();clearInterval(timer);document.removeEventListener('visibilitychange',load);window.removeEventListener('warehouse-storage-refresh',load);};
 },[warehouseId,onChange]);
 return <div className="storage-summary" aria-label="Dung lượng lưu trữ của kho">
  {value?<><Meter value={value}/>{value.state!=='normal'&&<p role="status" className="storage-warning">{value.state==='full'?'Đã chặn chụp ảnh mới.':'Kho đã dùng từ 80% dung lượng.'} Liên hệ admin để tăng hạn mức.</p>}</>:<small>{error||'Đang đo dung lượng…'}</small>}
  {value&&error&&<small role="status">{error}</small>}
 </div>;
}

export default function AdminStorage({warehouseId}){
 const [value,setValue]=useState(null),[limit,setLimit]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 useEffect(()=>{
  const controller=new AbortController();setValue(null);setError('');setNotice('');
  authRequest(`/api/admin/warehouses/${warehouseId}/storage`,undefined,{signal:controller.signal})
   .then(result=>{if(!controller.signal.aborted){setValue(result);setLimit(String(result.limit_bytes/1048576));}})
   .catch(e=>{if(!controller.signal.aborted)setError(e.message);});
  return()=>controller.abort();
 },[warehouseId]);
 async function save(e){
  e.preventDefault();if(busy||!value)return;
  setBusy(true);setError('');setNotice('');
  try{const result=await authRequest(`/api/admin/warehouses/${warehouseId}/storage`,{limit_mib:Number(limit),expected_version:value.version});setValue(result);setLimit(String(result.limit_bytes/1048576));setNotice(result.message);}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 async function reload(){
  setBusy(true);setError('');setNotice('');
  try{const result=await authRequest(`/api/admin/warehouses/${warehouseId}/storage`);setValue(result);setLimit(String(result.limit_bytes/1048576));}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 return <section className="storage-admin" aria-label="Hạn mức lưu trữ">
  {value&&<Meter value={value}/>}
  {error&&<p role="alert" className="storage-warning">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <form className="storage-form" onSubmit={save}>
   <label>Hạn mức (MiB)<input type="number" min="1" max="102400" step="1" required value={limit} disabled={busy||!value} onChange={e=>setLimit(e.target.value)}/></label>
   <button type="submit" disabled={busy||!value||Number(limit)===value.limit_bytes/1048576}>{busy?'Đang lưu…':'Lưu hạn mức'}</button>
   <button type="button" disabled={busy} onClick={reload}>Tải lại</button>
  </form>
  <p className="form-note">Cảnh báo từ 80%. Đầy hạn mức chỉ chặn ảnh mới; không tự xóa dữ liệu. Sao lưu và ảnh tạm của hàng đợi được tính riêng.</p>
 </section>;
}
