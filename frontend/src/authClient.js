let csrfToken = '';
let warehouseId = '';
export function setAuthToken(value, warehouse = '') { csrfToken = value || ''; warehouseId = warehouse; }

export async function apiFetch(path, options = {}) {
  if (typeof path !== 'string' || !path.startsWith('/api/')) throw new Error('Đường dẫn API không hợp lệ.');
  const headers = new Headers(options.headers || {});
  if (warehouseId && !path.startsWith('/api/auth/')) headers.set('X-Warehouse-ID', warehouseId);
  if (!['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase()) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken);
  }
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  if ((response.status === 401 || response.status === 409 && response.headers.get('X-Auth-Context-Changed') === '1') && csrfToken) window.dispatchEvent(new Event('warehouse-auth-expired'));
  return response;
}

export async function authRequest(path, body, options = {}) {
  const response = await apiFetch(path, {
    method: body === undefined ? 'GET' : 'POST', cache: 'no-store', signal: options.signal,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail
      : Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('; ') : 'Không thực hiện được yêu cầu.';
    const error = new Error(message); error.status = response.status; throw error;
  }
  return data;
}
