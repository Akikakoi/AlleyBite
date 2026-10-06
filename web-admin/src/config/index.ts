export const API_BASE = import.meta.env.VITE_API_BASE || '/api'

/** 常规接口超时 */
export const REQUEST_TIMEOUT = 15000

/** 手动触发采集是同步长任务（文档 9.5），单独放宽到 5 分钟 */
export const CRAWL_TIMEOUT = 300000