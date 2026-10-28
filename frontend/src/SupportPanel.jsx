import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import SupportMessages from './SupportMessages';
import {supportRequest,nonce,ticketState,categoryName,supportTime} from './supportClient';

export default function SupportPanel({admin=false,active:visible=true,onRecovery}){
 const base=admin?'/api/admin/support/tickets':'/api/support/tickets';
 const [listing,setListing]=useState(null),[selected,setSelected]=useState(null),[compose,setCompose]=useState(false);
 const [filter,setFilter]=useState('all'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[reply,setReply]=useState('');
 const active=useRef(null),request=useRef(null),replyRequest=useRef(null),busyRef=useRef(false);
 async function run(work){
  if(busyRef.current)return;
  active.current?.abort();const controller=new AbortController();active.current=controller;
  busyRef.current=true;setBusy(true);setError('');
  try{await work(controller.signal);}catch(e){if(!controller.signal.aborted)setError(e.message);}
  finally{if(active.current===controller){busyRef.current=false;if(!controller.signal.aborted)setBusy(false);}}
 }
 async function list(signal,state=filter,offset=0){
  const data=await supportRequest(base+'?'+new URLSearchParams({state,offset}),undefined,{signal});setListing(data);
 }
 useEffect(()=>{run(signal=>list(signal));return()=>{active.current?.abort();busyRef.current=false;};},[admin]);
 const wasVisible=useRef(visible);
 useEffect(()=>{
  if(visible&&!wasVisible.current)run(async signal=>{
   await list(signal,filter,listing?.offset||0);
   if(selected)setSelected(await supportRequest(base+'/'+selected.ticket.ticket_id,undefined,{signal}));
  });
  wasVisible.current=visible;
 },[visible]);
 useEffect(()=>{
  if(!visible||!selected?.ticket.ticket_id||selected.offset>0)return;
  const controller=new AbortController(),id=selected.ticket.ticket_id;
  const timer=setInterval(async()=>{
   if(busyRef.current||document.visibilityState!=='visible')return;
   try{const result=await supportRequest(base+'/'+id,undefined,{signal:controller.signal});if(!controller.signal.aborted)setSelected(old=>old?.ticket.ticket_id===id?result:old);}
   catch(e){if(!controller.signal.aborted)setError(e.message);}
  },10000);
  return()=>{clearInterval(timer);controller.abort();};
 },[selected?.ticket.ticket_id,selected?.offset,admin,visible]);
 function open(id){setReply('');setCompose(false);run(async signal=>setSelected(await supportRequest(base+'/'+id,undefined,{signal})));}
 function create(event){
  event.preventDefault();const f=new FormData(event.currentTarget),values={subject:String(f.get('subject')).trim(),category:String(f.get('category')),body:String(f.get('body')).trim()};
  const signature=JSON.stringify(values);if(request.current?.signature!==signature)request.current={signature,id:nonce()};
  run(async signal=>{
   const result=await supportRequest(base,{...values,request_id:request.current.id},{signal});
   const thread=await supportRequest(base+'/'+result.ticket.ticket_id,undefined,{signal});
   setSelected(thread);setCompose(false);request.current=null;await list(signal);
  });
 }
 function send(event){
  event.preventDefault();if(!selected||!reply.trim())return;
  const signature=selected.ticket.ticket_id+reply.trim();
  if(replyRequest.current?.signature!==signature)replyRequest.current={signature,id:nonce()};
  run(async signal=>{
   await supportRequest(base+'/'+selected.ticket.ticket_id+'/messages',{body:reply.trim(),request_id:replyRequest.current.id},{signal});
   setSelected(await supportRequest(base+'/'+selected.ticket.ticket_id,undefined,{signal}));setReply('');replyRequest.current=null;await list(signal);
  });
 }
 function changeState(state){
  run(async signal=>{await supportRequest(base+'/'+selected.ticket.ticket_id,{state},{method:'PATCH',signal});setSelected(await supportRequest(base+'/'+selected.ticket.ticket_id,undefined,{signal}));await list(signal);});
 }
 return <section className="portal-page support-page">
  <div className="page-heading"><div><p className="eyebrow">{admin?'KẾT NỐI NGƯỜI DÙNG':'LUÔN CÓ SỰ HỖ TRỢ'}</p><h1>{admin?'Hộp thư hỗ trợ':'Liên hệ admin'}</h1><p>{admin?'Theo dõi và phản hồi các vấn đề của người dùng.':'Gửi vấn đề bạn gặp. Admin sẽ trả lời trong hội thoại này.'}</p></div>
   {!admin&&<button className="primary-button" disabled={busy} onClick={()=>{setCompose(true);setSelected(null);setError('');}}><Icon name="help"/> Tạo yêu cầu</button>}
  </div>
  {error&&<p role="alert" className="portal-alert error">{error}</p>}
  <div className="support-layout">
   <aside className="surface support-list">
    <div className="support-list-tools"><label className="sr-only" htmlFor="support-filter">Lọc yêu cầu</label><select id="support-filter" value={filter} disabled={busy} onChange={e=>{const state=e.target.value;setFilter(state);run(signal=>list(signal,state));}}>
      <option value="all">Tất cả yêu cầu</option>{Object.entries(ticketState).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
     <button className="quiet-button" disabled={busy} onClick={()=>run(signal=>list(signal,filter,listing?.offset||0))}>Làm mới</button></div>
    <div className="support-ticket-list">{listing?.tickets.map(t=><button disabled={busy} key={t.ticket_id} className={'support-ticket '+(selected?.ticket.ticket_id===t.ticket_id?'selected':'')} onClick={()=>open(t.ticket_id)}>
      <span className={'status-chip '+t.state}>{ticketState[t.state]}</span><strong>{t.subject}</strong><span>{admin?(t.requester_type==='guest'?'Khách · '+t.username:t.username):categoryName[t.category]}</span><time>{supportTime(t.updated_at)}</time>
     </button>)}
     {listing?.tickets.length===0&&<div className="empty-card"><Icon name="mail" size={34}/><h3>Chưa có yêu cầu</h3><p>{admin?'Các yêu cầu mới sẽ xuất hiện ở đây.':'Bạn có thể tạo yêu cầu khi cần hỗ trợ.'}</p></div>}
     {!listing&&<p className="muted">Đang tải yêu cầu…</p>}
    </div>
    {listing&&<div className="support-pagination"><button disabled={busy||listing.offset===0} onClick={()=>run(signal=>list(signal,filter,Math.max(0,listing.offset-20)))}>Trước</button><span>{listing.total} yêu cầu</span><button disabled={busy||listing.offset+20>=listing.total} onClick={()=>run(signal=>list(signal,filter,listing.offset+20))}>Sau</button></div>}
   </aside>
   <div className="surface support-conversation">
    {compose&&!admin?<><h2>Yêu cầu hỗ trợ mới</h2><form className="portal-form" onSubmit={create}>
      <label>Tiêu đề<input name="subject" required minLength={3} maxLength={120} placeholder="Ví dụ: Camera chưa kết nối được" disabled={busy}/></label>
      <label>Nhóm vấn đề<select name="category" disabled={busy}>{Object.entries(categoryName).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      <label>Mô tả<textarea name="body" required maxLength={4000} rows={6} disabled={busy} placeholder="Bạn đang thao tác ở đâu, hệ thống báo gì và kết quả bạn mong đợi?"/></label>
      <p className="form-note">Không gửi mật khẩu hoặc mã đăng nhập trong nội dung hỗ trợ.</p>
      <div className="button-row"><button className="primary-button" disabled={busy}>Gửi yêu cầu</button><button type="button" className="secondary-button" disabled={busy} onClick={()=>setCompose(false)}>Hủy</button></div>
     </form></>:selected?<>
      <div className="conversation-heading"><div><p className="eyebrow">{categoryName[selected.ticket.category]}</p><h2>{selected.ticket.subject}</h2><span className={'status-chip '+selected.ticket.state}>{ticketState[selected.ticket.state]}</span></div>
       <button className="quiet-button" disabled={busy} onClick={()=>run(async signal=>setSelected(await supportRequest(base+'/'+selected.ticket.ticket_id,undefined,{signal})))}>Làm mới hội thoại</button></div>
      {admin&&selected.ticket.requester_type==='guest'&&<div className="recovery-check"><strong>Đối chiếu yêu cầu khôi phục</strong>
       <dl><div><dt>Tên đăng nhập</dt><dd>{selected.ticket.username}</dd></div><div><dt>Email gửi yêu cầu</dt><dd>{selected.ticket.email||'Chưa cung cấp'}</dd></div><div><dt>Email tài khoản</dt><dd>{selected.ticket.recovery?.registered_email||'Chưa có email đối chiếu'}</dd></div>{!selected.ticket.email&&selected.ticket.contact&&<div><dt>Liên hệ trong yêu cầu cũ</dt><dd>{selected.ticket.contact}</dd></div>}</dl>
       <p role="status" className={'status-chip '+(selected.ticket.recovery?.can_reset?'resolved':'open')}>{({matched:'Email khớp · chờ xác minh chủ email',account_not_found:'Không tìm thấy tài khoản',account_email_missing:'Tài khoản chưa có email',request_email_missing:'Yêu cầu cũ chưa có email',email_mismatch:'Email không khớp · không được cấp mật khẩu',email_changed:'Email đã thay đổi · cần yêu cầu mới',already_used:'Yêu cầu đã được dùng để cấp mật khẩu',resolved:'Yêu cầu đã giải quyết',account_locked:'Cần mở khóa tài khoản trước',not_recovery_request:'Không phải yêu cầu khôi phục'})[selected.ticket.recovery?.status]||'Chưa có kết quả đối chiếu'}</p>
       {selected.ticket.recovery?.can_reset&&<div className="button-row"><button disabled={busy} className="primary-button" onClick={()=>onRecovery?.({user_id:selected.ticket.recovery.user_id,ticket_id:selected.ticket.ticket_id})}>Xác minh và cấp mật khẩu tạm</button></div>}
       <p className="form-note">Xác minh người yêu cầu sở hữu email trước khi cấp mật khẩu. Không gửi mật khẩu qua hội thoại chưa xác minh.</p>
      </div>}
      {selected.total>50&&<div className="button-row"><button className="quiet-button" disabled={busy||selected.offset+selected.messages.length>=selected.total} onClick={()=>run(async signal=>setSelected(await supportRequest(base+'/'+selected.ticket.ticket_id+'?offset='+(selected.offset+50),undefined,{signal})))}>Tin cũ hơn</button><button className="quiet-button" disabled={busy||selected.offset===0} onClick={()=>run(async signal=>setSelected(await supportRequest(base+'/'+selected.ticket.ticket_id+'?offset='+Math.max(0,selected.offset-50),undefined,{signal})))}>Tin mới hơn</button></div>}
      <SupportMessages messages={selected.messages} admin={admin}/>
      {admin&&<div className="support-state-actions"><span>Trạng thái:</span>{Object.entries(ticketState).map(([k,v])=><button className="quiet-button" key={k} disabled={busy||selected.ticket.state===k} onClick={()=>changeState(k)}>{v}</button>)}</div>}
      <form className="reply-form" onSubmit={send}><label className="sr-only" htmlFor="support-reply">Nội dung trả lời</label><textarea id="support-reply" value={reply} onChange={e=>setReply(e.target.value)} required maxLength={4000} rows={3} disabled={busy} placeholder={admin?'Trả lời người dùng…':'Viết thêm thông tin hoặc trả lời admin…'}/><button className="primary-button" disabled={busy||!reply.trim()}><Icon name="arrow"/> Gửi trả lời</button></form>
     </>:<div className="empty-card conversation-empty"><span className="large-icon"><Icon name="help" size={40}/></span><h2>{admin?'Sẵn sàng hỗ trợ':'Chúng mình ở đây để giúp bạn'}</h2><p>{admin?'Chọn một yêu cầu để đọc và phản hồi.':'Chọn yêu cầu đã gửi hoặc tạo yêu cầu mới.'}</p></div>}
   </div>
  </div>
 </section>;
}

