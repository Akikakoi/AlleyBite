import { request } from './request'

/** 打卡条目（后端 /api/v1/ugc） */
export interface UgcItem {
  id: number
  restaurant_id: number
  restaurant_name: string | null
  username: string
  content: string
  images: string[]
  status: string
  created_at: string | null
}

/** 店铺打卡列表（仅审核通过，免登录可看） */
export function listRestaurantUgc(restaurantId: number, limit = 20) {
  return request<UgcItem[]>({
    method: 'GET',
    url: '/v1/ugc',
    params: { restaurant_id: restaurantId, limit },
  })
}

/** 发布打卡（需登录，先审后显） */
export function createUgc(payload: {
  restaurant_id: number
  content: string
  images?: string[]
}) {
  return request<{ id: number; status: string }>({
    method: 'POST',
    url: '/v1/ugc',
    data: payload,
  })
}

/** 我的打卡（含待审/被拒） */
export function listMyUgc() {
  return request<UgcItem[]>({
    method: 'GET',
    url: '/v1/ugc/mine',
  })
}

/** 上传打卡图片：直接 POST 二进制，Content-Type 即图片类型 */
export function uploadImage(file: File) {
  return request<{ path: string }>({
    method: 'POST',
    url: '/v1/uploads',
    data: file,
    headers: { 'Content-Type': file.type },
    timeout: 30000,
  })
}
