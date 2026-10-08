import { request } from './request'

/** 收藏条目（后端 /api/v1/favorites 列表项） */
export interface FavoriteItem {
  restaurant_id: number
  name: string
  city: string | null
  area: string | null
  cuisine: string | null
  avg_price: number | null
  address: string | null
  mention_count: number
  last_mentioned_at: string | null
  favorited_at: string | null
}

/** 我的收藏列表（按收藏时间倒序） */
export function listFavorites() {
  return request<FavoriteItem[]>({
    method: 'GET',
    url: '/v1/favorites',
  })
}

/** 收藏店铺；重复收藏幂等（created=false） */
export function addFavorite(restaurantId: number) {
  return request<{ restaurant_id: number; favorited: boolean; created: boolean }>({
    method: 'POST',
    url: '/v1/favorites',
    data: { restaurant_id: restaurantId },
  })
}

/** 取消收藏；未收藏时幂等 */
export function removeFavorite(restaurantId: number) {
  return request<{ restaurant_id: number; favorited: boolean }>({
    method: 'DELETE',
    url: `/v1/favorites/${restaurantId}`,
  })
}

/** 详情页收藏态查询 */
export function fetchFavoriteStatus(restaurantId: number) {
  return request<{ restaurant_id: number; favorited: boolean }>({
    method: 'GET',
    url: `/v1/favorites/${restaurantId}`,
  })
}
