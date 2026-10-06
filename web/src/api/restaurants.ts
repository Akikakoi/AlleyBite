import { get } from './request'

import type { RestaurantDetail, SourceRef } from '@/types'

export function getRestaurant(id: number | string) {
  return get<RestaurantDetail>(`/v1/restaurants/${id}`)
}

export function getRestaurantSources(id: number | string) {
  return get<SourceRef[]>(`/v1/restaurants/${id}/sources`)
}