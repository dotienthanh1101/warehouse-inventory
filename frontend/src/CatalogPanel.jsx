import { apiFetch } from './authClient';
import { useEffect, useRef, useState } from 'react';

async function request(path, method = 'GET', body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await apiFetch(`/api/catalog${path}`, {
      method, signal: controller.signal, cache: 'no-store',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Yêu cầu thất bại (${response.status}).`);
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Hết thời gian chờ. Bấm Làm mới để kiểm tra trước khi gửi lại.');
    throw error;
  } finally { clearTimeout(timer); }
}

const blank = { marker_id: '', product_name: '' };

function productCode(markerId) {
  const raw = String(markerId);
  if (!/^\d+$/.test(raw) || Number(raw) < 0 || Number(raw) > 249) return '';
  return `SP-${String(Number(raw)).padStart(3, '0')}`;
}

export default function CatalogPanel({ onChanged }) {
  const [listing, setListing] = useState(null);
  const [form, setForm] = useState(blank);
  const [editing, setEditing] = useState(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('active');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const lock = useRef(false);

  async function load() {
    const data = await request('/products');
    setListing(data);
    return data;
  }

  useEffect(() => {
    let alive = true;
    request('/products').then((data) => {
      if (!alive) return;
      setListing(data);
      setForm({ ...blank, marker_id: data.suggested_marker_id ?? '' });
    }).catch((err) => { if (alive) setError(err.message); });
    return () => { alive = false; };
  }, []);

  async function run(task) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    try { await task(); } catch (err) { setError(err.message); }
    finally { lock.current = false; setBusy(false); }
  }

  async function changed(message) {
    setNotice(message);
    const data = await load();
    setEditing(null);
    setForm({ ...blank, marker_id: data.suggested_marker_id ?? '' });
    try { await onChanged(); }
    catch { setError('Danh mục đã lưu, nhưng chưa tải lại tên hàng ở các tab khác. Bấm Làm mới trong Phiên kiểm kê.'); }
  }

  function submit(event) {
    event.preventDefault();
    run(async () => {
      const fields = { product_name: form.product_name.trim() };
      if (editing) {
        await request(`/products/${editing.marker_id}`, 'PUT', {
          ...fields, revision: editing.catalog_revision,
        });
        await changed('Đã lưu thông tin loại hàng.');
      } else {
        if (!/^\d+$/.test(String(form.marker_id)) || Number(form.marker_id) < 1 || Number(form.marker_id) > 249) {
          throw new Error('ID mới phải từ 01 đến 249.');
        }
        await request('/products', 'POST', { ...fields, marker_id: Number(form.marker_id) });
        await changed('Đã thêm loại hàng. Bạn có thể tải nhãn để sử dụng; số lượng tồn kho chưa thay đổi.');
      }
    });
  }

  function remove(product) {
    if (!window.confirm(`Xóa ID ${product.marker_label} — ${product.product_name}? Nếu đã có dữ liệu tham chiếu, hệ thống sẽ ngừng sử dụng và giữ lịch sử.`)) return;
    run(async () => {
      const result = await request(`/products/${product.marker_id}?revision=${product.catalog_revision}`, 'DELETE');
      await changed(result.message);
    });
  }

  function activate(product) {
    run(async () => {
      await request(`/products/${product.marker_id}/activate`, 'POST', { revision: product.catalog_revision });
      await changed('Đã kích hoạt lại đúng loại hàng này.');
    });
  }

  function edit(product) {
    setEditing(product);
    setForm({ marker_id: product.marker_id, product_name: product.product_name });
    setError(''); setNotice('');
  }

  const query = search.trim().toLocaleLowerCase('vi-VN');
  const products = (listing?.products || []).filter((product) => {
    if (filter === 'active' && !product.is_active) return false;
    if (filter === 'inactive' && product.is_active) return false;
    return `${product.marker_label} ${product.product_code} ${product.product_name}`.toLocaleLowerCase('vi-VN').includes(query);
  });

  return <section className="catalog-workspace">
    <div className="tab-intro"><div><h2>Danh mục hàng</h2><p>Mỗi ID marker đại diện một loại hàng; một loại có thể có nhiều thùng.</p></div>
      <button className="secondary" disabled={busy} onClick={() => run(async () => { await load(); await onChanged(); })}>Làm mới</button>
    </div>
    {error && <p className="alert" role="alert">{error}</p>}
    {notice && <p className="alert success" role="status">{notice}</p>}
    <div className="catalog-grid">
      <section className="card catalog-form"><h3>{editing ? `Sửa ID ${String(editing.marker_id).padStart(2, '0')}` : 'Thêm loại hàng'}</h3>
        <form onSubmit={submit}>
          <label>ID marker
            <input type="number" min="1" max="249" step="1" required value={form.marker_id}
              disabled={busy || Boolean(editing)} onChange={(e) => setForm({ ...form, marker_id: e.target.value })} />
          </label>
          {!editing && <p className="hint">ID trống tiếp theo: {listing?.suggested_marker_id == null ? 'Chưa có' : String(listing.suggested_marker_id).padStart(2, '0')}. ID ngừng dùng vẫn được giữ cho hàng cũ.</p>}
          <label>Mã sản phẩm · tự động<input readOnly value={productCode(form.marker_id)}
            aria-label="Mã sản phẩm tự động theo ID" /></label>
          <p className="hint">ID 01 → SP-001; ID 23 → SP-023. Mã sản phẩm được tạo cố định theo ID.</p>
          <label>Tên loại hàng<input required maxLength={120} placeholder="VD: Điện trở 1 kΩ" value={form.product_name}
            disabled={busy} onChange={(e) => setForm({ ...form, product_name: e.target.value })} /></label>
          {editing && <p className="hint">Chỉ sửa tên để mô tả đúng loại hàng hiện tại; không dùng ID cũ cho loại khác.</p>}
          <div className="actions"><button disabled={busy || !listing}>{editing ? 'Lưu thông tin' : 'Thêm hàng'}</button>
            {editing && <button type="button" className="secondary" disabled={busy} onClick={() => { setEditing(null); setForm({ ...blank, marker_id: listing?.suggested_marker_id ?? '' }); }}>Hủy sửa</button>}
          </div>
        </form>
        <p className="hint">Nhãn tải về là mã OpenCV chuẩn, có viền trắng. Tạo danh mục không cộng số lượng tồn kho.</p>
      </section>
      <section className="card catalog-list">
        <div className="card-heading"><h3>Các loại hàng</h3><span className="pill good">{listing?.active_count ?? 0} đang dùng / {listing?.total ?? 0} đã đăng ký</span></div>
        <div className="catalog-filters"><input aria-label="Tìm hàng" placeholder="Tìm ID, mã sản phẩm, tên hàng…" value={search} onChange={(e) => setSearch(e.target.value)} />
          <select aria-label="Trạng thái loại hàng" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="active">Đang dùng</option><option value="inactive">Ngừng sử dụng</option><option value="all">Tất cả</option>
          </select></div>
        <div className="catalog-table"><table><thead><tr><th>ID</th><th>Mã sản phẩm</th><th>Tên hàng</th><th>Trạng thái</th><th>Thao tác</th></tr></thead>
          <tbody>{products.map((product) => <tr key={product.marker_id}>
            <td><strong>{product.marker_label}</strong></td><td>{product.product_code}</td><td>{product.product_name}</td>
            <td>{product.is_active ? 'Đang dùng' : 'Ngừng dùng'}</td>
            <td><div className="catalog-actions"><button className="secondary compact" disabled={busy} onClick={() => edit(product)}>Sửa</button>
              {product.is_active ? <>
                <a className="catalog-download" href={`/api/catalog/products/${product.marker_id}/marker.png`} download>Tải nhãn</a>
                <button className="secondary compact" disabled={busy} onClick={() => remove(product)}>Xóa</button>
              </> : <button className="secondary compact" disabled={busy} onClick={() => activate(product)}>Dùng lại</button>}
            </div></td>
          </tr>)}</tbody></table></div>
        {listing && !products.length && <p className="hint">Không có hàng phù hợp bộ lọc.</p>}
        {!listing && !error && <p className="hint">Đang tải danh mục…</p>}
      </section>
    </div>
  </section>;
}
