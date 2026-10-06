import { get } from './request'

import type { RankData, RankQuery } from '@/types'

export function getRank(query: RankQuery) {
  const {
    city,
    page = 1,
    pageSize = 20,
    cuisine,
    priceMin,
    priceMax,
    area,
  } = query
  return get<RankData>('/v1/rank', {
    city,
    page,
    page_size: pageSize,
    cuisine,
    price_min: priceMin,
    price_max: priceMax,
    area,
  })
}