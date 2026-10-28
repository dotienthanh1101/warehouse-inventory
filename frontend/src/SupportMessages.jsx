import {useEffect,useRef} from 'react';
import {supportTime} from './supportClient';
export default function SupportMessages({messages,admin=false}){
 const ref=useRef(null),bottom=useRef(true);
 useEffect(()=>{if(bottom.current&&ref.current)ref.current.scrollTop=ref.current.scrollHeight;},[messages]);
 return <div ref={ref} className="support-messages" aria-label="Nội dung trao đổi" onScroll={()=>{const n=ref.current;bottom.current=n.scrollHeight-n.scrollTop-n.clientHeight<90;}}>
  {messages.map(m=><article key={m.message_id} className={'support-message '+((admin?m.author_role==='admin':m.author_role!=='admin')?'mine':'theirs')}>
   <div className="message-meta"><strong>{m.author_role==='admin'?'Admin':m.author_role==='guest'?'Khách · chưa xác minh':admin?'Người dùng':'Bạn'}</strong><time>{supportTime(m.created_at)}</time></div>
   <p>{m.body}</p>
  </article>)}
 </div>;
}

