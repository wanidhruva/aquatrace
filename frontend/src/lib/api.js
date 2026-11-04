const TIMEOUT_MS = 15000

export const BACKEND_URL =
  import.meta.env.VITE_API_URL ||
  (typeof window !== 'undefined' && window.location.hostname.endsWith('.amplifyapp.com')
    ? 'https://d3snenpbe1z846.cloudfront.net'
    : '')

export const buildUrl = (path) => {
  if (!path) return ''
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  if (!BACKEND_URL) return path
  return `${BACKEND_URL}${path.startsWith('/') ? '' : '/'}${path}`
}

function withTimeout(path, init = {}) {
  const url = buildUrl(path)
  const ctrl = new AbortController()
  const id = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  return fetch(url, { ...init, signal: ctrl.signal }).finally(() => clearTimeout(id))
}

async function parse(r) {
  if (!r.ok) {
    let body = null
    try {
      body = await r.json()
    } catch {
      /* non-JSON error body */
    }
    return Promise.reject(Object.assign(new Error(r.statusText || `HTTP ${r.status}`), { status: r.status, body }))
  }
  // 204 No Content / empty bodies are not JSON — treat as success with null.
  const len = r.headers.get('content-length')
  if (r.status === 204 || len === '0') return null
  const text = await r.text()
  if (!text.trim()) return null
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`backend returned non-JSON response for ${r.url} (status ${r.status})`)
  }
}

function timed(path, init) {
  return withTimeout(path, init)
    .then(parse)
    .catch((e) => {
      if (e?.name === 'AbortError') {
        throw new Error(`request timed out after ${TIMEOUT_MS / 1000}s: ${path}`)
      }
      throw e
    })
}

export const get = (path) => timed(path)
export const post = (path, body) =>
  timed(path, {
    method: 'POST',
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })

export const simulate = (scenario) => post(`/simulate/${scenario}`)
export const acknowledge = (id) => post(`/alerts/${id}/acknowledge`)
export const falsePositive = (id) => post(`/alerts/${id}/false-positive`)
export const getMode = () => get('/api/mode')
export const setMode = (mode) => post('/api/mode', { mode })
export const getFloodHistory = () => get('/api/layers/flood-history')
export const drillStatus = () => get('/api/drill/status')
export const startDrill = (scenario) => post(`/api/drill/start/${scenario}`)
export const stopDrill = () => post('/api/drill/stop')

// incident report PDF — fetched as a blob, not JSON
export const downloadReport = (id) =>
  withTimeout(`/api/alerts/${id}/report`).then((r) => {
    if (!r.ok) {
      return Promise.reject(new Error(`report unavailable (HTTP ${r.status})`))
    }
    return r.blob()
  })
export const awsStatus = () => get('/aws/status')

export const wsUrl = () => {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL
  if (typeof window !== 'undefined' && window.location.hostname.endsWith('.amplifyapp.com')) {
    return 'wss://d3snenpbe1z846.cloudfront.net/stream'
  }
  const proto = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = typeof window !== 'undefined' ? window.location.host : 'localhost:8000'
  return `${proto}://${host}/stream`
}

