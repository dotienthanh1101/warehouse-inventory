import {useEffect, useRef, useState} from 'react';
import {apiFetch} from './authClient';
import './adminWarehouse.css';

const when=value=>value?new Date(value).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'}):'—';
const state=value=>value==='closed'?'Đã đóng':'Đang mở';

async function read(path,signal){
  const response=await apiFetch(path,{cache:'no-store',signal});
  let data;
  try{data=await response.json();}catch{throw new Error('Chưa đọc được phản hồi. Hãy thử lại.');}
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Chưa đọc được dữ liệu kho.');
  return data;
}

function Pages({page,label,onPage,busy,total}){
  if(!page)return null;
  const count=total??page.total;
  return <div className="admin-pagination" aria-label={`Phân trang ${label}`}>
    <span>{count} mục · Trang {Math.floor(page.offset/page.limit)+1}</span>
    <button disabled={busy||page.offset===0} aria-label={`${label}: trang trước`} onClick={()=>onPage(Math.max(0,page.offset-page.limit))}>Trước</button>
    <button disabled={busy||page.offset+page.limit>=count} aria-label={`${label}: trang sau`} onClick={()=>onPage(page.offset+page.limit)}>Sau</button>
  </div>;
}

function Stock({items,label}){
  return <div className="account-table"><table aria-label={label}><thead><tr><th>ID</th><th>Mã sản phẩm</th><th>Loại hàng</th><th>Số kiện</th></tr></thead>
    <tbody>{items.map((p,i)=><tr key={`${p.marker_id}-${i}`}><td>{String(p.marker_id).padStart(2,'0')}</td><td>{p.product_code}</td><td>{p.product_name}</td><td>{p.quantity}</td></tr>)}
    {!items.length&&<tr><td colSpan={4}>Chưa có dòng số lượng.</td></tr>}</tbody></table></div>;
}

export default function AdminWarehousePanel({warehouseId,onClose}){
  const base=`/api/admin/warehouses/${warehouseId}`;
  const [info,setInfo]=useState(null),[tab,setTab]=useState('days');
  const [days,setDays]=useState(null),[sessions,setSessions]=useState(null),[day,setDay]=useState(null);
  const [session,setSession]=useState(null),[receipts,setReceipts]=useState(null),[receipt,setReceipt]=useState(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[imageError,setImageError]=useState(false);
  const active=useRef(null);
  async function run(work){
    active.current?.abort();
    const controller=new AbortController();active.current=controller;
    const timeout=setTimeout(()=>controller.abort('timeout'),15000);
    setBusy(true);setError('');
    try{await work(controller.signal);}
    catch(e){if(active.current===controller&&(!controller.signal.aborted||controller.signal.reason==='timeout'))setError(controller.signal.aborted?'Phản hồi quá chậm. Hãy thử lại.':e.message);}
    finally{clearTimeout(timeout);if(active.current===controller)setBusy(false);}
  }
  useEffect(()=>{
    run(async signal=>{const [summary,listing]=await Promise.all([read(base+'/records',signal),read(base+'/days',signal)]);setInfo(summary);setDays(listing);});
    return ()=>active.current?.abort();
  },[warehouseId]);
  function dayPage(offset=0){run(async signal=>setDays(await read(base+'/days?offset='+offset,signal)));}
  function sessionPage(offset=0,dayId=''){run(async signal=>setSessions(await read(base+'/sessions?'+new URLSearchParams({offset,...(dayId?{day_id:dayId}:{})}),signal)));}
  function openDay(id){
    setDay(null);setSession(null);setReceipts(null);setReceipt(null);
    run(async signal=>{const [detail,listing]=await Promise.all([read(base+'/days/'+id,signal),read(base+'/sessions?day_id='+id,signal)]);setDay(detail);setSessions(listing);});
  }
  function openSession(id){
    setSession(null);setReceipts(null);setReceipt(null);
    run(async signal=>{const [detail,listing]=await Promise.all([read(base+'/sessions/'+id,signal),read(base+'/sessions/'+id+'/receipts',signal)]);setSession(detail);setReceipts(listing);});
  }
  function receiptPage(offset){run(async signal=>setReceipts(await read(base+'/sessions/'+session.session.session_id+'/receipts?offset='+offset,signal)));}
  function openReceipt(id,offset=0){
    setImageError(false);setReceipt(null);
    run(async signal=>setReceipt(await read(base+'/receipts/'+id+'?offset='+offset,signal)));
  }
  function switchTab(value){
    setTab(value);setDay(null);setSession(null);setReceipts(null);setReceipt(null);setError('');
    if(value==='sessions')sessionPage();else dayPage();
  }
  const sessionList=<>
    <div className="account-table"><table aria-label="Danh sách phiên của kho"><thead><tr><th>Phiên</th><th>Ngày</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody>
      {sessions?.sessions.map(s=><tr key={s.session_id}><td>{s.name}</td><td>{s.business_date||'Chưa liên kết ngày'}</td><td>{state(s.status)}</td><td><button disabled={busy} onClick={()=>openSession(s.session_id)}>Xem phiên</button></td></tr>)}
      {sessions?.sessions.length===0&&<tr><td colSpan={4}>Chưa có phiên.</td></tr>}</tbody></table></div>
    <Pages page={sessions} label="Phiên" busy={busy} onPage={offset=>sessionPage(offset,day?.day.day_id)}/>
  </>;
  return <section className="account-admin admin-warehouse" aria-label="Dữ liệu kho người dùng">
    <header className="account-admin-heading"><div><p className="account-kicker">TRA CỨU KHO · CHỈ XEM</p><h1>{info?.warehouse.name||'Dữ liệu kho'}</h1>
      {info&&<p>Chủ kho: <strong>{info.warehouse.username}</strong> · {info.warehouse.is_active?'Tài khoản hoạt động':'Tài khoản đã khóa'}</p>}</div>
      <button onClick={onClose}>Quay lại tài khoản</button></header>
    <p className="account-note">Tra cứu toàn bộ lịch sử, không phải số tồn hiện tại. Các lượt truy cập được lưu nhật ký.</p>
    {info&&<div className="admin-warehouse-metrics">{[['Loại hàng',info.summary.product_count],['Ngày',info.summary.day_count],['Phiên',info.summary.session_count],['Biên nhận',info.summary.receipt_count],['Kiện ghi mới',info.summary.box_count]].map(([label,value])=><div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>}
    <nav className="admin-warehouse-tabs" role="tablist" aria-label="Tra cứu kho">
      <button role="tab" aria-selected={tab==='days'} disabled={busy} onClick={()=>switchTab('days')}>Ngày kiểm kê</button>
      <button role="tab" aria-selected={tab==='sessions'} disabled={busy} onClick={()=>switchTab('sessions')}>Phiên & ảnh</button>
    </nav>
    {error&&<p className="account-message error" role="alert">{error}</p>}
    {busy&&<p role="status">Đang tải dữ liệu kho…</p>}
    {!info&&!busy&&error&&<button onClick={()=>run(async signal=>{const [summary,listing]=await Promise.all([read(base+'/records',signal),read(base+'/days',signal)]);setInfo(summary);setDays(listing);})}>Thử tải lại</button>}
    {tab==='days'&&!session&&!receipt&&<section aria-label="Ngày kiểm kê của kho">
      {!day&&<><div className="account-table"><table><thead><tr><th>Ngày</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody>
        {days?.days.map(d=><tr key={d.day_id}><td>{d.business_date}</td><td>{state(d.status)}</td><td><button disabled={busy} onClick={()=>openDay(d.day_id)}>Xem ngày</button></td></tr>)}
        {days?.days.length===0&&<tr><td colSpan={3}>Kho chưa có ngày kiểm kê.</td></tr>}</tbody></table></div>
      <Pages page={days} label="Ngày" busy={busy} onPage={dayPage}/></>}
      {day&&<section className="admin-warehouse-section"><div className="account-admin-heading"><h2>Ngày {day.day.business_date} · {state(day.day.status)}</h2>
        <button disabled={busy} onClick={()=>setDay(null)}>Về danh sách ngày</button></div>
        {day.report?<><p>{day.report.status==='closed'?'Báo cáo đã chốt':'Xem trước từ các phiên đã chốt'} · {day.report.note}</p>
          <div className="account-table"><table aria-label="Báo cáo ngày của kho"><thead><tr><th>Loại hàng</th><th>Tồn đầu</th><th>Tăng</th><th>Giảm</th><th>Tồn cuối</th></tr></thead><tbody>
            {day.report.items.map(p=><tr key={p.marker_id}><td>{p.product_name}<small className="admin-small">{p.product_code}</small></td><td>{p.opening_quantity}</td><td>{p.total_increase}</td><td>{p.total_decrease}</td><td>{p.closing_quantity}</td></tr>)}</tbody></table></div>
          <p>Tổng: đầu {day.report.opening_total_boxes} · tăng {day.report.total_increase} · giảm {day.report.total_decrease} · cuối {day.report.closing_total_boxes} kiện.</p>
        </>:<><p className="account-note">{day.report_message}</p><Stock label="Tồn đầu ngày" items={day.opening_items}/></>}
        <h3>Các phiên trong ngày</h3>{sessionList}
      </section>}
    </section>}
    {tab==='sessions'&&!session&&!receipt&&sessionList}
    {session&&!receipt&&<section className="admin-warehouse-section" aria-label="Chi tiết phiên kiểm kê"><div className="account-admin-heading"><h2>{session.session.name}</h2>
      <button disabled={busy} onClick={()=>{setSession(null);setReceipts(null);}}>{day?'Về ngày đang xem':'Về danh sách phiên'}</button></div>
      <p>{session.session.business_date||'Phiên chưa liên kết ngày'} · {state(session.session.status)} · {session.receipt_count} biên nhận · {session.recorded_total_boxes} kiện ghi mới.</p>
      <p className="account-note">{session.note}</p>
      {session.legacy_row_count>0&&<p className="account-note">{session.legacy_row_count} dòng theo cơ chế cũ, hiển thị riêng.</p>}
      {session.stock_message?<p className="account-message error" role="alert">{session.stock_message}</p>
        :<Stock items={session.finalized?session.stock_items:session.recorded_items} label={session.finalized?'Tồn phiên đã chốt':'Số đã ghi trong phiên'}/>}
      <h3>Ảnh đã ghi vào phiên</h3>
      <div className="account-table"><table aria-label="Biên nhận của phiên"><thead><tr><th>Ghi nhận lúc</th><th>Phiên bản</th><th>Kiện mới</th><th>Thao tác</th></tr></thead><tbody>
        {receipts?.receipts.map(r=><tr key={r.review_id}><td>{when(r.recorded_at)}</td><td>{r.review_revision}</td><td>{r.box_count}</td><td><button disabled={busy} onClick={()=>openReceipt(r.review_id)}>Xem ảnh & kiện</button></td></tr>)}
        {receipts?.receipts.length===0&&<tr><td colSpan={4}>Chưa có ảnh ghi nhận theo cơ chế mới.</td></tr>}</tbody></table></div>
      <Pages page={receipts} label="Biên nhận" busy={busy} onPage={receiptPage}/>
    </section>}
    {receipt&&<section className="admin-warehouse-section" aria-label="Ảnh và dữ liệu đã ghi">
      <div className="account-admin-heading"><h2>Ảnh đã ghi nhận</h2><button disabled={busy} onClick={()=>setReceipt(null)}>Đóng ảnh</button></div>
      <p>{when(receipt.receipt.recorded_at)} · Phiên bản {receipt.receipt.review_revision} · {receipt.receipt.box_count} kiện mới · {receipt.duplicate_count} vùng chọn trùng.</p>
      <p className="account-note admin-record-id">Bản nháp: {receipt.receipt.review_id}</p>
      {receipt.snapshot_warning&&<p className="account-message error">{receipt.snapshot_warning}</p>}
      <div className="admin-evidence-grid"><div>{imageError?<p role="alert">Ảnh bằng chứng không còn sẵn có hoặc chưa tải được. Số liệu đã ghi vẫn giữ nguyên.</p>
        :<img key={receipt.image_url} src={receipt.image_url} alt="Ảnh bằng chứng của biên nhận" onError={()=>setImageError(true)}/>}</div>
        <div><Stock items={receipt.counts} label="Số kiện mới trong ảnh"/>
          <div className="account-table"><table aria-label="Các kiện đã ghi"><thead><tr><th>Vùng</th><th>Loại hàng</th><th>Mã kiện nội bộ</th></tr></thead><tbody>
            {receipt.instances.map(b=><tr key={b.instance_id}><td>{b.source_box_id}</td><td>{b.product_name}<small className="admin-small">{b.product_code} · ID {b.marker_id}</small></td><td className="admin-record-id">{b.instance_id}</td></tr>)}</tbody></table></div>
          {receipt.decisions?.length>0&&<details><summary>Quyết định đã lưu</summary><ul>{receipt.decisions.map((d,i)=><li key={i}>{d.box_id}: {d.decision==='new'?'Ghi mới':'Trùng'}{d.reference_instance_id&&<span className="admin-record-id"> · {d.reference_instance_id}</span>}{d.note&&` · ${d.note}`}</li>)}</ul></details>}
          <Pages page={receipt} total={Math.max(receipt.total,receipt.decision_total||0)} label="Chi tiết ảnh" busy={busy} onPage={offset=>openReceipt(receipt.receipt.review_id,offset)}/>
        </div></div>
    </section>}
  </section>;
}
