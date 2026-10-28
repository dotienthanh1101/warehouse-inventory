import {useEffect,useRef} from 'react';
const labels={preparing:'Đang lấy ảnh',queued:'Đang chờ lượt',running:'Đang xử lý',completed:'Hoàn tất',failed:'Có lỗi'};
export default function ImageTaskPanel({task,busy,onFollow,onDismiss,statusError,resultShown=false}){
  const dismiss=useRef(onDismiss),viewed=useRef(null);
  useEffect(()=>{dismiss.current=onDismiss;},[onDismiss]);
  useEffect(()=>{
    if(resultShown&&task?.state==='completed')viewed.current=task.task_id;
    if(task?.state!=='completed'||busy||viewed.current!==task.task_id)return;
    const timer=setTimeout(()=>dismiss.current?.(),4000);
    return()=>clearTimeout(timer);
  },[task?.task_id,task?.state,busy,resultShown]);
  if(!task)return null;
  const active=['preparing','queued','running'].includes(task.state);
  return <section className={`image-task-panel task-${task.state}`} aria-label="Tác vụ ảnh" role="status">
    <div><strong>{active&&<span className="task-spinner" aria-hidden="true"/>}{task.kind==='capture'?'Chụp & phân tích':'Kiểm tra trùng'} · {labels[task.state]||task.state}</strong>
      {task.state==='queued'&&<p>{task.queue_position?`Vị trí hiện tại: ${task.queue_position}. `:''}Kết quả sẽ hiện khi đến lượt xử lý.</p>}
      {task.state==='running'&&<p>Đang xử lý ảnh của bạn. Có thể tải lại trang rồi tiếp tục theo dõi.</p>}
      {task.state==='failed'&&<p>{task.error_message||'Hãy tạo một lượt xử lý mới.'}</p>}
      {task.state==='completed'&&<p>Kết quả đã sẵn sàng để kiểm tra.</p>}
      {active&&statusError&&<p>Chưa cập nhật được trạng thái. Hệ thống đang thử lại; có thể bấm Theo dõi tiến độ.</p>}
    </div>
    <div style={{display:'flex',flexWrap:'wrap',gap:8,alignItems:'center'}}>
      {(active||task.state==='completed')&&<button type="button" className="secondary" disabled={busy} onClick={onFollow}>
        {active?'Theo dõi tiến độ':'Mở kết quả'}</button>}
      {['completed','failed'].includes(task.state)&&onDismiss&&<button type="button" className="quiet" disabled={busy} onClick={onDismiss} aria-label="Đóng thông báo tác vụ">Đóng</button>}
    </div>
  </section>;
}
