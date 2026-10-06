import axios, { type AxiosError, type AxiosRequestConfig } from 'axios'

import { API_BASE, REQUEST_TIMEOUT } from '@/config'

export class ApiError extends Error {
  status: number
  code: number

  constructor(message: string, status = 0, code = -1) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

const http = axios.create({
  baseURL: API_BASE,
  timeout: REQUEST_TIMEOUT,
})

/** 统一解包 {code,message,data}；失败一律抛 ApiError（含 HTTP 状态） */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await http.request<ApiEnvelope<T>>(config)
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code !== 0) {
        throw new ApiError(body.message || '请求失败', response.status, body.code)
      }
      return body.data
    }
    return body as unknown as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    const err = error as AxiosError<{ detail?: string }>
    const status = err.response?.status ?? 0
    const detail = err.response?.data?.detail
    const message =
      detail || (status === 0 ? '网络异常，请稍后重试' : err.message || '请求失败')
    throw new ApiError(message, status, -1)
  }
}

export function get<T>(
  url: string,
  params?: Record<string, unknown>,
): Promise<T> {
  return request<T>({ method: 'GET', url, params })
}

/** 榜单页把 404 视为「城市正在收录」（文档 8.3 的 2001 语义） */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}