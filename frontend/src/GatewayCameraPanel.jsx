import {useEffect,useState,useRef} from 'react';
import {authRequest} from './authClient';
import './gatewayCamera.css';

export default function GatewayCameraPanel({warehouseId,busy,onChange}){
 const [state,setState]=useState('not_paired'),[code,setCode]=useState(''),[expires,setExpires]=useState('');
 const operation=useRef(0);
 const [working,setWorking]=useState(false),[error,setError]=useState(''),[copied,setCopied]=useState(false);
 useEffect(()=>{
  let alive=true;const controller=new AbortController();let timer;
  setCode('');setError('');setCopied(false);setState('not_paired');
  async function poll(){
   const current=operation.current;
   try{const result=await authRequest('/api/camera/gateway/status',undefined,{signal:controller.signal});
    if(alive&&current===operation.current){setState(result.state);if(result.state!=='waiting')setCode('');}
   }catch(e){if(alive&&e.name!=='AbortError')setError(e.message);}
   if(alive)timer=setTimeout(poll,3000);
  }
  poll();return()=>{alive=false;controller.abort();clearTimeout(timer);};
 },[warehouseId]);
 async function pair(){
  if(working||busy)return;operation.current+=1;setWorking(true);setError('');setCopied(false);
  try{const result=await authRequest('/api/camera/gateway/pair',{});setCode(result.pair_code);setExpires(result.expires_at);setState('waiting');onChange?.();}
  catch(e){setError(e.message);}finally{setWorking(false);}
 }
 async function revoke(){
  if(working||busy)return;operation.current+=1;setWorking(true);setError('');
  try{await authRequest('/api/camera/gateway/revoke',{});setCode('');setState('disconnected');onChange?.();}
  catch(e){setError(e.message);}finally{setWorking(false);}
 }
 async function copy(){try{await navigator.clipboard.writeText(code);setCopied(true);}catch{setError('Chọn và sao chép mã trong ô bên dưới.');}}
 return <section className="gateway-camera" aria-label="Cầu nối camera từ xa">
  <div className="gateway-heading"><strong>Camera tại kho</strong><span className={'pill '+(state==='paired'?'good':'')}>{state==='paired'?'Đã ghép':state==='waiting'?'Chờ ghép':'Chưa ghép'}</span></div>
  <p>Tạo mã tại đây, rồi nhập vào cầu nối trên máy tại kho. Video 1080p được chuẩn hóa thời gian; camera chỉ đọc một lần cho cả video và ảnh chụp.</p>
  <div className="actions"><a className="gateway-download" href="/api/camera/gateway/agent" download>Tải cầu nối</a><button type="button" disabled={busy||working||state==='paired'} onClick={pair}>{working?'Đang thực hiện…':'Tạo mã kết nối'}</button><button type="button" className="secondary" disabled={busy||working||!['paired','waiting'].includes(state)} onClick={revoke}>Ngắt cầu nối</button></div>
  {code&&<div className="gateway-pair-code"><label htmlFor="gateway-pair-code">Mã dùng một lần · hết hạn lúc {new Date(expires).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'})}</label><div><input id="gateway-pair-code" value={code} readOnly spellCheck={false} autoComplete="off" onFocus={e=>e.target.select()}/><button type="button" className="secondary" onClick={copy}>{copied?'Đã sao chép':'Sao chép'}</button></div><small>Không gửi mã này cho người khác. Tạo lại mã nếu đã hết hạn.</small></div>}
  {error&&<p className="gateway-error" role="alert">{error}</p>}
 </section>;
}
