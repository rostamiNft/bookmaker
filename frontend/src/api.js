// Thin fetch wrapper. All URLs are relative so the app works behind any proxy/host.
export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }
}

function token() {
  try { return localStorage.getItem('bm_token') || '' } catch { return '' }
}

export async function api(path, { method = 'GET', body, form } = {}) {
  const headers = {}
  const t = token()
  if (t) headers.Authorization = `Bearer ${t}`
  let payload
  if (form) payload = form
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  const res = await fetch(path, { method, headers, body: payload })
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) throw new ApiError(res.status, data && data.detail !== undefined ? data.detail : data)
  return data
}

export function withToken(url) {
  const t = token()
  if (!url || !t) return url
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t)
}
