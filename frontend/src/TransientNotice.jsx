import {useEffect,useRef} from 'react';

// Ordinary success/info feedback only. Validation errors and task recovery stay visible.
export default function TransientNotice({message,onDismiss}){
  const dismiss=useRef(onDismiss);
  useEffect(()=>{dismiss.current=onDismiss;},[onDismiss]);
  useEffect(()=>{
    if(!message)return;
    const timer=setTimeout(()=>dismiss.current(),4000);
    return()=>clearTimeout(timer);
  },[message]);
  if(!message)return null;
  return <div className="alert success transient-notice" role="status" aria-live="polite">
    <span>{message}</span>
    <button type="button" className="quiet" aria-label="Ẩn thông báo" onClick={onDismiss}>×</button>
  </div>;
}
