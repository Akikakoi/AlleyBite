import { CRAWL_TIMEOUT } from '@/config'

import { get, patch, post } from './request'
import type {
  AliasResult,
  AuditItem,
  CrawlOverview,
  CrawlRunPayload,
  CrawlRunResult,
  FeedbackItem,
  FeedbackStatus,
  LoginResult,
  MeResult,
  MergeResult,
  RestaurantListResult,
  RestaurantStatusResult,
  ReviewActionResult,
  ReviewItem,
} from './types'

export function login(username: string, password: string) {
  return post<LoginResult>('/v1/admin/login', { username, password })
}

export function getMe() {
  return get<MeResult>('/v1/admin/me')
}

export function listReviews(limit = 100) {
  return get<ReviewItem[]>('/v1/admin/reviews', { limit })
}

export function confirmReview(id: number, restaurantId?: number | null) {
  const body = restaurantId ? { restaurant_id: restaurantId } : {}
  return post<ReviewActionResult>(`/v1/admin/reviews/${id}/confirm`, body)
}

export function rejectReview(id: number) {
  return post<ReviewActionResult>(`/v1/admin/reviews/${id}/reject`)
}

export interface RestaurantQuery {
  city?: string
  status?: string
  q?: string
  limit?: number
  offset?: number
}

export function listRestaurants(query: RestaurantQuery) {
  return get<RestaurantListResult>('/v1/admin/restaurants', {
    city: query.city || undefined,
    status: query.status || undefined,
    q: query.q || undefined,
    limit: query.limit ?? 20,
    offset: query.offset ?? 0,
  })
}

export function mergeRestaurant(id: number, targetId: number) {
  return post<MergeResult>(`/v1/admin/restaurants/${id}/merge`, {
    target_id: targetId,
  })
}

export function addAlias(id: number, alias: string) {
  return post<AliasResult>(`/v1/admin/restaurants/${id}/aliases`, { alias })
}

export function setRestaurantStatus(id: number, status: 'active' | 'blocked') {
  return post<RestaurantStatusResult>(`/v1/admin/restaurants/${id}/status`, {
    status,
  })
}

export function getCrawlOverview(limit = 20) {
  return get<CrawlOverview>('/v1/admin/crawl', { limit })
}

/** 同步长任务：单独使用 5 分钟超时 */
export function runCrawl(payload: CrawlRunPayload) {
  return post<CrawlRunResult>('/v1/admin/crawl/run', payload, {
    timeout: CRAWL_TIMEOUT,
  })
}

export function listFeedback(limit = 50, status?: string) {
  return get<FeedbackItem[]>('/v1/admin/feedback', {
    limit,
    status: status || undefined,
  })
}

export function updateFeedback(id: number, status: FeedbackStatus) {
  return patch<FeedbackItem>(`/v1/admin/feedback/${id}`, { status })
}

export function listAudit(limit = 50, action?: string) {
  return get<AuditItem[]>('/v1/admin/audit', {
    limit,
    action: action || undefined,
  })
}