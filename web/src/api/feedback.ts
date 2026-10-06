import { request } from './request'

import type { FeedbackPayload, FeedbackResult } from '@/types'

/** 纠错/举报提交（文档 9.3）：免登录，后端按 IP 限流 */
export function submitFeedback(payload: FeedbackPayload) {
  return request<FeedbackResult>({
    method: 'POST',
    url: '/v1/feedback',
    data: payload,
  })
}