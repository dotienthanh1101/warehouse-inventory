import {useEffect,useRef,useState} from 'react';
import {guestSecret,nonce,supportRequest,ticketState} from './supportClient';
import SupportMessages from './SupportMessages';
import Icon from './Icon';

export default function GuestSupportPanel({onBack}){
 const [mode,setMode]=useState('create'),[code,setCode]=useState(''),[thread,setThread]=useState(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[reply,setReply]=useState(''),[copied,setCopied]=useState(false);
 const active=useRef(null),creation=useRef(null),message=useRef(null),codeField=useRef(null),locked=useRef(false);
 useEffect(()=>()=>active.current?.abort(),[]);
 async function run(work){
  if(locked.current)return;
  const controller=new AbortController();active.current=controller;locked.current=true;setBusy(true);setError('');
  try{await work(controller.signal);}catch(e){if(!controller.signal.aborted)setError(e.message);}
  finally{locked.current=false;if(!controller.signal.aborted)setBusy(false);}
 }
 function create(event){
  event.preventDefault();const f=new FormData(event.currentTarget);
  const values={username:String(f.get('username')).trim(),email:String(f.get('email')).trim(),subject:String(f.get('subject')).trim(),body:String(f.get('body')).trim(),category:'account'};
  const signature=JSON.stringify(values);
  if(creation.current?.signature!==signature)creation.current={signature,id:nonce(),secret:guestSecret()};
  run(async signal=>{
   const result=await supportRequest('/api/support/guest',{...values,request_id:creation.current.id,guest_secret:creation.current.secret},{signal});
   setCode(result.access_code);setMode('track');setThread(await supportRequest('/api/support/guest/read',{access_code:result.access_code},{signal}));creation.current=null;
  });
 }
 function lookup(event){event?.preventDefault();if(!code.trim())return;run(async signal=>setThread(await supportRequest('/api/support/guest/read',{access_code:code.trim()},{signal})));}
 function send(event){
  event.preventDefault();const signature=code+reply.trim();if(message.current?.signature!==signature)message.current={signature,id:nonce()};
  run(async signal=>{
   await supportRequest('/api/support/guest/reply',{access_code:code.trim(),request_id:message.current.id,body:reply.trim()},{signal});
   setThread(await supportRequest('/api/support/guest/read',{access_code:code.trim()},{signal}));setReply('');message.current=null;
  });
 }
 async function copy(){
  try{await navigator.clipboard.writeText(code);setCopied(true);}
  catch{codeField.current?.select();setCopied(false);}
 }
 return <section className="guest-support surface">
  <div className="page-heading"><div><p className="eyebrow">HỖ TRỢ TRUY CẬP</p><h1>Để admin giúp bạn</h1><p>Quên mật khẩu hoặc chưa đăng nhập được? Gửi yêu cầu và theo dõi phản hồi tại đây.</p></div><span className="large-icon"><Icon name="help" size={32}/></span></div>
  <div className="auth-mode-tabs"><button type="button" className={mode==='create'?'selected':''} disabled={busy} onClick={()=>{setMode('create');setThread(null);}}>Gửi yêu cầu</button><button type="button" className={mode==='track'?'selected':''} disabled={busy} onClick={()=>setMode('track')}>Theo dõi yêu cầu</button></div>
  {error&&<p role="alert" className="portal-alert error">{error}</p>}
  {mode==='create'?<form className="portal-form" onSubmit={create}>
   <div className="form-columns"><label>Tên đăng nhập<input name="username" required maxLength={64} autoComplete="username" disabled={busy}/></label><label>Email đã đăng ký<input type="email" name="email" required maxLength={254} autoComplete="email" placeholder="Nhập đúng email của tài khoản" disabled={busy}/></label></div>
   <label>Tiêu đề<input name="subject" required minLength={3} maxLength={120} defaultValue="Cần hỗ trợ truy cập tài khoản" disabled={busy}/></label>
   <label>Nội dung<textarea name="body" rows={4} required maxLength={4000} placeholder="Mô tả vấn đề truy cập bạn đang gặp…" disabled={busy}/></label>
   <p className="form-note">Email cần khớp email đã lưu của tài khoản. Admin sẽ xác minh chủ email trước khi cấp mật khẩu tạm. Không gửi mật khẩu trong nội dung.</p>
   <button className="primary-button" disabled={busy}>{busy?'Đang gửi…':'Gửi cho admin'}<Icon name="arrow"/></button>
  </form>:<>
   <form className="portal-form" onSubmit={lookup}><label htmlFor="guest-support-code">Mã hỗ trợ<textarea id="guest-support-code" aria-label="Mã hỗ trợ" ref={codeField} value={code} onChange={e=>{setCode(e.target.value);setThread(null);setCopied(false);}} required minLength={97} maxLength={97} autoComplete="off" autoCapitalize="none" autoCorrect="off" rows={2} spellCheck={false} placeholder="Dán mã hỗ trợ đã lưu" disabled={busy}/></label>
    <p className="form-note">Giữ mã này để xem phản hồi sau khi đóng trang. Mã hỗ trợ chỉ mở hội thoại, không dùng để đăng nhập.</p>
    <div className="button-row"><button className="primary-button" disabled={busy||!code.trim()}>Xem phản hồi</button>{code&&<button type="button" className="secondary-button" onClick={copy}>{copied?'Đã sao chép':'Sao chép mã'}</button>}</div>
   </form>
   {thread&&<section className="guest-thread"><div className="conversation-heading"><h2>{thread.ticket.subject}</h2><span className={'status-chip '+thread.ticket.state}>{ticketState[thread.ticket.state]}</span></div>
    <SupportMessages messages={thread.messages}/>
    <form className="reply-form" onSubmit={send}><label htmlFor="guest-reply">Bổ sung thông tin</label><textarea id="guest-reply" value={reply} onChange={e=>setReply(e.target.value)} rows={3} maxLength={4000} required disabled={busy}/><button className="primary-button" disabled={busy||!reply.trim()}>Gửi trả lời</button></form>
   </section>}
  </>}
  <button className="text-button guest-back" onClick={onBack} disabled={busy}>← Quay lại đăng nhập</button>
 </section>;
}

