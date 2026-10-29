import { apiFetch } from './authClient';
import { useEffect, useRef, useState } from 'react';
import './warehouse.css';
import CatalogPanel from './CatalogPanel';
import ImageTaskPanel from './ImageTaskPanel';
import WarehouseDashboard from './WarehouseDashboard';
import ReportExport from './ReportExport';
import ReportTrace from './ReportTrace';
import Icon from './Icon';
import TransientNotice from './TransientNotice';
import GatewayCameraPanel from './GatewayCameraPanel';
import GatewayVideoPreview from './GatewayVideoPreview';
import {latestImageTask,readImageTask,submitImageTask,watchImageTask,requestId} from './imageTaskClient';

const AUTO_NOTE =
  '[AUTO] Tự động chấp nhận vùng AI không có cảnh báo; chưa kiểm tra thủ công.';

const panel = {
  padding: 20, marginBottom: 20, border: '1px solid #dbe2ea',
  borderRadius: 12, background: '#fff', color: '#172033',
};
const row = {
  display: 'flex', gap: 12, flexWrap: 'wrap',
  alignItems: 'center', margin: '12px 0',
};
const input = {
  padding: 9, border: '1px solid #aab6c5',
  borderRadius: 6, maxWidth: '100%',
};
const warning = {
  padding: 12, background: '#fff3cd',
  color: '#664d03', borderRadius: 6,
};

async function api(path, body, method, timeoutMs = 180000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await apiFetch(path, {
      method: method || (body === undefined ? 'GET' : 'POST'),
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(
        typeof data.detail === 'string' ? data.detail
          : Array.isArray(data.detail)
            ? data.detail.map((item) => item.msg).join('; ')
            : `Yêu cầu thất bại: HTTP ${response.status}`
      );
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(
        'Hết thời gian chờ. Backend có thể vẫn đang xử lý. '
        + 'Tải lại bản nháp để kiểm tra trước khi thử lại.'
      );
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function validId(value) {
  return /^\d+$/.test(String(value))
    && Number(value) >= 0 && Number(value) <= 249;
}

function cleanNote(note = '') {
  return note === AUTO_NOTE ? '' : note;
}

function BoxEvidence({ url, bounds, width, height, label }) {
  const [x1, y1, x2, y2] = bounds;
  const pad = Math.max(8, Math.min(x2 - x1, y2 - y1) * 0.05);
  const left = Math.max(0, x1 - pad);
  const top = Math.max(0, y1 - pad);
  const right = Math.min(width, x2 + pad);
  const bottom = Math.min(height, y2 + pad);
  return <div className="duplicate-evidence">
    <strong>{label}</strong>
    <svg role="img" aria-label={label} viewBox={`${left} ${top} ${right - left} ${bottom - top}`}>
      <image href={url} x="0" y="0" width={width} height={height} />
      <rect x={x1} y={y1} width={x2 - x1} height={y2 - y1}
        fill="none" stroke="#f59e0b" strokeWidth="4" vectorEffect="non-scaling-stroke" />
    </svg>
    <a href={url} target="_blank" rel="noopener noreferrer">Mở ảnh đầy đủ</a>
  </div>;
}

// Chỉ tự chấp nhận khi cả vùng và các marker được gán đều rõ ràng.
function eligibleForAuto(review, original) {
  if (
    original.status !== 'proposed'
    || (original.warnings || []).length
    || !validId(original.suggested_marker_id)
  ) return false;

  const markers = review.analysis.markers.filter(
    (m) => m.suggested_box_id === original.box_id
  );

  return markers.length > 0 && markers.every(
    (m) => m.status === 'assigned'
      && !(m.warnings || []).length
      && m.marker_id === original.suggested_marker_id
  );
}

function editorFromReview(review) {
  if (review.human_review) {
    return {
      reviewId: review.review_id,
      boxes: review.human_review.boxes.map((box) => ({
        ...box,
        bounds: [...box.bounds],
        marker_id: box.marker_id == null ? '' : String(box.marker_id),
        note: box.note || '',
      })),
      excluded_regions: (review.human_review.excluded_regions || []).map((region) => ({
        ...region, bounds: [...region.bounds],
      })),
      assignments: { ...review.human_review.assignments },
    };
  }

  return {
    reviewId: review.review_id,
    excluded_regions: [],
    boxes: review.analysis.boxes.map((box) => {
      const accepted = eligibleForAuto(review, box);
      return {
        box_id: box.box_id,
        bounds: [...box.bounds],
        marker_id: box.suggested_marker_id == null
          ? '' : String(box.suggested_marker_id),
        // Backend hiện dùng reviewed để chỉ vùng đủ điều kiện ghi.
        // Ghi chú phân biệt tự động với kiểm tra thủ công.
        reviewed: accepted,
        note: accepted ? AUTO_NOTE : '',
      };
    }),
    assignments: Object.fromEntries(
      review.analysis.markers.map((m) => [
        m.detection_id, m.suggested_box_id || '',
      ])
    ),
  };
}

function statusText(box) {
  if (!box.reviewed) return 'Cần kiểm tra';
  return box.note === AUTO_NOTE ? 'AI tự chấp nhận' : 'Đã kiểm tra thủ công';
}

function StockTable({ items, daily = false, onTrace }) {
  const cell = { padding: 10, borderBottom: '1px solid #dbe2ea', textAlign: 'left' };
  return <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse' }}>
    <thead><tr>{['Loại hàng', daily ? 'Tồn đầu ngày' : 'Mốc trước', daily ? 'Tồn cuối' : 'Đã ghi nhận', 'Tăng', 'Giảm'].map((t) => <th key={t} style={cell}>{t}</th>)}{onTrace && <th style={cell}>Nguồn số liệu</th>}</tr></thead>
    <tbody>{items.map((item) => <tr key={item.marker_id}>
      <td style={cell}>{item.product_name} · ID {item.marker_id}</td>
      <td style={cell}>{daily ? item.opening_quantity : item.reference_quantity}</td>
      <td style={cell}>{daily ? item.closing_quantity : item.recorded_quantity}</td>
      <td style={cell}>{(daily ? item.total_increase : item.increase) ?? 'Chưa chốt'}</td>
      <td style={cell}>{(daily ? item.total_decrease : item.decrease) ?? 'Chưa chốt'}</td>
      {onTrace && <td style={cell}><button type="button" className="secondary" onClick={() => onTrace(item.marker_id)}>Xem nguồn</button></td>}
    </tr>)}</tbody>
  </table></div>;
}

// Pull one frame at a time: fetch -> decode -> display -> next request.
// No growing MJPEG download queue in the browser.
function CameraPreview({ session, attempt, targetFps = 30, enabled = true }) {
  const canvasRef = useRef(null);
  const [message, setMessage] = useState('Đang tải hình…');
  const [fps, setFps] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let controller = null;
    let frameRequest = null;
    let finishDraw = null;
    let lastSequence = -1;
    let displayed = 0;
    let sampleStart = performance.now();
    let displayedAny = false;
    const interval = 1000 / Math.max(1, Math.min(60, targetFps || 30));
    const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    setMessage('Đang tải hình…');
    setFps(0);

    async function loop() {
      while (active) {
        const started = performance.now();
        let bitmap = null;
        let timeout = null;
        try {
          controller = new AbortController();
          timeout = setTimeout(() => controller?.abort(), 5000);
          const response = await apiFetch(`/api/camera/preview.jpg?session=${session}&after=${lastSequence}`, {
            cache: 'no-store', signal: controller.signal,
          });
          if (response.status === 409) throw new Error('Camera đã đổi. Bấm Tải lại hình hoặc chờ cập nhật.');
          if (response.status === 404) throw new Error('Backend chưa cập nhật preview. Khởi động lại bằng daily_api.');
          if (response.status === 204) {
            clearTimeout(timeout);
            if (performance.now() - sampleStart > 2000) {
              if (active) { setFps(0); setMessage('Đang chờ khung hình mới…'); }
              displayed = 0; sampleStart = performance.now();
            }
            await pause(Math.max(8, interval - (performance.now() - started)));
            continue;
          }
          if (!response.ok) throw new Error('Đang chờ ảnh mới từ camera…');
          const blob = await response.blob();
          clearTimeout(timeout);
          if (!active) break;
          bitmap = await createImageBitmap(blob);
          if (!active) break;
          await new Promise((resolve, reject) => {
            finishDraw = resolve;
            frameRequest = requestAnimationFrame(() => {
              finishDraw = null;
              frameRequest = null;
              try {
                const canvas = canvasRef.current;
                if (active && canvas) {
                  if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
                    canvas.width = bitmap.width; canvas.height = bitmap.height;
                  }
                  const context = canvas.getContext('2d', { alpha: false });
                  if (!context) throw new Error('Không tạo được vùng hiển thị camera.');
                  context.drawImage(bitmap, 0, 0);
                }
                resolve();
              } catch (error) { reject(error); }
            });
          });
          if (!active) break;
          lastSequence = Number(response.headers.get('X-Frame-Sequence') ?? -1);
          displayed += 1;
          if (!displayedAny) { setMessage(''); displayedAny = true; }
          const elapsed = performance.now() - sampleStart;
          if (elapsed >= 1000) {
            setFps(Math.round(displayed * 1000 / elapsed));
            setMessage(''); displayed = 0; sampleStart = performance.now();
          }
        } catch (err) {
          if (!active) break;
          setMessage(err.name === 'AbortError' ? 'Preview phản hồi chậm; đang thử lại…' : err.message);
          setFps(0);
          displayed = 0; sampleStart = performance.now();
          await pause(250);
        } finally {
          clearTimeout(timeout);
          bitmap?.close();
        }
        if (active) await pause(Math.max(0, interval - (performance.now() - started)));
      }
    }
    loop();
    return () => {
      active = false;
      controller?.abort();
      if (frameRequest !== null) cancelAnimationFrame(frameRequest);
      finishDraw?.();
    };
  }, [session, attempt, targetFps, enabled]);

  return <div style={{ position: 'relative', width: '100%', height: '100%' }}>
    <canvas ref={canvasRef} width={1920} height={1080} role="img" aria-label="Hình trực tiếp từ camera"
      style={{ display: 'block', width: '100%', height: '100%', objectFit: 'contain' }} />
    <span role="status" style={{ position: 'absolute', bottom: 8, left: 8, right: message ? 8 : 'auto',
      padding: '4px 8px', borderRadius: 5, background: '#17263cdd', color: '#fff', fontSize: 12 }}>
      {message || `${fps} FPS hiển thị`}
    </span>
  </div>;
}

export default function App({ storageNamespace, activeSection, onSectionChange, profile, storageStatus }) {
  const STORAGE_KEY = `warehouse-review-editor-v1:${storageNamespace}`;
  const LAST_DAY_KEY = `warehouse-last-day-v1:${storageNamespace}`;
  const [localTab, setLocalTab] = useState('dashboard');
  const activeTab = activeSection ?? localTab;
  const setActiveTab = onSectionChange || setLocalTab;
  const [focusImage, setFocusImage] = useState(false);
  const [onlyPending, setOnlyPending] = useState(false);
  const [address, setAddress] = useState('');
  const [cameraAuth, setCameraAuth] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [camera, setCamera] = useState(null);
  const [serverError, setServerError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [operationBusy, setBusy] = useState(false);
  const [imageTask,setImageTask]=useState(null);
  const taskNoticeKey=storageNamespace?`warehouse-task-notice-hidden-v1:${storageNamespace}`:null;
  const [hiddenTaskNotice,setHiddenTaskNotice]=useState(()=>{
    if(!taskNoticeKey)return null;
    try{
      const saved=JSON.parse(localStorage.getItem(taskNoticeKey)||'null');
      return saved&&/^[0-9a-f]{32}$/.test(saved.task_id)&&['completed','failed'].includes(saved.state)?saved:null;
    }catch{return null;}
  });
  const taskNoticeHidden=!!imageTask&&['completed','failed'].includes(imageTask.state)
    &&hiddenTaskNotice?.task_id===imageTask.task_id&&hiddenTaskNotice?.state===imageTask.state;
  function hideTaskNotice(){
    if(!imageTask||!['completed','failed'].includes(imageTask.state))return;
    const saved={task_id:imageTask.task_id,state:imageTask.state};
    setHiddenTaskNotice(saved);
    if(taskNoticeKey)try{localStorage.setItem(taskNoticeKey,JSON.stringify(saved));}catch{}
  }
  const [taskStatusError,setTaskStatusError]=useState('');
  const taskActive=!!imageTask&&['preparing','queued','running'].includes(imageTask.state);
  const busy=operationBusy||taskActive;
  const taskController=useRef(null);
  const taskRequest=useRef(null);
  const [previewKey, setPreviewKey] = useState(0);

  const [days, setDays] = useState([]);
  const [dayId, setDayId] = useState('');
  const [day, setDay] = useState(null);
  const [dayReport, setDayReport] = useState(null);
  const [today, setToday] = useState('');
  const [catalog, setCatalog] = useState([]);
  const [openingCounts, setOpeningCounts] = useState({});
  const [dayReady, setDayReady] = useState(false);
  const [coverageAccepted, setCoverageAccepted] = useState(false);
  const [emptyAccepted, setEmptyAccepted] = useState(false);
  const [dayCloseAccepted, setDayCloseAccepted] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState('');
  const [sessionName, setSessionName] = useState('');
  const [sessionData, setSessionData] = useState(null);
  const [sessionError, setSessionError] = useState('');
  const [newBoxesAccepted, setNewBoxesAccepted] = useState(false);
  const [duplicateCheck, setDuplicateCheck] = useState(null);
  const [boxDecisions, setBoxDecisions] = useState({});
  const [referencePreviews, setReferencePreviews] = useState({});
  const [returnReviewId, setReturnReviewId] = useState('');
  const [confirmationUncertain, setConfirmationUncertain] = useState(false);

  const [review, setReview] = useState(null);
  const [editor, setEditor] = useState(null);
  const [reviewId, setReviewId] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [mode, setMode] = useState('select');
  const [drag, setDrag] = useState(null);
  const [showMasks, setShowMasks] = useState(true);
  const [imageReady, setImageReady] = useState(false);
  const [imageVersion, setImageVersion] = useState(0);
  const [unsynced, setUnsynced] = useState(false);

  const svgRef = useRef(null);
  const busyRef = useRef(false);
  const configBusy = useRef(false);
  const cameraOperation = useRef(0);
  const unprotectedRef = useRef(false);

  useEffect(()=>{
    const controller=new AbortController();
    latestImageTask(controller.signal).then(data=>{if(!controller.signal.aborted&&!taskController.current)setImageTask(data.task);})
      .catch(error=>{if(!controller.signal.aborted&&!taskController.current)setError('Chưa tải được tác vụ gần nhất. '+error.message);});
    return ()=>{controller.abort();taskController.current?.abort();};
  },[]);

  // Reload restores metadata, then keeps it current without submitting work or
  // installing an old result over the user's editor. A manual Follow owns its
  // own watcher while run() is busy; this observer pauses to avoid stale races.
  useEffect(()=>{
    if(!taskActive||operationBusy||['support','password'].includes(activeSection))return;
    const id=imageTask.task_id,controller=new AbortController();
    let timer;
    async function refresh(){
      let delay=1000;
      try{
        const current=await readImageTask(id,controller.signal);
        if(controller.signal.aborted)return;
        setTaskStatusError('');
        setImageTask(previous=>previous?.task_id===id?current:previous);
        if(['completed','failed'].includes(current.state)){
          taskRequest.current=null;
          return;
        }
      }catch(error){
        if(controller.signal.aborted)return;
        setTaskStatusError(error.message);
        delay=3000;
      }
      if(!controller.signal.aborted)timer=setTimeout(refresh,delay);
    }
    refresh();
    return ()=>{controller.abort();clearTimeout(timer);};
  },[imageTask?.task_id,taskActive,operationBusy,activeSection]);

  async function queueImageWork(kind,request){
    const signature=JSON.stringify({kind,...request});
    if(taskRequest.current?.signature!==signature)taskRequest.current={signature,id:requestId()};
    const controller=new AbortController();taskController.current=controller;setTaskStatusError('');
    const task=await submitImageTask({request_id:taskRequest.current.id,kind,...request},controller.signal);
    setImageTask(task);
    return await followImageWork(task,controller);
  }

  async function followImageWork(task,controller){
    return await watchImageTask(task.task_id,current=>{
      setTaskStatusError('');
      setImageTask(current);
      if(['completed','failed'].includes(current.state))taskRequest.current=null;
    },controller.signal);
  }

  function acceptComparison(report){
    setDuplicateCheck(report);
    setReferencePreviews({});setNewBoxesAccepted(false);
    setBoxDecisions(Object.fromEntries(report.results.map(item=>[item.box_id,{
      decision:item.default_decision==='new'&&!item.requires_human_decision?'new':'',
      reference_instance_id:null,note:'',
    }])));
    setNotice(report.results.some(r=>r.requires_human_decision)
      ?'Đã tự chọn kiện mới khi đủ điều kiện. Chỉ xử lý các kiện cần kiểm tra.'
      :'Đã tự chọn hàng mới. Bấm Ghi vào phiên để lưu số lượng.');
  }

  function followCurrentTask(){
    if(!imageTask||!mayReplace())return;
    run(async()=>{
      const controller=new AbortController();taskController.current=controller;
      const result=await followImageWork(imageTask,controller);
      if(imageTask.kind==='capture'){
        const fresh=await api(`/api/reviews/${result.review_id}`);
        installReview(fresh);await followReviewSession(fresh);
      }else{
        const fresh=await api(`/api/reviews/${result.review_id}`);
        if(fresh.inventory_recorded||fresh.revision!==result.review_revision)throw new Error('Bản nháp đã đổi hoặc đã ghi. Hãy kiểm tra trùng lại.');
        installReview(fresh);await followReviewSession({...fresh,session_id:result.session_id});
        acceptComparison(result);setActiveTab('review');
      }
    });
  }

  useEffect(() => {
    let disposed = false;
    let timer;
    let controller;

    async function poll() {
      const operation = cameraOperation.current;
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const response = await apiFetch('/api/camera/status', {
          cache: 'no-store', signal: controller.signal,
        });
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (!disposed && !configBusy.current
            && operation === cameraOperation.current) {
          setCamera(data);
          setServerError('');
        }
      } catch {
        if (!disposed && !configBusy.current
            && operation === cameraOperation.current) {
          setCamera(null);
          setServerError('Không liên lạc được backend.');
        }
      } finally {
        clearTimeout(timeout);
        if (!disposed) timer = setTimeout(poll, 1500);
      }
    }

    poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, []);

  useEffect(() => {
    const handler = (event) => {
      if (unprotectedRef.current) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, []);

  async function run(task) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await task();
    } catch (err) {
      setError(err.message || 'Thao tác thất bại.');
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  function clearReview() {
    setReview(null); setEditor(null); setReviewId(''); setSelectedId('');
    setUnsynced(false); setConfirmationUncertain(false); setNewBoxesAccepted(false);
    setDuplicateCheck(null); setBoxDecisions({});
    setDrag(null); setMode('select'); unprotectedRef.current = false;
  }

  async function fetchSession(id, strict = false) {
    setSessionId(id); setSessionData(null); setSessionError('');
    setNewBoxesAccepted(false); setCoverageAccepted(false); setEmptyAccepted(false);
    setDuplicateCheck(null); setBoxDecisions({});
    if (!id) return;
    try {
      const data = await api(`/api/inventory/sessions/${id}/reconciliation`);
      setSessionData({ ...data, total_boxes: data.recorded_total_boxes });
      if (data.day_status !== 'open') {
        setDay((current) => current?.day_id === data.day_id ? { ...current, status: data.day_status } : current);
      }
      return data;
    } catch (err) {
      setSessionError(err.message);
      if (strict) throw err;
    }
  }

  async function loadDay(id, preferredSession = '') {
    setDayReady(false); setDayId(id); setDay(null); setDayReport(null);
    setSessions([]); setSessionData(null); setSessionId(''); setSessionError('');
    setDayCloseAccepted(false); setCoverageAccepted(false); setEmptyAccepted(false);
    if (!id) { setDayReady(true); return; }
    const [detail, listing] = await Promise.all([
      api(`/api/inventory/days/${id}`), api(`/api/inventory/days/${id}/sessions`),
    ]);
    setDay(detail); setSessions(listing.sessions);
    try { localStorage.setItem(LAST_DAY_KEY, id); } catch { /* lưu tùy chọn */ }
    const selected = listing.sessions.find((v) => v.session_id === preferredSession)
      || listing.sessions.find((v) => v.status === 'open') || listing.sessions.at(-1);
    if (selected) await fetchSession(selected.session_id, true);
    if (detail.status === 'closed') setDayReport(await api(`/api/inventory/days/${id}/report`));
    setDayReady(true);
  }

  async function loadDays(preferredDay = '', preferredSession = '') {
    setDayReady(false);
    const data = await api('/api/inventory/days');
    setDays(data.days); setToday(data.business_date_today); setCatalog(data.catalog);
    setOpeningCounts((current) => Object.fromEntries(data.catalog.map((p) => [p.marker_id, current[p.marker_id] ?? '0'])));
    let remembered = '';
    try { remembered = localStorage.getItem(LAST_DAY_KEY) || ''; } catch { /* không bắt buộc */ }
    const selected = data.days.find((v) => v.day_id === preferredDay)
      || data.days.find((v) => v.status === 'open')
      || data.days.find((v) => v.day_id === remembered) || data.days[0];
    await loadDay(selected?.day_id || '', preferredSession);
  }

  useEffect(() => { run(() => loadDays()); }, []);

  function refreshSessions() {
    run(() => loadDays(dayId, sessionId));
  }

  function selectDay(id) {
    if (id === dayId || !mayReplace()) return;
    clearReview(); run(() => loadDay(id));
  }

  function selectSession(id) {
    if (id === sessionId || !mayReplace()) return;
    clearReview(); run(() => fetchSession(id, true));
  }

  function createDay() {
    if (!mayReplace()) return;
    run(async () => {
      const body = { opening_items: null };
      if (!days.length) {
        body.opening_items = catalog.map((p) => {
          const raw = String(openingCounts[p.marker_id] ?? '');
          const quantity = Number(raw);
          if (!/^\d+$/.test(raw) || !Number.isSafeInteger(quantity)) throw new Error('Tồn đầu phải là số nguyên không âm.');
          return { marker_id: p.marker_id, quantity };
        });
        if (!body.opening_items.length) throw new Error('Chưa có danh mục loại hàng.');
      }
      if (!window.confirm(days.length
        ? 'Mở ngày hôm nay và kế thừa tồn của ngày đã chốt gần nhất?'
        : 'Xác nhận danh sách tồn đầu? Sau khi tạo, danh sách này là mốc kiểm kê.')) return;
      let created;
      try { created = await api('/api/inventory/days', body); }
      catch (err) { setDayReady(false); throw new Error(`${err.message} Bấm Làm mới để kiểm tra trước khi thử lại.`); }
      clearReview(); await loadDays(created.day_id);
      setNotice('Đã mở ngày kiểm kê. Có thể tạo phiên đầu tiên.');
    });
  }

  function createSession(event) {
    event.preventDefault();
    if (!mayReplace()) return;
    run(async () => {
      if (!dayWritable) throw new Error('Chưa chọn ngày đang mở.');
      let created;
      try {
        created = await api(`/api/inventory/days/${dayId}/sessions`, { name: sessionName.trim() });
      } catch (err) { setDayReady(false); throw new Error(`${err.message} Hãy làm mới ngày / phiên.`); }
      clearReview(); setSessionName('');
      await loadDays(dayId, created.session_id);
      setActiveTab('camera');
      setNotice('Đã tạo phiên. Kiểm kê lại toàn bộ tồn kho cho phiên này.');
    });
  }

  function closeSession() {
    if (!sessionWritable || !coverageAccepted || (sessionData.total_boxes === 0 && !emptyAccepted)) return;
    if (review && !recorded) return setError('Còn bản nháp chưa ghi nhận. Ghi nhận hoặc đóng bản nháp đang xem trước khi chốt phiên.');
    if (!window.confirm(`Chốt tồn ${sessionData.total_boxes} đơn vị hàng? Các loại chưa ghi nhận sẽ được chốt bằng 0. Phiên sẽ khóa ghi thêm.`)) return;
    run(async () => {
      let result;
      try {
        result = await api(`/api/inventory/sessions/${sessionId}/finalize`, {
          confirm_full_coverage: true, expected_receipt_count: sessionData.receipt_count,
          confirm_empty: emptyAccepted,
        });
      } catch (err) { setDayReady(false); throw new Error(`${err.message} Hãy làm mới ngày / phiên để kiểm tra trạng thái.`); }
      await loadDays(dayId, sessionId);
      setNotice(result.already_finalized ? 'Phiên đã được chốt trước đó.' : 'Đã chốt phiên và lưu số tồn.');
    });
  }

  function previewDayReport() {
    run(async () => {
      setDayReport(null); setDayCloseAccepted(false);
      const result = await api(`/api/inventory/days/${dayId}/report`);
      setDayReport(result);
      if (result.status === 'closed') setDay((current) => ({ ...current, status: 'closed' }));
    });
  }

  function closeDay() {
    if (!dayWritable || !dayReport || !dayCloseAccepted) return;
    if (review && !recorded) return setError('Còn bản nháp chưa ghi nhận. Xử lý hoặc đóng bản nháp đang xem trước.');
    if (!window.confirm(`Chốt ngày ${day.business_date}? Sau khi chốt không được tạo thêm phiên hoặc sửa số lượng ngày này.`)) return;
    run(async () => {
      let result;
      try {
        result = await api(`/api/inventory/days/${dayId}/finalize`, {
          confirm_close: true, expected_session_count: dayReport.session_count,
        });
      } catch (err) { setDayReady(false); setDayReport(null); throw new Error(`${err.message} Hãy làm mới ngày / phiên trước khi thử lại.`); }
      await loadDays(dayId, sessionId);
      setDayReport(result); setNotice('Đã chốt cuối ngày. Báo cáo được lưu cố định.');
    });
  }

  function mayReplace() {
    return !unsynced || window.confirm(
      'Có dữ liệu chưa lưu về backend. Tiếp tục sẽ thay phần đang hiển thị. '
      + 'Bạn nên lưu dự phòng trước.'
    );
  }

  function installReview(data, savedEditor = null) {
    if (!data.analysis || !data.image_url) {
      throw new Error('Bản nháp không hợp lệ.');
    }

    const next = data.inventory_recorded
      ? editorFromReview(data)
      : savedEditor || editorFromReview(data);

    const generatedAuto = !data.human_review
      && next.boxes.some((b) => b.note === AUTO_NOTE && b.reviewed);

    setActiveTab('review');
    setReview(data);
    setReviewId(data.review_id);
    setEditor({ ...next, excluded_regions: next.excluded_regions || [] });
    setShowMasks(true);
    setSelectedId(
      next.boxes.find((b) => !b.reviewed)?.box_id
      || next.boxes[0]?.box_id || ''
    );
    setMode('select');
    setDrag(null);
    setImageReady(false);
    setImageVersion((n) => n + 1);
    setUnsynced(!data.inventory_recorded && (
      Boolean(savedEditor) || generatedAuto
    ));
    setNewBoxesAccepted(false);
    setDuplicateCheck(null); setBoxDecisions({});
    setConfirmationUncertain(false);
    unprotectedRef.current = false;
  }

  async function followReviewSession(data) {
    const id = data.receipt?.session_id || data.session_id;
    if (!id) return;
    try {
      const reconciliation = await api(`/api/inventory/sessions/${id}/reconciliation`);
      await loadDays(reconciliation.day_id, id);
    } catch (err) {
      setSessionId(id); setSessionData(null); setSessionError(err.message);
      setNotice('Bản nháp đã tải, nhưng phiên không thuộc luồng kiểm kê theo ngày hoặc chưa đọc được trạng thái.');
    }
  }

  const remoteCameraMode=window.location.protocol==='https:'&&!['localhost','127.0.0.1','::1'].includes(window.location.hostname);

  function connect(shouldConnect) {
    run(async () => {
      configBusy.current = true;
      cameraOperation.current += 1;
      setCamera(null);
      try {
        const data = shouldConnect
          ? await api('/api/camera/connect', { address, ...(cameraAuth ? { username, password } : {}) })
          : await api('/api/camera/disconnect', undefined, 'POST');
        setCamera(data);
        setServerError('');
        setPreviewKey((n) => n + 1);
      } finally {
        cameraOperation.current += 1;
        configBusy.current = false;
      }
    });
  }

  function capture() {
    if (!sessionWritable) return setError('Cần chọn phiên đang mở thuộc ngày đang mở.');
    if (!mayReplace()) return;
    run(async () => {
      const result=await queueImageWork('capture', { session_id: sessionId });
      installReview(await api(`/api/reviews/${result.review_id}`));
      setNotice('Vùng rõ ràng được tự chấp nhận. Kiểm tra các vùng màu cam.');
    });
  }

  function loadReview() {
    if (!mayReplace()) return;
    run(async () => {
      const id = reviewId.trim();
      if (!/^[0-9a-f]{32}$/.test(id)) {
        throw new Error('Mã bản nháp cần có 32 ký tự hợp lệ.');
      }
      const data = await api(`/api/reviews/${id}`);
      installReview(data);
      await followReviewSession(data);
      setNotice(data.inventory_recorded
        ? 'Bản nháp đã ghi nhận và khóa sửa.'
        : 'Đã tải bản nháp. Chỉ xử lý các vùng còn cần kiểm tra.');
    });
  }

  function openReferenceReview(id, returning = false) {
    if (!id || !mayReplace()) return;
    const currentId = review?.review_id;
    run(async () => {
      const data = await api(`/api/reviews/${id}`);
      installReview(data);
      await followReviewSession(data);
      setReturnReviewId(returning ? '' : currentId);
      setNotice(returning ? 'Đã quay lại. Bấm Kiểm tra trùng để cập nhật.' : 'Kiểm tra và ghi ảnh này làm mốc, sau đó quay lại ảnh vừa chụp.');
    });
  }

  function restoreLocal() {
    if (!mayReplace()) return;
    run(async () => {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) throw new Error('Chưa có bản lưu trên trình duyệt.');
      const saved = JSON.parse(raw);
      if (!saved.editor || !Array.isArray(saved.editor.boxes)) {
        throw new Error('Bản lưu không hợp lệ.');
      }
      const data = await api(`/api/reviews/${saved.editor.reviewId}`);
      if (!data.inventory_recorded && data.revision !== saved.revision) {
        throw new Error(`Bản lưu cũ. Hãy tải ${data.review_id} từ backend.`);
      }
      installReview(data, data.inventory_recorded ? null : saved.editor);
      await followReviewSession(data);
      setNotice(data.inventory_recorded
        ? 'Bản đã ghi nhận: hiển thị dữ liệu backend.'
        : 'Đã khôi phục. Cần lưu về backend trước khi ghi nhận.');
    });
  }

  function changeEditor(update) {
    if (editLocked) return;
    unprotectedRef.current = true;
    setUnsynced(true);
    setNewBoxesAccepted(false);
    setDuplicateCheck(null); setBoxDecisions({});
    setNotice('Có thay đổi chưa lưu.');
    setEditor((current) => update(current));
  }

  function patchBox(id, patch) {
    changeEditor((current) => ({
      ...current,
      boxes: current.boxes.map((box) => box.box_id === id
        ? {
          ...box, note: cleanNote(box.note),
          ...patch, reviewed: false,
        } : box),
    }));
  }

  function assignMarker(marker, owner) {
    const oldOwner = editor.assignments[marker.detection_id];
    if (oldOwner === owner) return;
    changeEditor((current) => ({
      ...current,
      assignments: { ...current.assignments, [marker.detection_id]: owner },
      boxes: current.boxes.map((box) => {
        if (box.box_id !== oldOwner && box.box_id !== owner) return box;
        return {
          ...box, reviewed: false, note: cleanNote(box.note),
          marker_id: box.box_id === owner && box.marker_id === ''
            ? String(marker.marker_id) : box.marker_id,
        };
      }),
    }));
  }

  // Dành cho bản nháp cũ đã lưu trước khi có quy tắc tự chấp nhận.
  // Chỉ áp dụng cho vùng chưa sửa ID, tọa độ, ghi chú hoặc cách gán.
  function acceptClearRegions() {
    let accepted = 0;
    const boxes = editor.boxes.map((box) => {
      const original = review.analysis.boxes.find(
        (b) => b.box_id === box.box_id
      );
      if (box.reviewed || box.note || !original
          || !eligibleForAuto(review, original)) return box;
      if (String(original.suggested_marker_id) !== String(box.marker_id)) return box;
      if (!box.bounds.every((value, i) => value === original.bounds[i])) return box;

      const assignmentUnchanged = review.analysis.markers.every((marker) => {
        const before = marker.suggested_box_id === box.box_id;
        const after = editor.assignments[marker.detection_id] === box.box_id;
        return before === after;
      });
      if (!assignmentUnchanged) return box;

      accepted += 1;
      return { ...box, reviewed: true, note: AUTO_NOTE };
    });

    if (!accepted) {
      setNotice('Không có thêm vùng chưa sửa đủ điều kiện tự chấp nhận.');
      return;
    }
    changeEditor((current) => ({ ...current, boxes }));
    setSelectedId(boxes.find((b) => !b.reviewed)?.box_id || selectedId);
    setNotice(`Đã tự chấp nhận ${accepted} vùng rõ ràng. Cần lưu về backend.`);
  }

  function deleteBox() {
    if (!selected || !window.confirm('Xóa vùng đơn vị hàng đang chọn?')) return;
    const id = selected.box_id;
    changeEditor((current) => ({
      ...current,
      boxes: current.boxes.filter((b) => b.box_id !== id),
      assignments: Object.fromEntries(
        Object.entries(current.assignments).map(([marker, owner]) => [
          marker, owner === id ? '' : owner,
        ])
      ),
    }));
    setSelectedId('');
    setMode('select');
  }

  function point(event) {
    const svg = svgRef.current;
    const matrix = svg?.getScreenCTM();
    if (!matrix) return null;
    const p = svg.createSVGPoint();
    p.x = event.clientX;
    p.y = event.clientY;
    const result = p.matrixTransform(matrix.inverse());
    return [
      Math.max(0, Math.min(review.width, result.x)),
      Math.max(0, Math.min(review.height, result.y)),
    ];
  }

  function pointerDown(event) {
    if (editLocked || !imageReady || mode === 'select' || event.button !== 0) return;
    const p = point(event);
    if (!p) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ start: p, end: p });
  }

  function pointerUp(event) {
    if (!drag || editLocked) return;
    const end = point(event) || drag.end;
    const bounds = [
      Math.min(drag.start[0], end[0]), Math.min(drag.start[1], end[1]),
      Math.max(drag.start[0], end[0]), Math.max(drag.start[1], end[1]),
    ];
    setDrag(null);
    if (bounds[2] - bounds[0] < 10 || bounds[3] - bounds[1] < 10) {
      setError('Vùng quá nhỏ. Hãy kéo rộng hơn để bao đúng vùng cần chọn.');
      return;
    }
    if (mode === 'exclude') {
      if ((editor.excluded_regions || []).length >= 200) {
        setError('Tối đa 200 vùng che trong một ảnh.');
        return;
      }
      changeEditor((current) => ({
        ...current,
        excluded_regions: [...(current.excluded_regions || []), {
          region_id: `exclude-${crypto.randomUUID()}`, bounds,
        }],
      }));
      setShowMasks(true);
    } else if (mode === 'redraw' && selectedId) {
      patchBox(selectedId, { bounds });
    } else if (mode === 'add') {
      const id = `manual-${crypto.randomUUID()}`;
      changeEditor((current) => ({
        ...current,
        boxes: [...current.boxes, {
          box_id: id, bounds, marker_id: '', reviewed: false, note: '',
        }],
      }));
      setSelectedId(id);
    }
    setMode('select');
  }

  function boxIssue(box) {
    if (!validId(box.marker_id)) return 'ID phải là số nguyên từ 0 đến 249.';
    const assigned = review.analysis.markers.filter(
      (m) => editor.assignments[m.detection_id] === box.box_id
    );
    if (assigned.some((m) => m.marker_id !== Number(box.marker_id))) {
      return 'ID nhập khác marker đang gán. Kiểm tra hoặc bỏ gán marker sai.';
    }
    return '';
  }

  function markReviewed() {
    const issue = boxIssue(selected);
    if (issue) return setError(issue);
    changeEditor((current) => ({
      ...current,
      boxes: current.boxes.map((box) => box.box_id === selectedId
        ? { ...box, reviewed: true, note: cleanNote(box.note) } : box),
    }));
    setError('');
  }

  function saveLocal() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        revision: review.revision, editor,
      }));
      unprotectedRef.current = false;
      setNotice('Đã lưu dự phòng trên trình duyệt.');
    } catch {
      setError('Không lưu được trên trình duyệt. Hãy giữ trang đang mở.');
    }
  }

  function saveBackend() {
    if (editLocked) return;
    run(async () => {
      const boxes = editor.boxes.map((box, index) => {
        const empty = box.marker_id === '';
        if (!empty && !validId(box.marker_id)) {
          throw new Error(`Đơn vị hàng ${index + 1}: ID không hợp lệ.`);
        }
        if (box.reviewed && boxIssue(box)) {
          throw new Error(`Đơn vị hàng ${index + 1}: ${boxIssue(box)}`);
        }
        return {
          box_id: box.box_id, bounds: box.bounds,
          marker_id: empty ? null : Number(box.marker_id),
          reviewed: Boolean(box.reviewed), note: box.note || '',
        };
      });

      const data = await api(`/api/reviews/${review.review_id}/correction`, {
        revision: review.revision,
        boxes, assignments: editor.assignments, note: '',
        excluded_regions: editor.excluded_regions || [],
      }, 'PUT');

      const next = editorFromReview(data);
      setReview(data);
      setEditor(next);
      setUnsynced(false);
      setNewBoxesAccepted(false);
      setDuplicateCheck(null); setBoxDecisions({});
      unprotectedRef.current = false;

      let extra = '';
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          revision: data.revision, editor: next,
        }));
      } catch {
        extra = ' Không cập nhật được bản dự phòng trên trình duyệt.';
      }
      setNotice(`Đã lưu backend, phiên bản ${data.revision}. Chưa ghi kiểm kê.${extra}`);
    });
  }

  function checkDuplicates() {
    if (editLocked || unsynced || !review?.human_review || pending) return;
    run(async () => {
      setDuplicateCheck(null); setBoxDecisions({}); setReferencePreviews({}); setNewBoxesAccepted(false);
      const report = await queueImageWork('duplicate-check', {
        session_id: sessionId, review_id:review.review_id, revision: review.revision,
      });
      acceptComparison(report);
    });
  }

  function patchDecision(boxId, patch) {
    setNewBoxesAccepted(false);
    setBoxDecisions((current) => ({
      ...current, [boxId]: { ...current[boxId], ...patch },
    }));
  }

  function confirmInventory() {
    if (!canConfirm) return;
    const newCount = duplicateCheck.results.filter(
      (item) => boxDecisions[item.box_id]?.decision === 'new'
    ).length;
    const repeatedCount = duplicateCheck.results.length - newCount;
    if (!window.confirm(
      `Ghi ${newCount} đơn vị hàng mới vào phiên "${sessionData.name}"; `
      + `${repeatedCount} đơn vị hàng xác nhận đã ghi? `
      + 'Sau khi ghi, bản nháp sẽ khóa sửa.'
    )) return;

    run(async () => {
      const id = review.review_id;
      const targetSession = sessionId;
      let result;
      try {
        result = await api(`/api/reviews/${id}/confirm`, {
          session_id: targetSession,
          revision: review.revision,
          confirm_new_boxes: true,
          check_id: duplicateCheck.check_id,
          decisions: duplicateCheck.results.map((item) => ({
            box_id: item.box_id,
            decision: boxDecisions[item.box_id].decision,
            reference_instance_id: boxDecisions[item.box_id].decision === 'duplicate'
              ? boxDecisions[item.box_id].reference_instance_id : null,
            note: boxDecisions[item.box_id].note.trim(),
          })),
        });
      } catch (err) {
        setConfirmationUncertain(true);
        setNewBoxesAccepted(false);
        throw new Error(
          `${err.message} Bấm Tải bản nháp để kiểm tra trạng thái.`
        );
      }

      setReview((current) => ({
        ...current,
        inventory_recorded: true,
        review_status: 'recorded',
        session_id: result.receipt.session_id,
        receipt: result.receipt,
      }));
      setMode('select');
      setDrag(null);
      setNewBoxesAccepted(false);
      setUnsynced(false);
      unprotectedRef.current = false;

      await fetchSession(targetSession);
      setNotice(result.message + (result.warning ? ` ${result.warning}` : ''));
    });
  }

  const recorded = Boolean(review?.inventory_recorded);
  const dayWritable = dayReady && day?.day_id === dayId && day.status === 'open';
  const sessionWritable = Boolean(dayWritable && sessionData?.session_id === sessionId
    && sessionData.day_id === dayId && sessionData.status === 'open' && sessionData.day_status === 'open');
  const boundMismatch = Boolean(review?.session_id && review.session_id !== sessionId);
  const editLocked = busy || recorded || confirmationUncertain || !sessionWritable || boundMismatch;
  const selected = editor?.boxes.find((b) => b.box_id === selectedId);
  const original = review?.analysis.boxes.find((b) => b.box_id === selectedId);
  const pending = editor?.boxes.filter((b) => !b.reviewed).length || 0;
  const autoCount = editor?.boxes.filter(
    (b) => b.reviewed && b.note === AUTO_NOTE
  ).length || 0;
  const manualCount = editor?.boxes.filter(
    (b) => b.reviewed && b.note !== AUTO_NOTE
  ).length || 0;

  const totals = {};
  for (const box of editor?.boxes || []) {
    if (box.reviewed && validId(box.marker_id)) {
      const id = Number(box.marker_id);
      totals[id] = (totals[id] || 0) + 1;
    }
  }

  const checkCurrent = Boolean(duplicateCheck
    && duplicateCheck.review_id === review?.review_id
    && duplicateCheck.review_revision === review?.revision
    && duplicateCheck.session_id === sessionId
    && duplicateCheck.comparison_version === 'dinov2-loftr-scene-regions-v2');
  const decisionsComplete = checkCurrent && duplicateCheck.results.length === editor?.boxes.length
    && duplicateCheck.results.every((item) => {
      const choice = boxDecisions[item.box_id];
      if (!choice) return false;
      if (choice.decision === 'new') {
        return !choice.reference_instance_id
          && (!item.requires_human_decision || Boolean(choice.note?.trim()));
      }
      return choice.decision === 'duplicate'
        && item.comparisons.some((c) => c.can_mark_duplicate && c.reference_instance_id === choice.reference_instance_id)
        && Boolean(choice.note?.trim());
    })
    && new Set(duplicateCheck.results.filter((item) =>
      boxDecisions[item.box_id]?.decision === 'duplicate'
    ).map((item) => boxDecisions[item.box_id].reference_instance_id)).size
      === duplicateCheck.results.filter((item) =>
        boxDecisions[item.box_id]?.decision === 'duplicate'
      ).length;

  const canConfirm = Boolean(
    review && editor?.boxes.length && !editLocked
    && !unsynced && review.human_review
    && pending === 0 && editor.boxes.every((b) => !boxIssue(b))
    && sessionData?.session_id === sessionId
    && sessionWritable
    && (!review.session_id || review.session_id === sessionId)
    && decisionsComplete
    && newBoxesAccepted
  );

  const captureReason = busy ? 'Đang xử lý, vui lòng chờ.'
    : !camera?.ready ? 'Kết nối camera để chụp ảnh.'
    : !sessionWritable ? 'Chọn hoặc tạo phiên đang mở để chụp.' : '';
  const recordReason = recorded ? 'Đã ghi vào phiên; bản nháp khóa sửa.'
    : busy ? 'Đang xử lý, vui lòng chờ.'
    : confirmationUncertain ? 'Tải lại bản nháp để xác minh lần ghi trước.'
    : !sessionWritable || boundMismatch ? 'Cần chọn đúng phiên đang mở.'
    : !editor?.boxes.length ? 'Chưa có đơn vị hàng để ghi nhận.'
    : pending ? `Còn ${pending} đơn vị hàng cần kiểm tra.`
    : editor.boxes.some((b) => boxIssue(b)) ? 'Cần sửa mã hoặc cách gán mã.'
    : unsynced || !review?.human_review ? 'Lưu chỉnh sửa trước khi ghi vào phiên.'
    : !checkCurrent ? 'Đối chiếu với các ảnh đã ghi trong phiên.'
    : Object.values(boxDecisions).some((c) => c.decision === 'await_reference') ? 'Cần xử lý ảnh đối chiếu trước khi ghi.'
    : !decisionsComplete ? 'Chọn cách xử lý và bổ sung lý do nếu cần.'
    : !newBoxesAccepted ? 'Xác nhận các quyết định đối chiếu.' : '';
  const visibleBoxes = (editor?.boxes || []).map((box, index) => ({ box, index }))
    .filter(({ box }) => !onlyPending || !box.reviewed)
    .sort((a, b) => Number(a.box.reviewed) - Number(b.box.reviewed));

  const [traceMarker, setTraceMarker] = useState(null);

  return (
    <main className="warehouse-app" data-active-tab={activeTab}>
      <div className="current-context" hidden={activeTab === 'review'}>
        <span>Ngày: <strong>{day?.business_date || 'Chưa chọn'}</strong></span>
        <span>Phiên: <strong>{sessionData?.name || 'Chưa chọn'}</strong></span>
        <button className="secondary compact" onClick={() => setActiveTab('session')}>Chọn ngày / phiên</button>
        {review && !recorded && <span className="pill warn">{unsynced ? 'Ảnh có chỉnh sửa chưa lưu' : 'Có bản nháp chưa ghi'}</span>}
      </div>
      {(error || serverError) && <div className="alert error" role="alert">{error || serverError}</div>}
      {!taskNoticeHidden&&<ImageTaskPanel task={imageTask} busy={operationBusy} onFollow={followCurrentTask} onDismiss={hideTaskNotice} statusError={taskStatusError} resultShown={Boolean(imageTask?.state === 'completed' && (imageTask.kind === 'duplicate-check' ? checkCurrent && duplicateCheck.review_id === imageTask.review_id : review?.session_id === imageTask.session_id && Date.parse(review?.created_at) >= Date.parse(imageTask.created_at)))}/>}
      <TransientNotice message={notice} onDismiss={() => setNotice('')}/>
      <div id="panel-dashboard" role="tabpanel" aria-labelledby="nav-dashboard" hidden={activeTab !== 'dashboard'}>
        <WarehouseDashboard profile={profile} day={day} session={sessionData} days={days} pending={pending} camera={camera} onNavigate={setActiveTab}/>
      </div>
      <div id="panel-camera" className="tab-panel camera-panel" role="tabpanel" aria-labelledby="nav-camera" hidden={activeTab !== 'camera'}>
        <div className="tab-intro"><div><h2>Camera IP</h2><p>Kết nối, căn góc chụp rồi bấm Chụp & phân tích.</p></div></div>
        <section className="card camera-card">
          <div className="card-heading"><h2>Camera</h2><span className={`pill ${camera?.ready ? 'good' : ''}`}>{remoteCameraMode?(camera?.ready?'Sẵn sàng chụp':'Chờ ảnh để chụp'):(camera?.ready?'Có tín hiệu':'Chưa có tín hiệu')}</span></div>
          {remoteCameraMode?<GatewayCameraPanel key={profile?.warehouse?.warehouse_id} warehouseId={profile?.warehouse?.warehouse_id} busy={busy}/>:<details className="camera-settings" open={!camera?.ready || undefined}><summary>Cấu hình camera</summary>
            <form autoComplete="off" onSubmit={(e) => { e.preventDefault(); connect(true); }}>
              <label>Địa chỉ camera<input name="camera-stream-address" value={address} required disabled={busy} placeholder="Nhập địa chỉ camera của bạn" autoComplete="off" onChange={(e) => setAddress(e.target.value)} /></label>
              <label className="camera-auth-toggle"><input type="checkbox" checked={cameraAuth} disabled={busy} onChange={(e) => { setCameraAuth(e.target.checked); setUsername(''); setPassword(''); }} />Camera yêu cầu đăng nhập</label>
              {cameraAuth && <div className="camera-auth-fields"><p>Nhập tài khoản do ứng dụng hoặc thiết bị camera cung cấp.</p>
                <div className="credentials"><label>Tài khoản camera<input name="camera-device-user" value={username} required disabled={busy} onChange={(e) => setUsername(e.target.value)} autoComplete="off" autoCapitalize="none" spellCheck={false} data-1p-ignore data-lpignore="true" /></label>
                <label>Mật khẩu camera<input name="camera-device-secret" value={password} type="password" disabled={busy} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" data-1p-ignore data-lpignore="true" /></label></div></div>}
              <div className="actions"><button disabled={busy}>Kết nối</button><button type="button" className="secondary" disabled={busy} onClick={() => connect(false)}>Ngắt</button></div>
            </form>
          </details>}
          <div className="camera-media">
            {remoteCameraMode ? <GatewayVideoPreview warehouseId={profile?.warehouse?.warehouse_id} enabled={activeTab==='camera'} attempt={previewKey}/> : camera?.ready ? <CameraPreview key={camera.session} session={camera.session} attempt={previewKey} targetFps={camera.source_fps_reported || 30} enabled={activeTab === 'camera'} />
            : <div className="empty-state"><strong>Chưa có hình camera</strong><p>{remoteCameraMode?'Ghép cầu nối tại kho để nhận hình camera.':'Mở Cấu hình camera để nhập địa chỉ và kết nối.'}</p></div>}
          </div>
          {storageStatus?.state==='full'&&<p role="alert" className="hint">Kho đã đầy. Liên hệ admin tăng hạn mức để chụp ảnh mới; dữ liệu đã lưu vẫn dùng được.</p>}
          <div className="actions"><button disabled={Boolean(captureReason)||storageStatus?.state==='full'} onClick={capture}>{busy ? 'Đang xử lý…' : 'Chụp & phân tích'}</button>
            <button className="secondary" disabled={busy || (!remoteCameraMode && !camera?.ready)} onClick={() => setPreviewKey((n) => n + 1)}>Tải lại hình</button></div>
          <p className="hint">{captureReason || 'Chụp xong sẽ chuyển sang tab Kiểm tra ảnh; chưa cộng số lượng.'}</p>
          {!sessionWritable && <button className="secondary" onClick={() => setActiveTab('session')}>Chọn phiên để chụp</button>}
          {review && <button className="secondary" onClick={() => setActiveTab('review')}>Tiếp tục kiểm tra ảnh</button>}
          <details><summary>Mở bản nháp đã lưu</summary>
          {taskNoticeHidden&&imageTask?.state==='completed'&&<button type="button" className="secondary" disabled={operationBusy} onClick={followCurrentTask}>Mở kết quả gần nhất</button>}
          <div style={row}>
          <input style={{ ...input, width: 350 }} value={reviewId}
            placeholder="Dán review_id" aria-label="Mã bản nháp"
            disabled={busy} onChange={(e) => setReviewId(e.target.value)} />
          <button disabled={busy} onClick={loadReview}>Tải bản nháp</button>
          <button disabled={busy} onClick={restoreLocal}>
            Khôi phục dự phòng
          </button>
        </div>
</details>
        </section>
      </div>
      <div id="panel-review" className="tab-panel" role="tabpanel" aria-labelledby="nav-review" hidden={activeTab !== 'review'}>
        <div className="tab-intro"><div><h2>Kiểm tra ảnh</h2><p>Chỉ cần xử lý các kiện có cảnh báo.</p></div>
        <button className="secondary" onClick={() => setActiveTab('camera')}>Về camera</button></div>
        <div className="review-contextbar" aria-label="Tóm tắt ảnh kiểm kê">
          <strong>{sessionData?.name || 'Chưa chọn phiên'}</strong>
          <span>{day?.business_date || 'Chưa chọn ngày'}</span>
          <span className={'review-state '+(sessionWritable?'':'is-closed')}>{sessionWritable?'Đang mở':sessionData?.finalized?'Đã chốt':'Chưa sẵn sàng'}</span>
          {editor && <><i className="review-context-divider"/><span>{editor.boxes.length} kiện phát hiện</span><span>{autoCount+manualCount} đã rõ</span>{pending>0&&<span className="review-context-warning">{pending} cần kiểm tra</span>}</>}
        </div>
        <div className={`review-tab-grid ${focusImage ? 'image-expanded' : ''}`}>
          <section className="card image-card">
          <div className="card-heading"><h2>Ảnh vừa chụp</h2><button className="secondary compact" onClick={() => setFocusImage((v) => !v)}>{focusImage ? 'Hiện danh sách' : 'Phóng rộng ảnh'}</button></div>
          {review && editor ? <>
            <div className="editor-toolbar" role="group" aria-label="Công cụ chỉnh ảnh">
              <button aria-pressed={mode === 'select'} disabled={editLocked} onClick={() => { setDrag(null); setMode('select'); }}>Chọn</button>
              <button aria-pressed={mode === 'add'} disabled={editLocked || !imageReady} onClick={() => { setDrag(null); setMode('add'); }}>Thêm kiện</button>
              <button aria-pressed={mode === 'redraw'} disabled={editLocked || !imageReady || !selected} onClick={() => { setDrag(null); setMode('redraw'); }}>Vẽ lại</button>
              <button aria-pressed={mode === 'exclude'} disabled={editLocked || !imageReady} onClick={() => { setDrag(null); setMode('exclude'); setShowMasks(false); }}>Che nhãn</button>
              <button className="danger-outline" disabled={editLocked || !selected} onClick={deleteBox}>Xóa kiện</button>
            </div>
            <p className="hint">{mode === 'select' ? 'Chọn vùng trên ảnh hoặc chọn kiện ở danh sách bên cạnh.' : mode === 'exclude' ? 'Kéo bao cả nhãn mã và viền trắng. Không thay đổi số đơn vị hàng.' : 'Kéo chuột bao trọn một đơn vị hàng rồi thả.'}</p>
            <div className="review-media">          <svg ref={svgRef} viewBox={`0 0 ${review.width} ${review.height}`}
            style={{ width: '100%', display: 'block', background: '#eee',
              touchAction: 'none', cursor: mode === 'select' ? 'default' : 'crosshair' }}
            onPointerDown={pointerDown}
            onPointerMove={(e) => {
              if (drag && !editLocked) {
                const p = point(e);
                if (p) setDrag((current) => current && ({ ...current, end: p }));
              }
            }}
            onPointerUp={pointerUp} onPointerCancel={() => setDrag(null)}>
            <image key={`${review.review_id}-${imageVersion}`} href={review.image_url}
              width={review.width} height={review.height}
              onLoad={() => setImageReady(true)}
              onError={() => { setImageReady(false); setError('Không tải được ảnh.'); }} />
            {editor.boxes.map((box, index) => {
              const [x1, y1, x2, y2] = box.bounds;
              const color = !box.reviewed ? '#e5a01a' : '#2165dc';
              const markerLabel = box.marker_id === '' || box.marker_id == null ? '?' : box.marker_id;
              const caption = `${String(index+1).padStart(2, '0')} · ID ${markerLabel}`;
              const labelY = Math.max(0, y1 - 37);
              return (
                <g key={box.box_id} role="button" tabIndex={busy ? -1 : 0} aria-label={`Chọn kiện ${index+1}`} aria-pressed={box.box_id === selectedId}
                  onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && mode === 'select' && !busy) { e.preventDefault(); setSelectedId(box.box_id); } }}
                  onClick={() => {
                  if (mode === 'select' && !busy) setSelectedId(box.box_id);
                }}>
                  <rect className="unit-outline" x={x1} y={y1} width={x2 - x1} height={y2 - y1}
                    fill={box.box_id === selectedId ? '#168cff22' : 'transparent'}
                    stroke={color} strokeWidth={box.box_id === selectedId ? 7 : 3} />
                  <rect x={x1} y={labelY} width={Math.max(130,caption.length*13+18)} height={32} rx={4} fill={color} pointerEvents="none"/>
                  <text x={x1+8} y={labelY+24} fill="#fff" fontSize={22} fontWeight={500} pointerEvents="none">{caption}</text>
                </g>
              );
            })}
            {review.analysis.markers.map((m) => (
              <polygon key={m.detection_id}
                points={m.corners.map((p) => p.join(',')).join(' ')}
                fill="none" stroke="#e000d5" strokeWidth={3} pointerEvents="none" />
            ))}
            {showMasks && (editor.excluded_regions || []).map((region, index) => {
              const [x1, y1, x2, y2] = region.bounds;
              return <g key={region.region_id} pointerEvents="none">
                <rect x={x1} y={y1} width={x2 - x1} height={y2 - y1}
                  fill="#000" fillOpacity={0.8} stroke="#c026d3" strokeWidth={3} />
                <text x={x1 + 5} y={y1 + 23} fill="#fff" fontSize={20}>Che {index + 1}</text>
              </g>;
            })}
            {drag && <rect
              x={Math.min(drag.start[0], drag.end[0])}
              y={Math.min(drag.start[1], drag.end[1])}
              width={Math.abs(drag.start[0] - drag.end[0])}
              height={Math.abs(drag.start[1] - drag.end[1])}
              fill="#168cff22" stroke="#168cff" strokeWidth={4} pointerEvents="none" />}
          </svg>
</div>
            <div className="image-legend"><span className="good-text">● Đã rõ mã</span><span className="manual-text">● Đã kiểm tra</span><span className="warn-text">● Cần kiểm tra</span><span>● Khung tím: mã đọc được</span></div>
            <details><summary>Vùng che nhãn ({(editor.excluded_regions || []).length})</summary>
              <label className="check"><input type="checkbox" checked={showMasks} onChange={(e) => setShowMasks(e.target.checked)} />Hiện vùng che bổ sung</label>
              <p className="hint">Chỉ che thêm nhãn còn thiếu. Lưu chỉnh sửa để lưu vùng che.</p>          {(editor.excluded_regions || []).map((region, index) => (
            <div key={region.region_id} style={row}>
              <span>Vùng che {index + 1}: </span>
              <button disabled={editLocked} onClick={() => changeEditor((current) => ({
                ...current,
                excluded_regions: (current.excluded_regions || []).filter(
                  (item) => item.region_id !== region.region_id
                ),
              }))}>Xóa vùng che {index + 1}</button>
            </div>
          ))}

            </details>
            <details><summary>Chi tiết ảnh & thao tác bổ sung</summary>
              <p className="technical">Bản nháp: {review.review_id} · Phiên bản {review.revision}</p>
              <p>Mỗi lần chụp lấy một ảnh để kiểm tra.</p>
              <div className="actions"><button className="secondary" disabled={busy} onClick={saveLocal}>Lưu dự phòng</button>
                <button className="secondary" disabled={editLocked} onClick={acceptClearRegions}>Áp dụng AI cho bản cũ</button>
                <button className="danger-outline" disabled={busy} onClick={() => { if (window.confirm('Đóng bản nháp? Thay đổi chưa lưu sẽ mất; dữ liệu đã lưu vẫn giữ.')) clearReview(); }}>Đóng bản nháp</button></div>
                        <details style={{ marginTop: 20 }}>
            <summary>Cảnh báo AI ban đầu và ảnh tham khảo</summary>
            {review.analysis.warnings.map((text, i) => <p key={i}>{text}</p>)}
            <p>Cảnh báo gốc được giữ để đối chiếu, không phải số vùng còn chờ kiểm tra.</p>
            {review.evidence_images.map((frame) => (
              <a key={frame.frame_index} href={frame.image_url} target="_blank"
                rel="noreferrer" style={{ marginRight: 18 }}>
                Khung {frame.frame_index + 1}
              </a>
            ))}
          </details>
            </details>
          </> : <div className="empty-state"><strong>Ảnh chụp sẽ hiển thị tại đây</strong><p>Chụp & phân tích hoặc mở bản nháp đã lưu.</p></div>}
        </section>
          <section id="boxes" className="card">
        <div className="card-heading"><h2>Danh sách kiện</h2>{editor && <small>{editor.boxes.length} kiện</small>}</div>
        {review && editor ? <>
          <div className="inspector-scroll">
          {(!sessionWritable || boundMismatch) && !recorded && <p className="alert">Chọn đúng phiên đang mở để sửa và ghi nhận bản nháp.</p>}
          {confirmationUncertain && <p className="alert">Chưa rõ trạng thái ghi. Mở bản nháp đã lưu → Tải bản nháp để kiểm tra.</p>}
          <div className="box-edit-grid">
            <div><label className="check box-list-filter"><input type="checkbox" checked={onlyPending} onChange={(e) => setOnlyPending(e.target.checked)} />Chỉ hiện kiện cần kiểm tra</label>
              <div className="box-list">{visibleBoxes.map(({box, index}) => <button key={box.box_id} className={`box-item ${box.box_id === selectedId ? 'selected' : ''}`} data-reviewed={box.reviewed} style={{order:index}} disabled={busy} onClick={() => { setSelectedId(box.box_id); setMode('select'); }} aria-pressed={box.box_id === selectedId}>
                <span className="unit-thumbnail" aria-hidden="true"><svg viewBox={`${box.bounds[0]} ${box.bounds[1]} ${box.bounds[2]-box.bounds[0]} ${box.bounds[3]-box.bounds[1]}`}><image href={review.image_url} width={review.width} height={review.height}/></svg></span>
                <span className="unit-item-text"><strong>Kiện {String(index+1).padStart(2,'0')}</strong><small>{catalog.find((p) => String(p.marker_id) === String(box.marker_id))?.product_name || 'Chưa xác định'} · ID {box.marker_id === '' || box.marker_id == null ? '?' : box.marker_id}</small></span>
                {box.reviewed ? <span className="unit-ready" title={box.note === AUTO_NOTE ? 'AI chấp nhận' : 'Đã kiểm tra'}><Icon name="check"/><span className="sr-only">{box.note === AUTO_NOTE ? 'AI chấp nhận' : 'Đã kiểm tra'}</span></span> : <span className="unit-pending" title="Cần kiểm tra">›<span className="sr-only">Cần kiểm tra</span></span>}
              </button>)}</div>{!visibleBoxes.length && <p className="hint">Không có đơn vị hàng trong bộ lọc này.</p>}
            </div><div className="selected-box" key={selectedId}>          {selected && <div style={{ ...panel, background: '#f5f8fc' }}>
            <h3>Thông tin kiện {String(editor.boxes.findIndex((b) => b.box_id === selectedId)+1).padStart(2,'0')}</h3>
            {!selected.reviewed && original?.warnings.map((text, i) => (
              <p key={i} className="unit-warning">{text}</p>
            ))}
            <label className="unit-product-field">Loại hàng
              <select aria-label="Loại hàng" value={selected.marker_id} disabled={editLocked} onChange={(e) => patchBox(selectedId, {marker_id:e.target.value})}>
                <option value="">Chọn loại hàng</option>
                {selected.marker_id !== '' && !catalog.some((p) => String(p.marker_id) === String(selected.marker_id)) && <option value={selected.marker_id}>ID {selected.marker_id} · Chưa có trong danh mục</option>}
                {catalog.filter((p) => p.is_active !== false || String(p.marker_id) === String(selected.marker_id)).map((p) => <option key={p.marker_id} value={p.marker_id}>{p.product_name} · ID {p.marker_id}</option>)}
              </select>
            </label>
            <label className="check reviewed-control"><input type="checkbox" checked={Boolean(selected.reviewed)} disabled={editLocked} onChange={(e) => e.target.checked ? markReviewed() : patchBox(selectedId, {})}/>Đã kiểm tra kiện này</label>
            <details className="review-notes"><summary>Ghi chú hiệu chỉnh</summary>
              <textarea aria-label="Ghi chú / căn cứ hiệu chỉnh" style={{ ...input, display: 'block', width: '100%' }}
                value={cleanNote(selected.note)} disabled={editLocked}
                onChange={(e) => patchBox(selectedId, { note: e.target.value })} />
            </details>
            {selected.marker_id !== '' && boxIssue(selected) && <p style={warning}>{boxIssue(selected)}</p>}
          </div>}

              {!selected && <p className="hint">Chọn một đơn vị hàng để xem hoặc chỉnh mã.</p>}
              <details>          <summary>Gán mã vào đơn vị hàng</summary>
          <p>Khung tím là marker đọc được. Đổi cách gán sẽ yêu cầu kiểm tra lại vùng bị ảnh hưởng.</p>
          {review.analysis.markers.map((m) => (
            <div key={m.detection_id} style={row}>
              <span>{m.detection_id} · ID {m.marker_id}</span>
              <select style={input} disabled={editLocked}
                value={editor.assignments[m.detection_id] || ''}
                onChange={(e) => assignMarker(m, e.target.value)}>
                <option value="">Không gán / bỏ qua</option>
                {editor.boxes.map((box, index) => (
                  <option key={box.box_id} value={box.box_id}>Đơn vị hàng {index + 1}</option>
                ))}
              </select>
            </div>
          ))}
</details>
            </div>
          </div>
          <div className="record-bar">
            {returnReviewId && returnReviewId !== review.review_id && <div className="actions"><button className="secondary" disabled={busy} onClick={() => openReferenceReview(returnReviewId, true)}>Quay lại ảnh vừa chụp</button></div>}
            <div className="counts-inline">{Object.entries(totals).map(([id, count]) => <span key={id}><strong>{catalog.find((p) => String(p.marker_id) === id)?.product_name || `ID ${id}`}</strong>: {count} đơn vị hàng</span>)}</div>
            {recorded ? <p className="alert success">Đã ghi {review.receipt?.box_count ?? editor.boxes.length} đơn vị hàng mới; {review.receipt?.duplicate_box_count ?? 0} đơn vị hàng trùng. Bản nháp khóa sửa.</p> : <>
              <p className="hint">Lưu chỉnh sửa, kiểm tra trùng rồi ghi vào phiên.</p>
              <div className="actions"><button className="secondary" disabled={editLocked || unsynced || !review.human_review || pending > 0} onClick={checkDuplicates}>Kiểm tra trùng</button>
                <span className="hint">{unsynced ? 'Có thay đổi chưa lưu' : review.human_review ? 'Đã lưu chỉnh sửa' : 'Chưa lưu chỉnh sửa'}</span></div>
              {checkCurrent && <div className="duplicate-review" aria-label="Đối chiếu hàng">
                <h3>Kiểm tra trùng</h3>
                <p className="hint">Đã xét {duplicateCheck.reference_image_count ?? duplicateCheck.capture_count ?? '?'} ảnh trước trong phiên.</p>
                {!!duplicateCheck.history_issues?.length && <details><summary>Có ảnh chưa đối chiếu được</summary>
                  {duplicateCheck.history_issues.map((message, i) => <p key={i}>{message}</p>)}
                </details>}
                {duplicateCheck.results.map((item, index) => {
                  const choice = boxDecisions[item.box_id] || {};
                  const suspect = item.comparisons.filter((c) => c.outcome === 'suspected_duplicate');
                  const unresolved = item.comparisons.filter((c) => c.outcome === 'not_evaluable');
                  const selectedReference = item.comparisons.find((c) => c.comparison_id === referencePreviews[item.box_id]);
                  const picked = selectedReference || suspect.find((c) => c.can_mark_duplicate)
                    || suspect[0] || unresolved.find((c) => c.can_mark_duplicate) || unresolved[0] || item.comparisons[0];
                  const surfaceProblem = item.requires_human_decision && !suspect.length && picked?.evaluation_status === 'insufficient_surface';
                  const label = (c) => `${c.reference_instance_id ? 'Đã ghi' : 'Chưa ghi'} · ${c.reference_recorded_at ? new Date(c.reference_recorded_at).toLocaleTimeString('vi-VN') : ''} · ${c.reference_box_id}`;
                  const sameSelected = choice.decision === 'duplicate' || choice.decision === 'await_reference';
                  return <div className="duplicate-item" key={item.box_id}>
                    <div className="duplicate-item-heading">
                      <strong>Kiện {index + 1} · ID {item.marker_id}</strong>
                      <span className={`pill ${suspect.length || item.requires_human_decision ? 'warn' : ''}`}>
                        {choice.decision === 'duplicate' ? 'Cùng kiện · +0' : surfaceProblem ? 'Ảnh chưa đủ bề mặt' : suspect.length ? 'Nghi trùng' : item.requires_human_decision ? 'Cần kiểm tra' : 'Hàng mới · +1 khi ghi'}
                      </span>
                    </div>
                    {surfaceProblem && <div className="alert" role="status">
                      <p><strong>Chưa đối chiếu được ảnh này.</strong> {picked.note}</p>
                      <p>Nếu chỉ chụp nhãn mã, bỏ vùng này và chụp lại cả kiện. Marker riêng không đại diện cho một kiện hàng.</p>
                      <a href={duplicateCheck.image_url} target="_blank" rel="noopener noreferrer">Xem ảnh mới</a>
                      <div className="actions">
                        <button type="button" className="secondary" disabled={editLocked} onClick={() => {
                          if (!window.confirm('Bỏ vùng này vì không phải một kiện hàng? Chưa thay đổi số lượng đã ghi.')) return;
                          changeEditor((current) => ({
                            ...current, boxes: current.boxes.filter((box) => box.box_id !== item.box_id),
                            assignments: Object.fromEntries(Object.entries(current.assignments).map(([marker, owner]) => [marker, owner === item.box_id ? '' : owner])),
                          }));
                          setSelectedId('');
                          setMode('select');
                          setNotice('Đã bỏ vùng. Lưu chỉnh sửa và kiểm tra lại nếu ảnh còn kiện khác; nếu không có hàng thì chụp ảnh khác.');
                        }}>Bỏ vùng không phải hàng</button>
                        {!choice.manual_override && <button type="button" className="secondary" disabled={editLocked} onClick={() => patchDecision(item.box_id, { manual_override: true })}>Ảnh vẫn có kiện: kiểm tra thủ công</button>}
                      </div>
                    </div>}
                    {picked && item.requires_human_decision && !surfaceProblem ? <details open>
                      <summary>{suspect.length ? 'Đối chiếu kiện nghi trùng' : 'Chọn cách tính kiện này'}</summary>
                      {item.comparisons.length > 1 && <label>Ảnh đối chiếu
                        <select disabled={editLocked} value={picked.comparison_id} onChange={(e) => {
                          setReferencePreviews((v) => ({ ...v, [item.box_id]: e.target.value }));
                          patchDecision(item.box_id, { decision: '', reference_instance_id: null, note: '' });
                        }}>
                          {item.comparisons.map((c) => <option key={c.comparison_id} value={c.comparison_id}>{label(c)}{c.outcome === 'suspected_duplicate' ? ' · Nghi trùng' : c.outcome === 'new_candidate' ? ' · Khác rõ' : ' · Chưa rõ'}</option>)}
                        </select>
                      </label>}
                      <div className="duplicate-evidence-grid">
                        <BoxEvidence label="Ảnh mới" url={duplicateCheck.image_url} bounds={item.bounds} width={duplicateCheck.image_width} height={duplicateCheck.image_height} />
                        <BoxEvidence label={`Ảnh trước · ${picked.reference_instance_id ? 'Đã ghi' : 'Chưa ghi'}`} url={picked.reference_image_url} bounds={picked.reference_bounds} width={picked.reference_width} height={picked.reference_height} />
                      </div>
                    </details> : !item.comparisons.length && <p className="hint">Chưa có ảnh trước trong phiên.</p>}
                    {!item.requires_human_decision && choice.decision === 'new' && <p className="hint">Đã tự chọn hàng mới. Bấm Ghi vào phiên để lưu.</p>}
                    {!item.requires_human_decision && picked && <details>
                      <summary>Đổi cách tính</summary>
                      <label>Đối chiếu với
                        <select disabled={editLocked} value={picked.comparison_id} onChange={(e) => {
                          setReferencePreviews((v) => ({ ...v, [item.box_id]: e.target.value }));
                          patchDecision(item.box_id, { decision: 'new', reference_instance_id: null, note: '' });
                        }}>
                          {item.comparisons.map((c) => <option key={c.comparison_id} value={c.comparison_id}>{label(c)}</option>)}
                        </select>
                      </label>
                      <div className="duplicate-evidence-grid">
                        <BoxEvidence label="Ảnh mới" url={duplicateCheck.image_url} bounds={item.bounds} width={duplicateCheck.image_width} height={duplicateCheck.image_height} />
                        <BoxEvidence label="Ảnh trước" url={picked.reference_image_url} bounds={picked.reference_bounds} width={picked.reference_width} height={picked.reference_height} />
                      </div>
                      <button type="button" className="secondary" disabled={editLocked} onClick={() => patchDecision(item.box_id, picked.can_mark_duplicate
                        ? { decision: 'duplicate', reference_instance_id: picked.reference_instance_id, note: 'Người giám sát xác nhận hai ảnh là cùng kiện.' }
                        : { decision: 'await_reference', reference_instance_id: null, note: '' })}>Cùng kiện{picked.can_mark_duplicate ? ' · +0' : ''}</button>
                    </details>}

                    <div hidden={surfaceProblem && !choice.manual_override}>
                    <div className="actions" role="group" aria-label={`Xử lý kiện ${index + 1}`}>
                      <button type="button" className={choice.decision === 'new' ? '' : 'secondary'} aria-pressed={choice.decision === 'new'} disabled={editLocked}
                        onClick={() => patchDecision(item.box_id, { decision: 'new', reference_instance_id: null, note: choice.decision === 'new' ? choice.note : item.requires_human_decision ? 'Người giám sát xác nhận đây là kiện riêng biệt.' : '' })}>Hàng mới · +1</button>
                      {item.requires_human_decision && <button type="button" className={sameSelected ? '' : 'secondary'} aria-pressed={sameSelected} disabled={editLocked || !picked}
                        onClick={() => patchDecision(item.box_id, picked.can_mark_duplicate
                          ? { decision: 'duplicate', reference_instance_id: picked.reference_instance_id, note: 'Người giám sát xác nhận hai ảnh là cùng kiện.' }
                          : { decision: 'await_reference', reference_instance_id: null, note: '' })}>Cùng kiện{picked?.can_mark_duplicate ? ' · +0' : ''}</button>}
                    </div>
                    {choice.decision === 'await_reference' && <div className="alert" role="status">
                      {!picked?.reference_instance_id ? <>
                        <p>Ảnh trước chưa ghi. Ghi ảnh trước làm mốc rồi đối chiếu lại ảnh này.</p>
                        <button type="button" className="secondary" disabled={editLocked} onClick={() => openReferenceReview(picked.reference_review_id)}>Mở ảnh trước</button>
                      </> : <p>Mã loại hàng khác nhau. Kiểm tra lại mã trước khi gộp.</p>}
                    </div>}
                    {choice.decision === 'new' && item.requires_human_decision && <label>Lý do tính mới
                      <input disabled={editLocked} maxLength={500} value={choice.note || ''}
                        onChange={(e) => patchDecision(item.box_id, { note: e.target.value })} placeholder="Ví dụ: hai kiện khác nhau…" />
                    </label>}
                    </div>
                    {picked && <details>
                      <summary>Chi tiết đối chiếu</summary>
                      <p>{picked.note}</p>
                      {surfaceProblem && <div className="duplicate-evidence-grid">
                        <BoxEvidence label="Ảnh mới" url={duplicateCheck.image_url} bounds={item.bounds} width={duplicateCheck.image_width} height={duplicateCheck.image_height} />
                        <BoxEvidence label="Ảnh trước" url={picked.reference_image_url} bounds={picked.reference_bounds} width={picked.reference_width} height={picked.reference_height} />
                      </div>}
                      {picked.appearance && <p className="hint">DINOv2: ảnh gốc {picked.appearance.original.toFixed(3)} · che marker {picked.appearance.masked.toFixed(3)}.</p>}
                      {['original', 'masked'].map((branch) => {
                        const metric = picked.branches?.[branch];
                        return <p className="hint" key={branch}><strong>{branch === 'original' ? 'Ảnh gốc' : 'Che marker'}: </strong>
                          {!metric?.comparison_performed ? 'Chưa ghép điểm' : `${metric.mutual_matches} điểm khớp; ${metric.inliers} phù hợp hình học; độ phủ ${(100 * metric.coverage_new).toFixed(1)}% / ${(100 * metric.coverage_recorded).toFixed(1)}%.`}
                        </p>;
                      })}
                      <p className="hint">Điểm tương đồng và độ phủ không phải xác suất cùng kiện.</p>
                      {picked.mask_warning && <p className="hint">{picked.mask_warning}</p>}
                      <p className="hint">Bản nháp: {picked.reference_review_id} · {label(picked)}</p>
                    </details>}
                  </div>;
                })}
                <p className="hint">Thêm {duplicateCheck.results.filter((item) => boxDecisions[item.box_id]?.decision === 'new').length} kiện · Trùng {duplicateCheck.results.filter((item) => boxDecisions[item.box_id]?.decision === 'duplicate').length} kiện</p>
              </div>}
              <label className="check"><input type="checkbox" checked={newBoxesAccepted} disabled={editLocked || !decisionsComplete} onChange={(e) => setNewBoxesAccepted(e.target.checked)} />Xác nhận cách tính số lượng.</label>
              
            </>}
          </div>
          </div>
          <div className="review-primary-actions">
            <p className="hint">{recorded ? 'Đã ghi vào phiên. Bản nháp khóa sửa.' : recordReason || 'Sẵn sàng ghi vào phiên.'}</p>
            {!recorded && <div className="actions"><button className="secondary" disabled={editLocked} onClick={saveBackend}>Lưu hiệu chỉnh</button><button disabled={!canConfirm} onClick={confirmInventory}>Ghi vào phiên</button></div>}
          </div>
        </> : <p className="hint">Danh sách kiện sẽ xuất hiện sau khi phân tích ảnh.</p>}
      </section>
        </div>
      </div>
      <div id="panel-session" className="tab-panel" role="tabpanel" aria-labelledby="nav-session" hidden={activeTab !== 'session'}>
        <div className="tab-intro"><div><h2>Quản lý ngày & phiên kiểm kê</h2><p>Mỗi phiên ghi nhận lại toàn bộ tồn kho trong phạm vi kiểm kê.</p></div></div>
        <div className="session-tab-grid"><section className="card context-card" aria-label="Chọn ngày và phiên">
        <div className="card-heading"><h2>Chọn ngày & phiên</h2></div>
        <div className="context-selectors">
          <label>Ngày kiểm kê<select value={dayId} disabled={busy || !dayReady} onChange={(e) => selectDay(e.target.value)}>
            <option value="">Chọn ngày</option>{days.map((d) => <option key={d.day_id} value={d.day_id}>{d.business_date} · {d.status === 'closed' ? 'Đã chốt' : 'Đang mở'}</option>)}
          </select></label>
          <label>Phiên kiểm kê<select value={sessionId} disabled={busy || !dayReady} onChange={(e) => selectSession(e.target.value)}>
            <option value="">Chọn phiên</option>{sessions.map((v) => <option key={v.session_id} value={v.session_id}>#{v.session_order} · {v.name} · {v.status === 'closed' ? 'Đã chốt' : 'Đang mở'}</option>)}
          </select></label>
          <button className="secondary" disabled={busy} onClick={refreshSessions}>Làm mới</button>
        </div>
                {dayWritable && day.business_date === today && !sessions.some((s) => !s.finalized_at) && <form onSubmit={createSession} style={row}>
          <input style={input} value={sessionName} maxLength={120} required placeholder="Tên phiên mới"
            aria-label="Tên phiên mới" disabled={busy} onChange={(e) => setSessionName(e.target.value)} />
          <button disabled={busy || !sessionName.trim()}>Tạo phiên</button>
        </form>}

        <details className="day-setup" open={!day || undefined}><summary>Tồn đầu & mở ngày mới</summary>        {!dayReady && <p style={warning}>Đang tải hoặc chưa xác minh được trạng thái. Nếu có lỗi, bấm Làm mới.</p>}
        {day && <>
          <p>Ngày <strong>{day.business_date}</strong> · <strong>{day.status === 'closed' ? 'Đã chốt — chỉ xem dữ liệu' : 'Đang mở'}</strong></p>
          <p>Tồn đầu ngày: <strong>{day.opening_total_boxes} đơn vị hàng</strong></p>
          <details><summary>Danh sách tồn đầu ngày</summary>
            {day.opening_items.map((p) => <p key={p.marker_id}>{p.product_name} · ID {p.marker_id}: {p.quantity} đơn vị hàng</p>)}
          </details>
          {day.status === 'open' && day.business_date !== today && <p style={warning}>
            Đây là ngày cũ chưa chốt. Hoàn tất ngày này trước khi mở ngày hôm nay; không tạo phiên mới cho ngày cũ.
          </p>}
        </>}
        {dayReady && !days.some((d) => d.status === 'open') && !days.some((d) => d.business_date === today) && <div style={{ marginTop: 20 }}>
          <h3>Mở ngày {today}</h3>
          {!days.length ? <>
            <p>Khai báo tồn đầu cho từng loại, nhập 0 nếu không có hàng.</p>
            {catalog.map((p) => <label key={p.marker_id} style={row}>
              {p.product_name} · ID {p.marker_id}
              <input style={{ ...input, width: 110 }} type="number" min="0" step="1" disabled={busy}
                value={openingCounts[p.marker_id] ?? '0'}
                onChange={(e) => setOpeningCounts((v) => ({ ...v, [p.marker_id]: e.target.value }))} />
            </label>)}
          </> : <p>Tồn đầu tự kế thừa báo cáo ngày đã chốt gần nhất.</p>}
          <button disabled={busy} onClick={createDay}>Mở ngày mới</button>
        </div>}
        {dayReady && days.some((d) => d.business_date === today && d.status === 'closed') && <p>
          Ngày {today} đã chốt, không thể mở lại. Sang ngày mới bấm Làm mới để mở ngày mới.
        </p>}
</details>
      </section><section id="session" className="card"><div className="card-heading"><h2>Phiên kiểm kê</h2><span className="pill">{sessionData?.finalized ? 'Đã chốt' : sessionData ? 'Đang mở' : 'Chưa chọn'}</span></div>        {!sessions.length && dayReady && <p>Chưa có phiên trong ngày đang chọn.</p>}
        {sessionError && <p role="alert" style={warning}>{sessionError}</p>}
        {sessionData && <>
          <h3>{sessionData.name} · {sessionData.finalized ? 'Đã chốt' : 'Đang mở'}</h3>
          <p>Mốc: {sessionData.reference_type === 'opening_stock' ? 'Tồn đầu ngày' : 'Phiên đã chốt liền trước'}.
            {' '}Bản nháp đã ghi: <strong>{sessionData.receipt_count}</strong>.
            {' '}Số đơn vị hàng: <strong>{sessionData.recorded_total_boxes}</strong>.</p>
          <StockTable items={sessionData.items} />
          <p>{sessionData.message}</p>
          {sessionWritable && <div style={{ ...panel, background: '#f5f8fc' }}>
            <h3>Kết thúc phiên</h3>
            <p>Chốt toàn bộ tồn kho của phiên; loại chưa ghi nhận sẽ thành 0. Mỗi phiên đếm lại tồn, không cộng tồn của các phiên với nhau.</p>
            <label style={{ display: 'block', margin: '12px 0' }}>
              <input type="checkbox" checked={coverageAccepted} disabled={busy}
                onChange={(e) => setCoverageAccepted(e.target.checked)} />{' '}
              Tôi đã kiểm kê đủ phạm vi kho, ghi nhận các ảnh cần thiết và kiểm tra không đếm trùng đơn vị hàng trong phiên.
            </label>
            {sessionData.total_boxes === 0 && <label style={{ display: 'block', margin: '12px 0' }}>
              <input type="checkbox" checked={emptyAccepted} disabled={busy} onChange={(e) => setEmptyAccepted(e.target.checked)} />{' '}
              Tôi xác nhận kho không còn hàng, chốt tồn bằng 0.
            </label>}
            {review && !recorded && <p style={warning}>Còn bản nháp đang xem chưa ghi nhận. Ghi nhận hoặc đóng bản nháp trước khi chốt phiên.</p>}
            <button disabled={busy || !coverageAccepted || (sessionData.total_boxes === 0 && !emptyAccepted) || Boolean(review && !recorded)} onClick={closeSession}>
              Chốt phiên
            </button>
          </div>}
        </>}
</section></div>
      </div>
      <div id="panel-report" className="tab-panel report-panel" role="tabpanel" aria-labelledby="nav-report" hidden={activeTab !== 'report'}>
        <div className="tab-intro"><div><h2>Báo cáo & chốt ngày</h2><p>Đối chiếu tồn đầu, tăng, giảm và tồn cuối của ngày đang chọn.</p></div>
          <button className="secondary" onClick={() => setActiveTab('session')}>Chọn ngày / phiên</button></div>
        <section id="report" className="card"><div className="card-heading"><h2>Báo cáo ngày</h2></div>        <p>Tổng tăng/giảm là tổng chênh lệch giữa các phiên liên tiếp, không phải tổng nhập/xuất thực tế.</p>
        <button disabled={busy || !dayReady || !dayId || !sessions.length || sessions.some((s) => !s.finalized_at)} onClick={previewDayReport}>
          {day?.status === 'closed' ? 'Tải lại báo cáo' : 'Xem báo cáo'}
        </button>
        {sessions.some((s) => !s.finalized_at) && <p>Cần kết thúc phiên đang mở trước khi tổng hợp ngày.</p>}
        {dayReport && <>
          <h3>Ngày {dayReport.business_date} · {dayReport.status === 'closed' ? 'Báo cáo đã chốt' : 'Bản xem trước'}</h3>
          <StockTable items={dayReport.items} daily onTrace={setTraceMarker} />
          <p><strong>Tồn đầu: {dayReport.opening_total_boxes} · Tăng: {dayReport.total_increase} · Giảm: {dayReport.total_decrease} · Tồn cuối: {dayReport.closing_total_boxes}</strong></p>
          <p>{dayReport.session_count} phiên. {dayReport.closed_at && `Chốt lúc ${new Date(dayReport.closed_at).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })}.`}</p>
          <ReportExport report={dayReport} dayId={dayId} warehouseId={storageNamespace} active={activeTab==='report'} />
          <ReportTrace key={`${dayId}:${storageNamespace}:${traceMarker}`} dayId={dayReport.day_id} markerId={traceMarker} active={activeTab==='report'} onClose={() => setTraceMarker(null)} />
          {dayWritable && dayReport.status === 'open' && <>
            <label style={{ display: 'block', margin: '16px 0' }}>
              <input type="checkbox" checked={dayCloseAccepted} disabled={busy}
                onChange={(e) => setDayCloseAccepted(e.target.checked)} />{' '}
              Tôi đã kiểm tra báo cáo và đồng ý khóa ngày này, không tạo thêm phiên hoặc sửa số lượng.
            </label>
            <button disabled={busy || !dayCloseAccepted || Boolean(review && !recorded)} onClick={closeDay}>Chốt ngày</button>
          </>}
        </>}
</section>
      </div>
      <div id="panel-catalog" className="tab-panel" role="tabpanel" aria-labelledby="nav-catalog" hidden={activeTab !== 'catalog'}>
        <CatalogPanel onChanged={async () => {
          const data = await api('/api/inventory/days');
          setCatalog(data.catalog);
        }} />
      </div>
      <footer className="app-footer">Kiểm kê bằng camera · Số liệu tăng/giảm được tính giữa các phiên đã chốt.</footer>
    </main>
  );
}
