export interface LoginResult {
  token: string
  expires_at: string
  username: string
  role: string
}

export interface MeResult {
  username: string
  role: string
}

export interface ReviewItem {
  id: number
  mention_id: number
  shop_name_raw: string
  address_text: string | null
  evidence_span: string | null
  score: number
  reason: string | null
  candidate_restaurant_id: number | null
  candidate_name: string | null
  city: string | null
}

export interface ReviewActionResult {
  id: number
  status: string
}

export interface RestaurantItem {
  id: number
  name: string
  city: string | null
  address: string | null
  area: string | null
  cuisine: string | null
  avg_price: number | null
  status: string
  merged_into: number | null
  mention_count: number
  alias_count: number
}

export interface RestaurantListResult {
  items: RestaurantItem[]
  total: number
}

export interface RestaurantStatusResult {
  id: number
  status: string
}

export interface MergeResult {
  source_id: number
  target_id: number
  status: string
  mentions_moved: number
}

export interface AliasCreatedResult {
  created: true
  id: number
  alias: string
}

export interface AliasExistsResult {
  created: false
  restaurant_id: number
}

export type AliasResult = AliasCreatedResult | AliasExistsResult

export interface CrawlJob {
  id: number
  job_type: string
  city: string | null
  status: string
  started_at: string | null
  finished_at: string | null
  stats: Record<string, unknown> | null
  error: string | null
}

export interface CrawlBreaker {
  tripped: string[]
  sources: Record<string, unknown>
  totals: Record<string, unknown>
  last_job_id: number | null
}

export interface CrawlOverview {
  jobs: CrawlJob[]
  breaker: CrawlBreaker
}

export interface CrawlRunResult {
  job_id: number
  job_type: string
  status: string
  stats: Record<string, unknown> | null
  error: string | null
}

export interface CrawlRunPayload {
  city?: string
  sources?: string[]
  mode: 'incremental' | 'full'
  force?: boolean
}

export type FeedbackStatus = 'pending' | 'processing' | 'resolved' | 'rejected'

export interface FeedbackItem {
  id: number
  restaurant_id: number | null
  type: string
  content: string
  contact: string | null
  status: string
  created_at: string
}

export interface AuditItem {
  id: number
  operator: string
  action: string
  target_type: string
  target_id: string | number | null
  before: unknown
  after: unknown
  created_at: string
}
// --- 数据看板（文档 9.5 数据看板）-------------------------------------------

export interface AdminStatsOverview {
  cities: number
  restaurants_total: number
  restaurants_active: number
  mentions_total: number
  raw_total: number
}

export interface AdminStatsExtract {
  extracted: number
  failed: number
  failure_rate: number
  avg_confidence: number
  address_coverage: number
}

export interface AdminStatsJobsSummary {
  success: number
  failed: number
  success_rate: number
}

export interface AdminStatsDayPoint {
  date: string
  count?: number
  success?: number
  failed?: number
}

export interface AdminStatsCityRow {
  city: string
  active: number
  total: number
}

export interface AdminStats {
  overview: AdminStatsOverview
  raw_status: { status: string; count: number }[]
  extract: AdminStatsExtract
  jobs_summary: AdminStatsJobsSummary
  jobs_14d: AdminStatsDayPoint[]
  mentions_14d: AdminStatsDayPoint[]
  city_restaurants: AdminStatsCityRow[]
  generated_at: string
  window_days: number
}

// --- 账号管理（文档 9.5 RBAC）-----------------------------------------------

export interface AdminUserItem {
  id: number
  username: string
  role: string
  is_active: boolean
  last_login_at: string | null
}

export interface AdminUserUpsertPayload {
  username: string
  password: string
  role: 'superadmin' | 'operator' | 'reviewer'
}
