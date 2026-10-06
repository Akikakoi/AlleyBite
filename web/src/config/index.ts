export const API_BASE = import.meta.env.VITE_API_BASE || '/api'

/** 文档 9.4：接口超时 8s，失败可重试 */
export const REQUEST_TIMEOUT = 8000