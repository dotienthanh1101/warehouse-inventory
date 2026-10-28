import {useEffect,useState} from 'react';
import {authRequest} from './authClient';
import './serverStorage.css';

const gib=bytes=>(bytes/1073741824).toLocaleString('vi-VN',{maximumFractionDigits:2})+' GiB';
const labels={normal:'Ổn định',warning:'Cần chú ý',critical:'Cần xử lý dung lượng'};

export default function ServerStorage(){
 const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  let active=true,controller=null;
  async function load(){
   if(document.visibilityState==='hidden')return;
   controller?.abort();controller=new AbortController();setBusy(true);
   try{const value=await authRequest('/api/admin/storage-capacity',undefined,{signal:controller.signal});if(active){setData(value);setError('');}}
   catch(e){if(active&&e.name!=='AbortError')setError(e.message);}
   finally{if(active&&!controller.signal.aborted)setBusy(false);}
  }
  load();const timer=setInterval(load,60000);document.addEventListener('visibilitychange',load);
  return()=>{active=false;controller?.abort();clearInterval(timer);document.removeEventListener('visibilitychange',load);};
 },[attempt]);
 return <section className="surface server-storage" aria-label="Dung lượng hệ thống">
  <div className="card-title"><div><h2>Dung lượng hệ thống</h2><p>Ổ lưu dữ liệu các kho</p></div><button className="text-button" disabled={busy} onClick={()=>setAttempt(n=>n+1)}>Làm mới dung lượng</button></div>
  {error&&<p className="portal-alert error" role="alert">{error} Chưa có số liệu mới để cấp thêm dung lượng.</p>}
  {data?<><div className="server-storage-state"><span className={'storage-state '+data.state}>{labels[data.state]}</span><span>{data.warehouse_count} kho người dùng · tổng hạn mức {gib(data.allocated_limit_bytes)}</span></div>
   <div className="server-storage-grid">
    <div><span>Ổ đĩa còn trống</span><strong>{gib(data.free_bytes)}</strong></div>
    <div><span>Hạn mức đã cấp, chưa dùng</span><strong>{gib(data.unspent_quota_bytes)}</strong></div>
    <div><span>{data.headroom_bytes<0?'Ngân sách đang thiếu':'Có thể cấp thêm'}</span><strong>{gib(Math.abs(data.headroom_bytes))}</strong></div>
   </div>
   <div className="server-disk-label"><span>Toàn ổ đã dùng {data.physical_used_percent}%</span><span>Tổng {gib(data.total_bytes)}</span></div>
   <progress aria-label="Dung lượng toàn ổ đã sử dụng" max="100" value={data.physical_used_percent}/>
   {data.state!=='normal'&&<p className="server-capacity-warning" role="status">{!data.capture_allowed_by_disk?'Đã chặn ảnh mới vì ổ gần đầy. Vẫn xem và chốt dữ liệu đã có được.':data.headroom_bytes<0?'Hạn mức đã cấp vượt ngân sách hiện còn. Tạm ngừng cấp thêm; giảm hạn mức chưa dùng hoặc bổ sung ổ lưu trữ.':'Ngân sách còn ít hoặc ổ đã dùng từ 80%. Kiểm tra sức chứa trước khi mở rộng.'}</p>}
   <p className="form-note">Dự phòng {gib(data.reserve_bytes)}. Có thể cấp thêm = ổ trống − hạn mức chưa dùng − dự phòng. Bản sao lưu trên cùng ổ đã nằm trong phần chiếm dụng thực tế.</p>
  </>:<p className="form-note">{busy?'Đang đo dung lượng…':'Chưa có số liệu dung lượng.'}</p>}
 </section>;
}
