import { request } from './request'

/** 推荐条目（V2.0 个性化推荐） */
export interface RecommendItem {
  restaurant_id: number
  name: string | null
  city: string | null
  cuisine: string | null
  score: number | null
  mention_count: number
}

export interface RecommendData {
  strategy: 'taste' | 'hot'
  items: RecommendItem[]
}

/** 猜你想吃：登录按口味召回，未登录回退全城热门 */
export function getRecommend(limit = 6) {
  return request<RecommendData>({
    method: 'GET',
    url: '/v1/recommend',
    params: { limit },
  })
}

/** 上报详情页浏览事件（未登录也记录） */
export function recordView(restaurantId: number) {
  return request<{ recorded: boolean }>({
    method: 'POST',
    url: '/v1/views',
    data: { restaurant_id: restaurantId },
  })
}
