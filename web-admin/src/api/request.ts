import axios, {
  type AxiosError,
  type AxiosRequestConfig,
  type AxiosResponse,
} from 'axios'

import { API_BASE, REQUEST_TIMEOUT } from '@/config'

export const TOKEN_KEY = 'alleybite.admin.token'

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

/** 所有 /api/v1/admin/* 请求带 Bearer 令牌 */
http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

/** 401 清除本地令牌并跳转登录页 */
http.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

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
  config?: AxiosRequestConfig,
): Promise<T> {
  return request<T>({ ...config, method: 'GET', url, params })
}

export function post<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return request<T>({ ...config, method: 'POST', url, data })
}

export function patch<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return request<T>({ ...config, method: 'PATCH', url, data })
}

/** 统一的错误信息提取，供页面 catch 使用 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return '操作失败'
}