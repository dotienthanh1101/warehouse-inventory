import {useEffect,useRef,useState} from 'react';
import App from './App';
import AdminAccountsPanel from './AdminAccountsPanel';
import AdminDashboard from './AdminDashboard';
import PasswordPanel from './PasswordPanel';
import RecoveryEmailPanel from './RecoveryEmailPanel';
import SupportPanel from './SupportPanel';
import GuestSupportPanel from './GuestSupportPanel';
import Icon from './Icon';
import {authRequest,setAuthToken} from './authClient';
import './auth.css';
import './portal.css';
import {UserStorage} from './StoragePanel';

const userSections=[['dashboard','home','Tổng quan'],['camera','camera','Camera'],['review','scan','Kiểm tra ảnh'],['session','list','Phiên kiểm kê'],['report','chart','Báo cáo ngày'],['catalog','tag','Danh mục hàng'],['support','help','Liên hệ admin']];
const adminSections=[['dashboard','home','Tổng quan'],['accounts','users','Tài khoản'],['warehouses','box','Dữ liệu kho'],['support','help','Hộp thư hỗ trợ']];
const core=new Set(['dashboard','camera','review','session','report','catalog']);
function clearPrivateCache(){try{for(const key of Object.keys(localStorage)){if(key.startsWith('warehouse-review-editor-v1')||key.startsWith('warehouse-last-day-v1'))localStorage.removeItem(key);}}catch{}}
function Brand(){return <span className="portal-brand"><span className="brand-mark"><Icon name="box" size={28}/></span><span>Kiểm kê kho<small>Quản lý bằng hình ảnh</small></span></span>;}

export default function AuthShell(){
 const [profile,setProfile]=useState(null),[loading,setLoading]=useState(true),[mode,setMode]=useState('login');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[registrationOpen,setRegistrationOpen]=useState(false);
 const [section,setSection]=useState('dashboard'),[adminView,setAdminView]=useState('accounts'),[drawer,setDrawer]=useState(false),[showPassword,setShowPassword]=useState(false);
 const [supportVisited,setSupportVisited]=useState(false);
 const [recoveryTarget,setRecoveryTarget]=useState(null);
 const [storageStatus,setStorageStatus]=useState(null);
 useEffect(()=>{setStorageStatus(null);},[profile?.warehouse?.warehouse_id]);
 useEffect(()=>{window.scrollTo(0,0);},[section]);
 const authRestore=useRef(null);
 const menu=useRef(null),menuButton=useRef(null),drawerButton=useRef(null);
 const isAdmin=profile?.user.role==='admin',forced=!!profile?.user.must_change_password;
 const sections=isAdmin?adminSections:userSections;
 function navigate(next){if(next==='support')setSupportVisited(true);setSection(next);setDrawer(false);if(menu.current)menu.current.open=false;if(['accounts','warehouses'].includes(next))setAdminView(next);}
 async function restore(){
  authRestore.current?.abort();const controller=new AbortController();authRestore.current=controller;
  setLoading(true);setError('');
  try{const p=await authRequest('/api/auth/me',undefined,{signal:controller.signal});if(controller.signal.aborted)return;setAuthToken(p.csrf_token,p.warehouse?.warehouse_id||'');setProfile(p);}
  catch(e){if(!controller.signal.aborted&&e.status!==401)setError(e.message);}
  if(controller.signal.aborted)return;
  try{const config=await authRequest('/api/auth/config',undefined,{signal:controller.signal});if(!controller.signal.aborted)setRegistrationOpen(config.registration_open);}catch{}
  if(!controller.signal.aborted)setLoading(false);
 }
 useEffect(()=>{
  restore();
  function expired(){clearPrivateCache();setAuthToken('');setProfile(null);setSupportVisited(false);setRecoveryTarget(null);setSection('dashboard');setDrawer(false);setError('Phiên đã hết hạn hoặc đã được thu hồi. Vui lòng đăng nhập lại.');}
  async function check(){try{const p=await authRequest('/api/auth/me');setAuthToken(p.csrf_token,p.warehouse?.warehouse_id||'');setProfile(old=>old&&(old.user.user_id!==p.user.user_id||old.user.role!==p.user.role||old.user.must_change_password!==p.user.must_change_password||old.warehouse?.warehouse_id!==p.warehouse?.warehouse_id)?p:old);}catch{}}
  function visible(){if(document.visibilityState==='visible')check();}
  window.addEventListener('warehouse-auth-expired',expired);document.addEventListener('visibilitychange',visible);window.addEventListener('warehouse-auth-check',check);
  const timer=setInterval(()=>{if(document.visibilityState==='visible')check();},15000);
  return()=>{authRestore.current?.abort();window.removeEventListener('warehouse-auth-expired',expired);window.removeEventListener('warehouse-auth-check',check);document.removeEventListener('visibilitychange',visible);clearInterval(timer);};
 },[]);
 useEffect(()=>{
  function outside(e){if(menu.current&&!menu.current.contains(e.target))menu.current.open=false;}
  function escape(e){if(e.key==='Escape'){if(menu.current?.open){menu.current.open=false;menuButton.current?.focus();}if(drawer){setDrawer(false);drawerButton.current?.focus();}}}
  document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};
 },[drawer]);
 async function submit(event){
  event.preventDefault();if(busy)return;
  const f=new FormData(event.currentTarget),username=String(f.get('username')||'').trim(),password=String(f.get('password')||'');
  setError('');setNotice('');
  if(mode==='register'&&password!==f.get('confirm_password')){setError('Hai mật khẩu chưa khớp.');return;}
  setBusy(true);
  try{if(mode==='register'){
    const result=await authRequest('/api/auth/register',{username,password,email:String(f.get('email')||'').trim(),display_name:String(f.get('display_name')||'').trim(),warehouse_name:String(f.get('warehouse_name')||'').trim()});
    setMode('login');setNotice(result.message);setShowPassword(false);
   }else{const result=await authRequest('/api/auth/login',{username,password});setAuthToken(result.csrf_token,result.warehouse?.warehouse_id||'');setProfile(result);setSupportVisited(false);setRecoveryTarget(null);setSection('dashboard');setShowPassword(false);}
  }catch(e){setError(e.message);}finally{setBusy(false);}
 }
 async function logout(){
  setBusy(true);setError('');
  try{await authRequest('/api/auth/logout',{});clearPrivateCache();setAuthToken('');setProfile(null);setSupportVisited(false);setRecoveryTarget(null);setSection('dashboard');setDrawer(false);setMode('login');setNotice('Đã đăng xuất.');}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 function passwordDone(message){clearPrivateCache();setAuthToken('');setProfile(null);setSupportVisited(false);setRecoveryTarget(null);setSection('dashboard');setError('');setMode('login');setNotice(message);}
 if(loading)return <main className="portal-loading"><Brand/><span className="loading-spinner"/><p>Đang kiểm tra đăng nhập…</p></main>;
 if(profile)return <div className="app-shell" data-section={section} data-role={isAdmin?'admin':'user'}>
  {drawer&&<button className="sidebar-scrim" aria-label="Đóng điều hướng" onClick={()=>{setDrawer(false);drawerButton.current?.focus();}}/>}
  <aside className={'portal-sidebar '+(drawer?'is-open':'')} aria-label="Thanh điều hướng">
   <div className="sidebar-brand"><Brand/><button className="mobile-close icon-button" aria-label="Đóng menu" onClick={()=>{setDrawer(false);drawerButton.current?.focus();}}><Icon name="close"/></button></div>
   <div className="workspace-chip"><span className="workspace-avatar"><Icon name={isAdmin?'users':'box'}/></span><div><strong>{isAdmin?'Quản trị hệ thống':profile.warehouse?.name||'Kho của bạn'}</strong><small>{isAdmin?'Không gian quản trị':'Không gian làm việc riêng'}</small></div></div>
   <p className="nav-caption">{isAdmin?'ĐIỀU HÀNH HỆ THỐNG':'KHÔNG GIAN KIỂM KÊ'}</p>
   <nav role="tablist" aria-orientation="vertical" aria-label="Điều hướng chính" className="sidebar-nav" hidden={forced} onKeyDown={e=>{
    if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key)||forced)return;e.preventDefault();
    const i=sections.findIndex(s=>s[0]===section),n=e.key==='Home'?0:e.key==='End'?sections.length-1:(i+(e.key==='ArrowDown'?1:-1)+sections.length)%sections.length;
    navigate(sections[n][0]);document.getElementById('nav-'+sections[n][0])?.focus();
   }}>{sections.map(([id,icon,label])=><button type="button" key={id} id={'nav-'+id} role="tab" aria-selected={section===id} aria-controls={'panel-'+id} tabIndex={section===id?0:-1} disabled={forced} onClick={()=>navigate(id)}><Icon name={icon}/><span>{label}</span>{section===id&&<i/>}</button>)}</nav>
   <div className="sidebar-bottom">{!isAdmin&&!forced&&profile.warehouse&&<UserStorage key={profile.warehouse.warehouse_id} warehouseId={profile.warehouse.warehouse_id} onChange={setStorageStatus}/>}<span className="sidebar-footnote">Kiểm kê rõ ràng. Dữ liệu riêng tư.</span></div>
  </aside>
  <div className="portal-main">
   <header className="portal-topbar"><div className="topbar-context"><button ref={drawerButton} className="icon-button mobile-menu" aria-label="Mở menu điều hướng" aria-expanded={drawer} onClick={()=>setDrawer(!drawer)}><Icon name="menu"/></button><span className="topbar-breadcrumb">{isAdmin?'Quản trị':'Kho của tôi'}<i>/</i><strong>{section==='password'?'Đổi mật khẩu':section==='email'?'Email khôi phục':sections.find(s=>s[0]===section)?.[2]}</strong></span></div>
    <div className="topbar-actions">
     <details ref={menu} className="profile-menu"><summary ref={menuButton} aria-label={'Tài khoản: '+profile.user.display_name}><span className="profile-avatar">{profile.user.display_name.slice(0,1).toUpperCase()}</span><span className="profile-name"><strong>{profile.user.display_name}</strong><small>{isAdmin?'Quản trị viên':'Người giám sát kho'}</small></span><Icon name="chevron" size={16}/></summary>
      <div className="profile-dropdown"><div><strong>{profile.user.username}</strong><small>{isAdmin?'Tài khoản quản trị':profile.warehouse?.name}</small></div>
       {!forced&&<><button onClick={()=>navigate('password')}><Icon name="lock"/>Đổi mật khẩu</button>{!isAdmin&&<button onClick={()=>navigate('email')}><Icon name="mail"/>Email khôi phục</button>}</>}
       <button disabled={busy} className="logout-item" onClick={logout}><Icon name="logout"/>Đăng xuất</button>
      </div></details>
    </div>
   </header>
   <div className="portal-content">
    {error&&<p className="portal-alert error" role="alert">{error}</p>}
    {!isAdmin&&!forced&&profile.user.email===null&&section!=='email'&&<p className="portal-alert">Tài khoản chưa có email khôi phục. Bổ sung tại menu tài khoản góc phải → Email khôi phục.</p>}
    {forced?<PasswordPanel required onDone={passwordDone}/>:<>
     {section==='email'&&!isAdmin&&<RecoveryEmailPanel email={profile.user.email} onDone={passwordDone} onCancel={()=>navigate('dashboard')}/>}
     {section==='password'&&<PasswordPanel onDone={passwordDone} onCancel={()=>navigate('dashboard')}/>}
     {isAdmin?<>
      {section==='dashboard'&&<div id="panel-dashboard" role="tabpanel" aria-labelledby="nav-dashboard"><AdminDashboard profile={profile} onNavigate={navigate}/></div>}
      <div hidden={!['accounts','warehouses'].includes(section)} id={'panel-'+adminView} role="tabpanel" aria-labelledby={'nav-'+adminView}><AdminAccountsPanel view={adminView} recoveryTarget={recoveryTarget} onWarehouseOpen={()=>navigate('warehouses')} onAccounts={()=>navigate('accounts')}/></div>
     </>:profile.warehouse?<div hidden={!core.has(section)}><App key={profile.warehouse.warehouse_id} storageNamespace={profile.warehouse.warehouse_id} activeSection={section} onSectionChange={next=>setSection(current=>core.has(current)?next:current)} profile={profile} storageStatus={storageStatus}/></div>:<p role="alert">Tài khoản chưa có kho hợp lệ.</p>}
     <div id="panel-support" role="tabpanel" aria-labelledby="nav-support" hidden={section!=='support'}>{supportVisited&&<SupportPanel key={profile.user.user_id} admin={isAdmin} active={section==='support'} onRecovery={target=>{setRecoveryTarget({...target,key:Date.now()});navigate('accounts');}}/>}</div>
    </>}
   </div>
  </div>
 </div>;
 return <div className="public-portal"><header className="public-header"><Brand/><span className="public-descriptor">Nền tảng hỗ trợ kiểm kê bằng hình ảnh</span></header>
  <main className={'auth-stage '+(mode==='support'?'support-stage':'')}>
   {mode==='support'?<GuestSupportPanel onBack={()=>{setMode('login');setError('');}}/>:<section className="auth-layout">
    <aside className="auth-story"><span className="hero-tag">MỖI KIỆN HÀNG ĐỀU ĐƯỢC GHI NHẬN</span><div className="auth-art" aria-hidden="true"><span className="art-orbit one"/><span className="art-orbit two"/><Icon name="box" size={155}/><span className="art-check"><Icon name="check" size={24}/></span><span className="art-label"><Icon name="scan"/> Thu ảnh · Kiểm tra · Đối soát</span></div><div><h2>Từ hình ảnh<br/>đến số liệu rõ ràng.</h2><p>Kết nối camera, kiểm tra từng đơn vị hàng và theo dõi biến động tồn kho trong không gian của riêng bạn.</p></div><div className="auth-story-foot"><Icon name="lock" size={18}/><span>Tài khoản riêng · Kho dữ liệu riêng</span></div></aside>
    <div className="auth-form-side"><div className="auth-welcome"><span className="welcome-icon"><Icon name="box" size={24}/></span><h1>{mode==='register'?'Bắt đầu với kho của bạn':'Chào mừng trở lại'}</h1><p>{mode==='register'?'Tạo tài khoản để mở không gian kiểm kê riêng.':'Đăng nhập để tiếp tục công việc kiểm kê.'}</p></div>
     <div className="auth-mode-tabs" role="tablist" aria-label="Truy cập tài khoản"><button role="tab" aria-selected={mode==='login'} className={mode==='login'?'selected':''} disabled={busy} onClick={()=>{setMode('login');setError('');}}>Đăng nhập</button>{registrationOpen&&<button role="tab" aria-selected={mode==='register'} className={mode==='register'?'selected':''} disabled={busy} onClick={()=>{setMode('register');setError('');}}>Đăng ký</button>}</div>
     {error&&<p className="portal-alert error" role="alert">{error}</p>}{notice&&<p className="portal-alert success" role="status">{notice}</p>}
     <form key={mode} className="portal-form auth-form" onSubmit={submit}>
      <label>Tên đăng nhập<input name="username" required minLength={mode==='register'?3:1} maxLength={32} autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="Nhập tên đăng nhập"/></label>
      {mode==='register'&&<label>Email<input name="email" type="email" required maxLength={254} autoComplete="email" placeholder="Email dùng để khôi phục tài khoản"/></label>}
      {mode==='register'&&<div className="form-columns"><label>Tên hiển thị<input name="display_name" required maxLength={80} autoComplete="name" placeholder="Tên của bạn"/></label><label>Tên kho<input name="warehouse_name" required maxLength={100} placeholder="Ví dụ: Kho linh kiện"/></label></div>}
      <div className="password-label"><div className="password-heading"><label htmlFor="auth-password">Mật khẩu</label>{mode==='login'&&<button type="button" className="text-button" onClick={()=>{setMode('support');setError('');}}>Quên mật khẩu?</button>}</div><span className="password-input"><input id="auth-password" name="password" type={showPassword?'text':'password'} required minLength={mode==='register'?12:1} maxLength={128} autoComplete={mode==='register'?'new-password':'current-password'} placeholder="Nhập mật khẩu"/><button type="button" className="password-toggle" aria-label={showPassword?'Ẩn mật khẩu':'Hiện mật khẩu'} onClick={()=>setShowPassword(!showPassword)}>{showPassword?'Ẩn':'Hiện'}</button></span></div>
      {mode==='register'&&<><small className="form-note">Mật khẩu từ 12 đến 128 ký tự.</small><label>Nhập lại mật khẩu<input name="confirm_password" type="password" required maxLength={128} autoComplete="new-password"/></label></>}
      <button className="primary-button auth-submit" disabled={busy}>{busy?'Đang xử lý…':mode==='register'?'Tạo tài khoản':'Đăng nhập'}<Icon name="arrow"/></button>
     </form>

     {error&&<button className="text-button" disabled={busy} onClick={restore}>Thử kết nối lại</button>}
     <div className="auth-form-foot"><Icon name="lock" size={15}/> Dữ liệu được quản lý theo tài khoản và quyền truy cập.</div>
    </div>
   </section>}
  </main><footer className="public-footer">Hệ thống hỗ trợ kiểm kê kho bằng xử lý ảnh từ camera IP</footer>
 </div>;
}
