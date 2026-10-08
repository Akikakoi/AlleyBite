import { request } from './request'

/** 注册/登录结果（后端 /api/v1/auth/*） */
export interface AuthResult {
  token: string
  expires_at: string
  username: string
}

export interface AuthPayload {
  username: string
  password: string
}

/** 注册（成功即视为登录，直接返回令牌） */
export function register(payload: AuthPayload) {
  return request<AuthResult>({
    method: 'POST',
    url: '/v1/auth/register',
    data: payload,
  })
}

/** 登录：口令换用户态令牌 */
export function login(payload: AuthPayload) {
  return request<AuthResult>({
    method: 'POST',
    url: '/v1/auth/login',
    data: payload,
  })
}

/** 当前登录用户 */
export function fetchMe() {
  return request<{ username: string }>({
    method: 'GET',
    url: '/v1/auth/me',
  })
}
