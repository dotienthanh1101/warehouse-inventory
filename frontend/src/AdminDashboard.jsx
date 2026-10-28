import {useEffect,useRef,useState} from 'react';
import {supportRequest} from './supportClient';
import Icon from './Icon';
import ServerStorage from './ServerStorage';
export default function AdminDashboard({profile,onNavigate}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 useEffect(()=>{const c=new AbortController();supportRequest('/api/admin/dashboard',undefined,{signal:c.signal}).then(setData).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[attempt]);
 const total=data?.user_count||0;
 return <section className="dashboard-page"><div className="dashboard-hero admin-hero"><div><span className="hero-tag">GÓC NHÌN QUẢN TRỊ</span><h1>Chào {profile.user.display_name}.</h1><p>Quản lý truy cập. Theo dõi dữ liệu.<br/>Hỗ trợ người dùng ở đúng nơi cần thiết.</p></div><div className="hero-illustration" aria-hidden="true"><Icon name="users" size={130}/></div></div>
  {error&&<p role="alert" className="portal-alert error">{error}<button className="text-button" onClick={()=>{setError('');setAttempt(a=>a+1);}}>Thử lại</button></p>}
  <div className="metric-grid">{[['users','Người dùng',data?.user_count],['box','Kho dữ liệu',data?.warehouse_count],['check','Đang hoạt động',data?.active_users],['help','Chờ hỗ trợ',data?.support.waiting]].map(([icon,label,value])=><article className="surface metric-card" key={label}><span className="icon-tile"><Icon name={icon}/></span><span className="metric-label">{label}</span><strong>{value??'—'}</strong></article>)}</div>
  <ServerStorage/>
  <div className="dashboard-grid"><section className="surface dashboard-chart"><div className="card-title"><h2>Trạng thái người dùng</h2><Icon name="chart"/></div>
   <div className="account-donut-layout"><div className="account-donut" style={{'--portion':(total?100*data.active_users/total:0)+'%'}}><div><strong>{data?.user_count??'—'}</strong><span>người dùng</span></div></div><div className="donut-legend"><p><i className="legend-dot blue"/>Hoạt động <strong>{data?.active_users??'—'}</strong></p><p><i className="legend-dot pale"/>Đã khóa <strong>{data?.locked_users??'—'}</strong></p></div></div>
  </section><section className="surface workflow-card"><div className="card-title"><h2>Tình hình hỗ trợ</h2><Icon name="help"/></div>
   <dl className="support-overview"><div><dt>Chờ phản hồi</dt><dd>{data?.support.waiting??'—'}</dd></div><div><dt>Đang xử lý</dt><dd>{data?.support.in_progress??'—'}</dd></div><div><dt>Đã giải quyết</dt><dd>{data?.support.resolved??'—'}</dd></div></dl>
   <p className="form-note">Chọn chức năng tại menu bên trái để quản lý tài khoản, xem dữ liệu kho hoặc xử lý yêu cầu hỗ trợ.</p>
  </section></div>
 </section>;
}

