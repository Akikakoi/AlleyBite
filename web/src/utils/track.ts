/**
 * 行为埋点（方向六 榜单可带走 批 1）：北极星「榜单带走率」数据源。
 *
 * fire-and-forget：埋点失败静默忽略，绝不影响页面主流程；
 * session_key 为 localStorage 持久化的匿名随机 id，服务端按日去重算 UV。
 * 动作白名单在后端 track_service.TRACK_ACTIONS，未知动作会被 422 拒收。
 */

import { API_BASE } from '@/config'
import { getToken } from './token'

const SID_KEY = 'alleybite.track_sid'

export interface TrackPayload {
  city?: string
  restaurantId?: number
}

export function getSessionKey(): string {
  let sid = localStorage.getItem(SID_KEY)
  if (!sid) {
    sid =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
    localStorage.setItem(SID_KEY, sid)
  }
  return sid
}

export function track(action: string, payload?: TrackPayload): void {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  try {
    void fetch(`${API_BASE}/v1/track`, {
      method: 'POST',
      keepalive: true,
      headers,
      body: JSON.stringify({
        action,
        session_key: getSessionKey(),
        city: payload?.city,
        restaurant_id: payload?.restaurantId,
      }),
    }).catch(() => {})
  } catch {
    // 埋点是尽力而为的旁路，任何异常都不外抛
  }
}
