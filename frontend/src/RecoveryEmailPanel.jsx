import {useState} from 'react';
import {apiFetch} from './authClient';

export default function RecoveryEmailPanel({email,onDone,onCancel}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 async function save(event){
  event.preventDefault();if(busy)return;
  const form=new FormData(event.currentTarget);setBusy(true);setError('');
  try{
   const response=await apiFetch('/api/auth/email',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:String(form.get('email')).trim(),current_password:String(form.get('current_password'))})});
   const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Không lưu được email. Kiểm tra thông tin và thử lại.');
   onDone(data.message);
  }catch(e){setError(e.message);}finally{setBusy(false);}
 }
 return <section className="surface recovery-email-panel"><p className="eyebrow">TÀI KHOẢN CỦA BẠN</p><h1>Email khôi phục</h1>
  <p className="muted">{email?'Email đang lưu: '+email:'Tài khoản này chưa có email khôi phục.'}</p>
  <p className="form-note">Lưu email bạn đang sử dụng để admin đối chiếu khi cần khôi phục tài khoản. Sau khi lưu, bạn cần đăng nhập lại.</p>
  {error&&<p role="alert" className="portal-alert error">{error}</p>}
  <form className="portal-form" onSubmit={save}><label>Email<input type="email" name="email" defaultValue={email||''} required maxLength={254} autoComplete="email" disabled={busy}/></label>
   <label>Mật khẩu hiện tại<input type="password" name="current_password" required maxLength={128} autoComplete="current-password" disabled={busy}/></label>
   <div className="button-row"><button className="primary-button" disabled={busy}>{busy?'Đang lưu…':'Lưu email'}</button><button className="secondary-button" type="button" disabled={busy} onClick={onCancel}>Hủy</button></div>
  </form>
 </section>;
}
