import Icon from './Icon';
export function QuantityChart({items=[],closed=false}){
 const rows=items.filter(p=>p.reference_quantity>0||p.recorded_quantity>0).slice(0,6);
 const max=Math.max(1,...rows.flatMap(p=>[p.reference_quantity||0,p.recorded_quantity||0]));
 return <section className="surface dashboard-chart"><div className="card-title"><div><p className="eyebrow">SỐ LƯỢNG THEO LOẠI</p><h2>{closed?'Mốc trước và tồn đã chốt':'Mốc đối chiếu & số đã ghi'}</h2></div><span className="icon-tile"><Icon name="chart"/></span></div>
  <div className="chart-legend"><span><i className="legend-dot pale"/>Mốc đối chiếu</span><span><i className="legend-dot blue"/>{closed?'Tồn đã chốt':'Đã ghi trong phiên'}</span></div>
  {rows.length?<div className="quantity-chart">{rows.map(p=><div className="quantity-row" key={p.marker_id}><div className="quantity-label"><strong>{p.product_name}</strong><small>{p.product_code}</small></div>
   <div className="bar-pair"><div><span className="quantity-bar pale" style={{width:100*(p.reference_quantity||0)/max+'%'}}/><b>{p.reference_quantity||0}</b></div><div><span className="quantity-bar blue" style={{width:100*(p.recorded_quantity||0)/max+'%'}}/><b>{p.recorded_quantity||0}</b></div></div></div>)}</div>
   :<div className="empty-card"><Icon name="chart" size={40}/><p>Chưa có số lượng để hiển thị biểu đồ.</p></div>}
  {items.filter(p=>p.reference_quantity>0||p.recorded_quantity>0).length>6&&<p className="form-note">Hiển thị 6 loại có số lượng. Xem đầy đủ tại Phiên kiểm kê.</p>}
  <p className="form-note">{closed?'Số liệu từ phiên đã chốt.':items.length?'Phiên đang mở: số đã ghi là tạm thời, chưa kết luận tăng/giảm.':'Chọn ngày và phiên để xem số liệu đối chiếu.'}</p>
 </section>;
}
export default function WarehouseDashboard({profile,day,session,days,pending,camera,onNavigate}){
 const closed=!!session?.finalized;
 return <section className="dashboard-page">
  <div className="dashboard-hero"><div><span className="hero-tag"><span className="live-dot"/> KHÔNG GIAN KIỂM KÊ CỦA BẠN</span><h1>Xin chào, {profile?.user.display_name||'bạn'}.</h1><p>Một góc nhìn rõ ràng hơn cho kho của bạn.<br/>Thu ảnh, kiểm tra và đối soát trong cùng một nơi.</p><button className="primary-button" onClick={()=>onNavigate(session?'camera':'session')}>{session?'Tiếp tục kiểm kê':'Chọn ngày & phiên'}<Icon name="arrow"/></button></div>
   <div className="hero-illustration" aria-hidden="true"><div className="scan-corner tl"/><div className="scan-corner tr"/><div className="scan-corner bl"/><div className="scan-corner br"/><Icon name="box" size={130}/><span className="hero-float"><Icon name="check"/> Kiểm kê có kiểm soát</span></div></div>
  <div className="metric-grid">
   {[['box',closed?'Tồn phiên đã chốt':'Đã ghi trong phiên',session?.recorded_total_boxes??'—','kiện'],['list','Mốc đối chiếu',session?.reference_total_boxes??'—','kiện'],['scan','Cần kiểm tra ảnh',pending,'vùng'],['camera','Nguồn camera',camera?.ready?'Đã kết nối':'Chưa kết nối',day?.business_date||'Chưa chọn ngày']].map(([icon,label,value,note])=><article className="surface metric-card" key={label}><span className={'icon-tile '+icon}><Icon name={icon}/></span><span className="metric-label">{label}</span><strong>{value}</strong><small>{note}</small></article>)}
  </div>
  <div className="dashboard-grid"><QuantityChart items={session?.items||[]} closed={closed}/><section className="surface workflow-card"><div className="card-title"><h2>Tiếp tục công việc</h2><Icon name="list"/></div><p className="muted">{session?.name||'Chưa chọn phiên kiểm kê.'}</p>
   {[['session','01','Ngày & phiên','Chọn mốc kiểm kê và phạm vi kho'],['camera','02','Thu nhận ảnh','Kết nối camera và chụp một ảnh'],['review','03','Kiểm tra kết quả','Hiệu chỉnh và đối chiếu trước khi ghi'],['report','04','Báo cáo ngày','Xem số liệu, xác nhận và chốt ngày']].map(([tab,n,title,subtitle])=><button className="workflow-link" key={tab} onClick={()=>onNavigate(tab)}><span>{n}</span><div><strong>{title}</strong><small>{subtitle}</small></div><Icon name="arrow" size={17}/></button>)}
  </section></div>
  <section className="surface recent-days"><div className="card-title"><h2>Ngày kiểm kê gần đây</h2><button className="text-button" onClick={()=>onNavigate('report')}>Xem báo cáo <Icon name="arrow" size={17}/></button></div>
   {days.length?<div className="recent-day-grid">{days.slice(0,4).map(d=><div key={d.day_id}><span className="icon-tile"><Icon name="chart"/></span><div><strong>{d.business_date}</strong><span className={'status-chip '+(d.status==='closed'?'resolved':'open')}>{d.status==='closed'?'Đã chốt':'Đang mở'}</span></div></div>)}</div>:<p className="muted">Kho chưa có ngày kiểm kê. Bắt đầu bằng việc mở ngày và tạo phiên.</p>}
  </section>
 </section>;
}

