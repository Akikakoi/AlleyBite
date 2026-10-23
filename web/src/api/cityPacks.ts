import { request } from './request'

/** 收藏包条目（后端 /api/v1/city-packs 列表项，方向六批 2 订阅制） */
export interface CityPackItem {
  city: string
  rank_count: number
  maturity: string
  generated_at: string | null
  packed_at: string | null
}

/** 我的收藏包列表（订阅时间倒序，附最新快照摘要） */
export function listCityPacks() {
  return request<CityPackItem[]>({
    method: 'GET',
    url: '/v1/city-packs',
  })
}

/** 收藏整城；重复订阅幂等（created=false）；新建订阅由服务端落 pack_save 埋点 */
export function subscribeCityPack(city: string, sessionKey?: string) {
  return request<{ city: string; packed: boolean; created: boolean }>({
    method: 'POST',
    url: '/v1/city-packs',
    data: { city, session_key: sessionKey },
  })
}

/** 退订收藏包；未订阅时幂等 */
export function unsubscribeCityPack(city: string) {
  return request<{ city: string; packed: boolean }>({
    method: 'DELETE',
    url: `/v1/city-packs/${encodeURIComponent(city)}`,
  })
}

/** 榜单页收藏按钮态查询 */
export function fetchCityPackStatus(city: string) {
  return request<{ city: string; packed: boolean }>({
    method: 'GET',
    url: `/v1/city-packs/${encodeURIComponent(city)}`,
  })
}
