/**
 * Client connection settings for the Android remote-control PoC.
 *
 * Same-origin browser sessions (the current desktop/PWA flow) keep using the
 * HttpOnly cookie. A native/remote client can instead store a server URL and a
 * device Bearer token. The token is only used after an explicit pairing flow;
 * it is never sent to a different origin.
 */

const SERVER_KEY = 'qpet_server_url'
const TOKEN_KEY = 'qpet_device_token'
const DEVICE_ID_KEY = 'qpet_device_id'

export interface ConnectionSettings {
  /** Empty string = same origin (cookie auth, current browser flow). */
  server: string
  /** Empty string = no paired device token. */
  token: string
  deviceId: string
}

export interface PairLink {
  protocol: number
  server: string
  ticket: string
  expires?: number
}

export class ConnectionError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ConnectionError'
    this.status = status
  }
}

const trimSlash = (s: string) => s.replace(/\/+$/, '')

/**
 * Normalize a user-entered dashboard address.
 *
 * Accepts `192.168.1.9:8787`, `http://host:8787/`, or an exported HTTPS
 * reverse-proxy URL. Query strings and fragments are rejected because they
 * would be silently dropped by URL joining.
 */
export function normalizeServer(input: string): string {
  const raw = input.trim()
  if (!raw) return ''
  const withScheme = /^[a-z][a-z\d+.-]*:\/\//i.test(raw) ? raw : `http://${raw}`
  let u: URL
  try {
    u = new URL(withScheme)
  } catch {
    throw new Error('服务器地址格式不正确')
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    throw new Error('服务器地址只支持 http 或 https')
  }
  if (!u.hostname) throw new Error('服务器地址缺少主机名')
  if (u.username || u.password) throw new Error('服务器地址不能包含账号密码')
  if (u.search || u.hash) throw new Error('服务器地址不能包含查询参数或锚点')
  u.pathname = trimSlash(u.pathname)
  return trimSlash(u.toString())
}

/** Vite/native builds may set a default while leaving browser mode untouched. */
export function defaultServer(): string {
  const env = String(import.meta.env.VITE_QPET_SERVER ?? '').trim()
  return env ? normalizeServer(env) : ''
}

const safeGet = (key: string): string => {
  try {
    return localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

const safeSet = (key: string, value: string): void => {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch {
    /* private mode / embedded webview may deny localStorage */
  }
}

export function readConnection(): ConnectionSettings {
  return {
    server: safeGet(SERVER_KEY),
    token: safeGet(TOKEN_KEY),
    deviceId: safeGet(DEVICE_ID_KEY),
  }
}

export function saveServer(server: string): ConnectionSettings {
  const normalized = normalizeServer(server)
  safeSet(SERVER_KEY, normalized)
  return readConnection()
}

export function saveDeviceToken(token: string, deviceId = ''): void {
  safeSet(TOKEN_KEY, token.trim())
  safeSet(DEVICE_ID_KEY, deviceId.trim())
}

export function clearDeviceToken(): void {
  safeSet(TOKEN_KEY, '')
  safeSet(DEVICE_ID_KEY, '')
}

/** Empty base = same-origin; the browser then keeps normal cookie semantics. */
export function apiUrl(path: string, server = readConnection().server): string {
  const p = path.startsWith('/') ? path : `/${path}`
  return server ? `${trimSlash(server)}${p}` : p
}

/** Absolute asset URL (background images, icons) for remote mode. */
export function assetUrl(path: string): string {
  // Empty image sources are common in the UI's "unknown icon" branches; do
  // not turn them into a request to the dashboard root. Absolute data/blob
  // URLs are already complete and must not be prefixed either.
  if (!path || !path.startsWith('/') || path.startsWith('//')) return path
  return apiUrl(path)
}

/**
 * Parse the deep link emitted by `/api/client/pairing`.
 *
 * Canonical form: `qpet://pair?v=1&base=http...&ticket=...&expires=...`.
 * `?server=...&ticket=...` is accepted for hand testing.
 */
export function parsePairLink(input: string): PairLink {
  const raw = input.trim()
  if (!raw) throw new Error('请输入配对链接')
  let u: URL
  try {
    u = new URL(raw)
  } catch {
    throw new Error('配对链接格式不正确')
  }
  const server = u.searchParams.get('base') || u.searchParams.get('server') || ''
  const ticket = u.searchParams.get('ticket') || ''
  if (!server || !ticket) throw new Error('配对链接缺少服务器地址或配对码')
  const parsedProtocol = Number(u.searchParams.get('v') || '1')
  if (!Number.isFinite(parsedProtocol) || parsedProtocol !== 1) {
    throw new Error(`不支持的客户端协议版本：${u.searchParams.get('v') || '?'}`)
  }
  const rawExpires = u.searchParams.get('expires') || ''
  const expires = rawExpires ? Number(rawExpires) : undefined
  if (rawExpires && (!Number.isFinite(expires) || (expires as number) <= 0)) {
    throw new Error('配对链接的过期时间不正确')
  }
  return {
    protocol: parsedProtocol,
    server: normalizeServer(server),
    ticket,
    expires,
  }
}

/** A stable non-secret device identifier for UI and server-side audit. */
export function ensureDeviceId(): string {
  const current = safeGet(DEVICE_ID_KEY)
  if (current) return current
  const id = `client-${globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)}`
  safeSet(DEVICE_ID_KEY, id)
  return id
}
