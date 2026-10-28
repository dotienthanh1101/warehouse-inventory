import {apiFetch} from './authClient';

async function request(path,body,signal){
  const controller=new AbortController(),abort=()=>controller.abort();
  if(signal?.aborted)controller.abort();else signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,15000);
  try{
    const response=await apiFetch(path,{method:body===undefined?'GET':'POST',cache:'no-store',signal:controller.signal,
      headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Chưa đọc được thông tin tác vụ ảnh.');
    return data;
  }catch(error){
    if((error.name==='AbortError'&&!signal?.aborted)||error instanceof TypeError)
      throw new Error('Chưa nhận được phản hồi. Tác vụ có thể vẫn đang chạy; hãy theo dõi lại tiến độ.');
    throw error;
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
export function requestId(){return crypto.randomUUID().replaceAll('-','');}
export const latestImageTask=signal=>request('/api/tasks/latest',undefined,signal);
export const readImageTask=(id,signal)=>request('/api/tasks/'+id,undefined,signal);
export const submitImageTask=(payload,signal)=>request('/api/tasks',payload,signal);
function pause(signal){return new Promise((resolve,reject)=>{
  const abort=()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));};
  const timer=setTimeout(()=>{signal?.removeEventListener('abort',abort);resolve();},1000);
  if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
});}
export async function watchImageTask(id,onUpdate,signal){
  while(!signal?.aborted){
    const task=await request('/api/tasks/'+id,undefined,signal);onUpdate(task);
    if(task.state==='failed')throw new Error(task.error_message||'Xử lý ảnh thất bại.');
    if(task.state==='completed')return await request('/api/tasks/'+id+'/result',undefined,signal);
    await pause(signal);
  }
  throw new DOMException('Aborted','AbortError');
}
