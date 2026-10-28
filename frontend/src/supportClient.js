import {apiFetch} from './authClient';
export const nonce=()=>crypto.randomUUID().replaceAll('-','');
export const guestSecret=()=>Array.from(crypto.getRandomValues(new Uint8Array(32)),b=>b.toString(16).padStart(2,'0')).join('');
export async function supportRequest(path,body,options={}){
 const controller=new AbortController(),abort=()=>controller.abort();
 const signal=options.signal;
 if(signal?.aborted)controller.abort();else signal?.addEventListener('abort',abort,{once:true});
 const timeout=setTimeout(abort,15000);
 try{
  const response=await apiFetch(path,{method:options.method||(body===undefined?'GET':'POST'),cache:'no-store',signal:controller.signal,
   headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(controller.signal.aborted)throw new DOMException('Aborted','AbortError');
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Chưa thực hiện được yêu cầu hỗ trợ. Kiểm tra nội dung và thử lại.');
  return data;
 }catch(error){
  if(error.name==='AbortError'&&!signal?.aborted)throw new Error('Phản hồi quá chậm. Bạn có thể thử lại; lượt gửi cũ sẽ không được tạo hai lần.');
  if(error instanceof TypeError)throw new Error('Chưa kết nối được hệ thống hỗ trợ. Hãy thử lại.');
  throw error;
 }finally{clearTimeout(timeout);signal?.removeEventListener('abort',abort);}
}
export const ticketState={open:'Chờ phản hồi',in_progress:'Đang hỗ trợ',resolved:'Đã giải quyết'};
export const categoryName={camera:'Camera',inventory:'Kiểm kê',account:'Tài khoản',other:'Khác'};
export const supportTime=value=>new Date(value).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'});

