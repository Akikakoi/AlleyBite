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

/** 发送短信验证码；mock 模式（后端未配短信通道）返回 dev_code 供联调 */
export function sendSmsCode(phone: string) {
  return request<{ mock: boolean; ttl_minutes: number; dev_code?: string }>({
    method: 'POST',
    url: '/v1/auth/sms/send',
    data: { phone },
  })
}

/** 验证码登录：无账号自动注册 */
export function smsLogin(payload: { phone: string; code: string }) {
  return request<AuthResult & { created: boolean }>({
    method: 'POST',
    url: '/v1/auth/sms/login',
    data: payload,
  })
}
