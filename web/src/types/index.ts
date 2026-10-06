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
  exclude_reason: string | null
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
}

export interface RankFilters {
  cuisine?: string
  priceMin?: number
  priceMax?: number
  area?: string
}