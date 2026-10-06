import { get } from './request'

import type { City } from '@/types'

export function getCities() {
  return get<City[]>('/v1/cities')
}

export function searchCities(q: string) {
  return get<City[]>('/v1/cities/search', { q })
}