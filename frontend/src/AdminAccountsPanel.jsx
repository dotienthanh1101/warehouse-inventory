import {useEffect, useState} from 'react';
import {authRequest} from './authClient';
import AdminWarehousePanel from './AdminWarehousePanel';
import AdminStorage from './StoragePanel';

const labels={lock:'Khóa tài khoản',unlock:'Mở khóa',revoke:'Thu hồi đăng nhập',reset:'Cấp mật khẩu tạm'};
const dateText=value=>value ? new Date(value).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'}) : '—';

export default function AdminAccountsPanel({view='accounts',onWarehouseOpen,onAccounts,recoveryTarget}){
  const [listing,setListing]=useState(null),[detail,setDetail]=useState(null),[overview,setOverview]=useState(null);
  const [search,setSearch]=useState(''),[status,setStatus]=useState('all'),[query,setQuery]=useState({search:'',status:'all',offset:0});
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [action,setAction]=useState(''),[reason,setReason]=useState(''),[adminPassword,setAdminPassword]=useState('');
  const [temporary,setTemporary]=useState(null);
  const [warehouseId,setWarehouseId]=useState('');
  const [recoveryRequests,setRecoveryRequests]=useState(null),[recoveryTicket,setRecoveryTicket]=useState(''),[identityConfirmed,setIdentityConfirmed]=useState(false);
  useEffect(()=>{if(view==='accounts')setWarehouseId('');},[view]);
  async function refresh(q=query){
    const params=new URLSearchParams({...q,limit:20});
    setListing(await authRequest('/api/admin/users?'+params));setQuery(q);
  }
  async function load(q=query){
    setBusy(true);setError('');
    try{await refresh(q);}catch(e){setError(e.message);}finally{setBusy(false);}
  }
  useEffect(()=>{load();},[]);
  async function inspect(id,ticketId=''){
    setBusy(true);setError('');setOverview(null);setAction('');setTemporary(null);setNotice('');setDetail(null);setAdminPassword('');setReason('');setIdentityConfirmed(false);setRecoveryTicket('');setRecoveryRequests(null);
    try{setDetail(await authRequest('/api/admin/users/'+id));if(ticketId){setRecoveryRequests(await authRequest('/api/admin/users/'+id+'/recovery-requests'));setRecoveryTicket(ticketId);setIdentityConfirmed(false);setAction('reset');}}catch(e){setError(e.message);}finally{setBusy(false);}
  }
  async function viewWarehouse(id){
    setBusy(true);setError('');
    try{setOverview(await authRequest(`/api/admin/warehouses/${id}/overview`));}catch(e){setError(e.message);}finally{setBusy(false);}
  }
  useEffect(()=>{if(recoveryTarget)inspect(recoveryTarget.user_id,recoveryTarget.ticket_id);},[recoveryTarget?.key]);
  async function choose(value){setAction(value);setReason('');setAdminPassword('');setTemporary(null);setNotice('');setError('');setIdentityConfirmed(false);setRecoveryTicket('');setRecoveryRequests(null);
    if(value==='reset'){setBusy(true);try{setRecoveryRequests(await authRequest('/api/admin/users/'+detail.user_id+'/recovery-requests'));}catch(e){setError(e.message);}finally{setBusy(false);}}}
  async function submit(event){
    event.preventDefault();if(busy||!detail)return;
    setBusy(true);setError('');setTemporary(null);
    try{
      const result=await authRequest(`/api/admin/users/${detail.user_id}/actions`,{
        action,expected_version:detail.account_version,reason:reason.trim(),...(action==='reset'?{admin_password:adminPassword,recovery_ticket_id:recoveryTicket,confirm_identity:identityConfirmed}:{})
      });
      setAdminPassword('');setAction('');setNotice(result.message);
      if(result.temporary_password)setTemporary(result);
      // Keep the one-time password visible even if a later refresh fails.
      try{setDetail(await authRequest('/api/admin/users/'+detail.user_id));await refresh();}
      catch(e){setError('Thao tác đã hoàn tất. Chưa tải lại được danh sách: '+e.message);}
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  if(warehouseId&&view==='warehouses')return <AdminWarehousePanel key={warehouseId} warehouseId={warehouseId} onClose={()=>{setWarehouseId('');onAccounts?.();}}/>;
  return <section className="account-admin">
    <div className="account-admin-heading"><div><p className="eyebrow">{view==='warehouses'?'TRA CỨU CHỈ ĐỌC':'QUẢN LÝ TRUY CẬP'}</p><h1>{view==='warehouses'?'Dữ liệu kho người dùng':'Quản trị tài khoản'}</h1><p>{listing?`${listing.total} tài khoản phù hợp`:'Đang tải…'}</p></div>
      <button disabled={busy} onClick={()=>load()}>Làm mới</button></div>
    <form className="admin-filters" onSubmit={e=>{e.preventDefault();load({search:search.trim(),status,offset:0});}}>
      <label>Tìm tài khoản hoặc kho<input value={search} disabled={busy} maxLength={100} onChange={e=>setSearch(e.target.value)} placeholder="Tên đăng nhập, tên hiển thị, kho" /></label>
      <label>Trạng thái<select value={status} disabled={busy} onChange={e=>setStatus(e.target.value)}><option value="all">Tất cả</option><option value="active">Hoạt động</option><option value="locked">Đã khóa</option></select></label>
      <button disabled={busy} type="submit">Tìm</button></form>
    {error&&<p className="account-message error" role="alert">{error}</p>}
    {notice&&<p className="account-message" role="status">{notice}</p>}
    <div className="account-table"><table><thead><tr><th>Tài khoản</th><th>Kho</th><th>Quyền</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody>
      {listing?.users.map(u=><tr key={u.user_id}><td><strong>{u.username}</strong><br/>{u.display_name}</td>
        <td>{u.legacy_warehouse?'Dữ liệu kho cũ · chỉ xem':u.warehouse_name||'Không sử dụng kho'}</td>
        <td>{u.role==='admin'?'Admin':'User'}</td><td>{u.is_active?'Hoạt động':'Đã khóa'}{u.must_change_password&&<small className="admin-small">Cần đổi mật khẩu</small>}</td>
        <td><div className="admin-row-actions">{view==='accounts'&&<button disabled={busy} onClick={()=>inspect(u.user_id)}>Chi tiết</button>}
          {u.warehouse_id&&<><button disabled={busy} onClick={()=>viewWarehouse(u.warehouse_id)}>Xem tổng quan</button>
            <button disabled={busy} onClick={()=>{setTemporary(null);setAdminPassword('');setAction('');setWarehouseId(u.warehouse_id);onWarehouseOpen?.();}}>Xem dữ liệu</button></>}</div></td></tr>)}
      {listing?.users.length===0&&<tr><td colSpan={5}>Không có tài khoản phù hợp.</td></tr>}
    </tbody></table></div>
    <div className="admin-pagination"><button disabled={busy||!query.offset} onClick={()=>load({...query,offset:Math.max(0,query.offset-20)})}>Trước</button>
      <span>Trang {Math.floor(query.offset/20)+1}</span><button disabled={busy||!listing||query.offset+20>=listing.total} onClick={()=>load({...query,offset:query.offset+20})}>Sau</button></div>
    {overview&&<div className="account-overview" role="status"><strong>{overview.warehouse.name}</strong>
      <span>{overview.product_count} loại hàng · {overview.day_count} ngày · {overview.session_count} phiên · {overview.box_count} kiện đã ghi</span>
      <button onClick={()=>setOverview(null)}>Đóng tổng quan</button></div>}
    {view==='accounts'&&detail&&<section className="admin-detail" aria-label="Chi tiết tài khoản">
      <div className="account-admin-heading"><h2>{detail.username}</h2><button disabled={busy} onClick={()=>{setDetail(null);setTemporary(null);setAction('');setNotice('');setAdminPassword('');}}>Đóng chi tiết</button></div>
      <dl className="admin-meta"><div><dt>Tên hiển thị</dt><dd>{detail.display_name}</dd></div><div><dt>Kho</dt><dd>{detail.warehouse_name||'Không sử dụng kho'}</dd></div>
        <div><dt>Trạng thái</dt><dd>{detail.is_active?'Hoạt động':'Đã khóa'}</dd></div><div><dt>Phiên đăng nhập còn hiệu lực</dt><dd>{detail.active_sessions}</dd></div>
        <div><dt>Email khôi phục</dt><dd>{detail.email||'Chưa bổ sung email'}</dd></div>
        <div><dt>Ngày tạo</dt><dd>{dateText(detail.created_at)}</dd></div><div><dt>Mật khẩu</dt><dd>{detail.must_change_password?'Cần đổi mật khẩu tạm':'Đã thiết lập'}</dd></div></dl>
      {detail.role==='user'&&detail.warehouse_id&&<AdminStorage key={detail.warehouse_id} warehouseId={detail.warehouse_id}/>}
      {detail.role==='user'?<><div className="admin-row-actions">
        <button disabled={busy} onClick={()=>choose(detail.is_active?'lock':'unlock')}>{detail.is_active?'Khóa tài khoản':'Mở khóa'}</button>
        <button disabled={busy} onClick={()=>choose('revoke')}>Thu hồi đăng nhập</button>
        <button disabled={busy||!detail.is_active} onClick={()=>choose('reset')}>Cấp mật khẩu tạm</button>
        <button disabled={busy} onClick={()=>inspect(detail.user_id)}>Tải lại chi tiết</button></div>
        {action&&<form className="admin-action" onSubmit={submit}><h3>{labels[action]}</h3>
          <p>{action==='unlock'?'User có thể đăng nhập lại sau khi mở khóa.':'Các phiên đăng nhập sẽ bị thu hồi và camera của user sẽ ngắt. Thao tác đang xử lý cần hoàn tất trước.'}</p>
          <label>Lý do<input required minLength={3} maxLength={240} disabled={busy} value={reason} onChange={e=>setReason(e.target.value)} /></label>
          {action==='reset'&&<><div className="recovery-check"><strong>Đối chiếu email trước khi cấp mật khẩu</strong><p>Email tài khoản: {detail.email||'Chưa có email'}</p>
           <label className="recovery-source">Yêu cầu khôi phục khớp email<select aria-label="Yêu cầu khôi phục khớp email" value={recoveryTicket} required disabled={busy} onChange={e=>{setRecoveryTicket(e.target.value);setIdentityConfirmed(false);}}><option value="">Chọn yêu cầu đã nhận</option>{recoveryRequests?.requests.map(t=><option key={t.ticket_id} value={t.ticket_id}>{dateText(t.created_at)} · {t.subject}</option>)}</select></label>
           {recoveryRequests?.requests.length===0&&<p role="status">Chưa có yêu cầu hợp lệ. User cần gửi tên đăng nhập và email khớp email tài khoản tại Quên mật khẩu.</p>}
           <label className="identity-confirm"><input type="checkbox" checked={identityConfirmed} onChange={e=>setIdentityConfirmed(e.target.checked)} disabled={busy||!recoveryTicket} required/>Đã xác minh người yêu cầu là chủ email qua kênh liên hệ độc lập.</label>
           <p className="form-note">Email trùng chưa đủ chứng minh quyền sở hữu. Chỉ giao mật khẩu tạm qua kênh đã xác minh; không gửi vào hội thoại công khai.</p></div>
          <label>Mật khẩu admin để xác nhận<input name="admin-step-up-password" type="password" autoComplete="current-password" required disabled={busy} value={adminPassword} onChange={e=>setAdminPassword(e.target.value)} /></label></>}
          <div className="admin-row-actions"><button disabled={busy||action==='reset'&&(!recoveryTicket||!identityConfirmed||!recoveryRequests?.requests.some(t=>t.ticket_id===recoveryTicket))} type="submit">{busy?'Đang xử lý…':'Xác nhận'}</button><button disabled={busy} type="button" onClick={()=>{setAction('');setAdminPassword('');}}>Hủy</button></div>
        </form>}
      </>:<p className="account-note">Tài khoản quản trị không được khóa hoặc đặt lại mật khẩu tại đây.</p>}
      {temporary&&<div className="admin-temporary" role="status"><strong>Mật khẩu tạm của {detail.username}</strong>
        <label>Mật khẩu tạm<input value={temporary.temporary_password} readOnly autoComplete="off" spellCheck={false} /></label>
        <p>Hết hạn: {dateText(temporary.temporary_password_expires_at)}. Giao mật khẩu cho đúng người dùng; chỉ hiển thị trong lần thao tác này.</p>
        <button onClick={()=>setTemporary(null)}>Đã ghi lại · Ẩn mật khẩu</button></div>}
      <details className="admin-history"><summary>Hoạt động gần đây của tài khoản</summary><ul>{detail.recent_actions.map((a,i)=><li key={i}>{dateText(a.created_at)} · {a.action}</li>)}</ul></details>
    </section>}
    <p className="account-note">Quản lý tài khoản và quyền truy cập. Thao tác quản trị quan trọng được lưu nhật ký.</p>
  </section>;
}
