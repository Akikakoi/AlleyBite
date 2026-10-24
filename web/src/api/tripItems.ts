import { request } from './request'

/** 行前清单条目（后端 /api/v1/trip-items 列表项，方向六批 3） */
export interface TripItem {
  restaurant_id: number
  name: string
  city: string | null
  area: string | null
  cuisine: string | null
  avg_price: number | null
  address: string | null
  status: 'planned' | 'visited'
  saved_at: string | null
  visited_at: string | null
}

/** 我的行前清单（勾店时间倒序，附店铺摘要与状态） */
export function listTripItems() {
  return request<TripItem[]>({
    method: 'GET',
    url: '/v1/trip-items',
  })
}
