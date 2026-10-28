import {useState} from 'react';
import {authRequest} from './authClient';

export default function PasswordPanel({required=false,onDone,onCancel}){
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(event){
    event.preventDefault();if(busy)return;
    const form=new FormData(event.currentTarget),current=String(form.get('current_password')||''),next=String(form.get('new_password')||'');
    if(next!==form.get('confirm_password')){setError('Hai mật khẩu mới chưa khớp.');return;}
    setBusy(true);setError('');
    try{const data=await authRequest('/api/auth/change-password',{current_password:current,new_password:next});onDone(data.message);}
    catch(e){setError(e.message);}finally{setBusy(false);}
  }
  return <main className="account-page"><section className="account-card"><h1>Đổi mật khẩu</h1>
    <p className="account-subtitle">{required?'Bạn cần đổi mật khẩu tạm trước khi sử dụng kho.':'Đổi mật khẩu sẽ đăng xuất tất cả các phiên hiện tại.'}</p>
    {error&&<p role="alert" className="account-message error">{error}</p>}
    <form onSubmit={submit}>
      <label>{required?'Mật khẩu tạm':'Mật khẩu hiện tại'}<input name="current_password" type="password" required maxLength={128} autoComplete="current-password" disabled={busy}/></label>
      <label>Mật khẩu mới<input name="new_password" type="password" required minLength={12} maxLength={128} autoComplete="new-password" disabled={busy}/></label>
      <small>Từ 12 đến 128 ký tự, khác mật khẩu hiện tại.</small>
      <label>Nhập lại mật khẩu mới<input name="confirm_password" type="password" required minLength={12} maxLength={128} autoComplete="new-password" disabled={busy}/></label>
      <button type="submit" className="account-primary" disabled={busy}>{busy?'Đang lưu…':'Lưu mật khẩu mới'}</button>
      {!required&&<button type="button" className="account-switch" disabled={busy} onClick={onCancel}>Quay lại</button>}
    </form></section></main>;
}
