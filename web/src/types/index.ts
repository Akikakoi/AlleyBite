/** 与后端接口响应结构对齐（文档 7.3 / 8.1） */

export interface City {
  name: string
  code: string | null
  status: string
  has_rank: boolean
}

export interface SourceRef {
  source: string
  source_url: string | null
  title: string | null
  excerpt: string | null
  published_at: string | null
}

export interface RankItem {
  rank: number
  restaurant_id: number
  name: string
  area: string | null
  address: string | null
  location: { lat: number; lng: number } | null
  cuisine: string | null
  avg_price: number | null
  score: number
  /** 城市内分位显示分（1.0–4.9，5 分制，与榜单页口径一致）；无快照时可能缺失 */
  display_score?: number
  praise_keywords: string[]
  complaints: string[]
  recommended_dishes: string[]
  mention_count: number
  last_mentioned_at: string | null
  sources: SourceRef[]
}

export interface RankData {
  city: string
  generated_at: string | null
  algorithm_ver: string
  total: number
  page: number
  page_size: number
  items: RankItem[]
}

export interface RestaurantDetail {
  restaurant_id: number
  name: string
  area: string | null
  address: string | null
  location: { lat: number; lng: number } | null
  cuisine: string | null
  avg_price: number | null
  status: string
  score: number
  /** 城市内分位显示分（有榜单快照时后端直接给出） */
  display_score?: number
  exclude_reason: string | null
  /** 「为什么上榜」人话理由（公开报道信号自动生成，被剔除店为空） */
  reasons: string[]
  /** 大众点评搜索直达链接（城市未收录或店名为空时缺失） */
  review_url?: string | null
  mention_count: number
  last_mentioned_at: string | null
  praise_keywords: string[]
  complaints: string[]
  recommended_dishes: string[]
  sources: SourceRef[]
}

export interface RankQuery {
  city: string
  page?: number
  pageSize?: number
  cuisine?: string
  priceMin?: number
  priceMax?: number
  area?: string
  /** 时间维度（文档 2.2 V1.1）：仅看近 N 天内被提及的店，如 90 */
  days?: number
}

export interface RankFilters {
  cuisine?: string
  priceMin?: number
  priceMax?: number
  area?: string
  /** 时间维度：仅看近 N 天内被提及的店（90 = 近 90 天），不传 = 全部 */
  days?: number
}

/** 纠错/举报（文档 9.3 详情页纠错入口） */
export type FeedbackType = 'info' | 'closed' | 'label' | 'other'

export interface FeedbackPayload {
  restaurant_id?: number
  type: FeedbackType
  content: string
  contact?: string
}

export interface FeedbackResult {
  id: number
  status: string
}